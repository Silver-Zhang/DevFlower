"""Actual OS PTYs with a synthetic CLI; real Codex/Claude evidence is separate."""
import json
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).parent), str(Path(__file__).resolve().parents[1] / 'tools')]
from test_workbench_runtime import fixture, structured_session
from studio.native_terminals import NativeTerminals, dimensions
from studio.terminal_text import TerminalText
from studio.workbench_sessions import WorkbenchSessions
from studio.graph import Invalid
from studio.server import Conflict
from orchestration.common import Blocked, digest


class TerminalTextTests(unittest.TestCase):
    def test_split_credential_and_color_escapes_never_escape_redaction(self):
        reader = TerminalText(['abcdef'])
        self.assertEqual(reader.feed(b'hello abc'), 'hello ')
        self.assertEqual(reader.feed(b'\x1b[31mdef\x1b[0m done'), '[REDACTED]\x1b[0m done')

    def test_every_split_point_is_safe(self):
        for split in range(1, 12):
            value = b'opaque-key12'
            reader = TerminalText([value.decode()])
            result = reader.feed(value[:split]) + reader.feed(value[split:]) + reader.feed(b'', True)
            self.assertEqual(result, '[REDACTED]')

    def test_osc_clipboard_and_links_are_not_forwarded(self):
        reader = TerminalText()
        self.assertEqual(reader.feed(b'a\x1b]52;c;'), 'a')
        self.assertEqual(reader.feed(b'c2VjcmV0\x07b'), 'b')
        self.assertEqual(reader.feed(b'\x1b]8;;https://example.invalid\x1b\\link\x1b]8;;\x1b\\'), 'link')

    def test_terminal_csi_and_utf8_survive_chunk_boundaries(self):
        reader = TerminalText()
        self.assertEqual(reader.feed(b'\x1b[3'), '')
        self.assertEqual(reader.feed(b'1mhello\x1b[0m world'), '\x1b[31mhello\x1b[0m world')
        value = '你好'.encode()
        self.assertEqual(reader.feed(value[:2]) + reader.feed(value[2:]), '你好')

    def test_terminal_dimensions_are_typed_and_bounded(self):
        for cols, rows in [(True, 25), (80, False), (1000, 25), (80, 0)]:
            with self.assertRaises(Invalid): dimensions(cols, rows)
        self.assertEqual(dimensions(80, 24), (80, 24))

    def test_eof_does_not_flush_a_partial_credential(self):
        reader = TerminalText(['opaque-credential'])
        self.assertEqual(reader.feed(b'visible opaque-'), 'visible ')
        self.assertEqual(reader.feed(b'', True), '')


class NativeTerminalProtocolTests(unittest.TestCase):
    def setUp(self):
        self.base, self.rt = fixture(self, admission=True)
        self.workspace = self.base.project['id']
        _, self.session = structured_session(self.base, self.rt, 'pty-protocol', 'codex')
        self.sessions = WorkbenchSessions(self.rt)
        self.manager = NativeTerminals(self.rt, self.sessions)
        self.addCleanup(self.manager.shutdown)

    def prepare(self):
        return self.manager.prepare(dict(workspace=self.workspace, handle=self.session['handle']))

    def launch(self):
        import subprocess
        real = subprocess.Popen
        # Test-only replacement of the fixed native child. No API or production
        # configuration accepts this executable/payload override.
        home = self.rt.db.get('session-handle', self.session['handle'])['profile']['home']
        lock = Path(home).parent / (digest(str(Path(home).resolve()).encode()) + '.session.lock')
        code = "import os,tty,fcntl; f=open(" + repr(str(lock)) + ", 'a+');fcntl.flock(f,fcntl.LOCK_EX);tty.setraw(0);os.write(1,b'READY\\r\\n');\nwhile True:\n b=os.read(0,1024)\n if not b or b==b'\\x04':break\n os.write(1,b'Got:'+b)\n"
        calls=[]
        def spawn(argv, **kwargs):
            self.assertTrue(argv[2].endswith('/tools/devflower_native_terminal.py'))
            self.assertIn('--handle', argv);self.assertNotIn('shell', kwargs)
            calls.append(argv)
            return real([sys.executable, '-B', '-c', code], **kwargs)
        preview=self.prepare()
        data=dict(id=preview['id'],sha256=preview['sha256'],answer='Synthetic protocol test; no actual provider',cols=80,rows=24)
        with patch('studio.native_terminals.subprocess.Popen',side_effect=spawn):
            row=self.manager.start(data)
            replay=self.manager.start(data)
        self.assertEqual(row['id'],replay['id']);self.assertEqual(len(calls),1)
        self.key=row['id'];self.addCleanup(lambda:self.manager.close(self.workspace,self.key))
        self.wait(lambda:'READY' in self.manager.snapshot(self.workspace,self.key)['output'])
        return row

    def wait(self,predicate):
        for _ in range(80):
            if predicate():return
            time.sleep(.025)
        self.fail('OS PTY observation deadline')

    def test_prepare_rejects_browser_commands_and_foreign_workspace(self):
        with self.assertRaises(Invalid):self.manager.prepare(dict(workspace=self.workspace,handle=self.session['handle'],argv=['sh']))
        with self.assertRaises((Invalid,Blocked)):self.manager.prepare(dict(workspace='foreign',handle=self.session['handle']))

    def test_real_pty_input_sequence_replay_and_native_lock(self):
        self.launch()
        self.manager.input(self.workspace,self.key,1,'hello\n')
        self.assertTrue(self.manager.input(self.workspace,self.key,1,'hello\n')['duplicate'])
        with self.assertRaises(Conflict):self.manager.input(self.workspace,self.key,1,'other\n')
        with self.assertRaises(Conflict):self.manager.input(self.workspace,self.key,3,'gap\n')
        self.wait(lambda:'Got:hello' in self.manager.snapshot(self.workspace,self.key)['output'])
        self.assertEqual(self.manager.snapshot(self.workspace,self.key)['output'].count('Got:hello'),1)
        with self.assertRaises(Blocked):self.prepare()
        self.manager.resize(self.workspace,self.key,90,25)
        with self.assertRaises(Blocked):self.manager.snapshot('foreign',self.key)

    def test_reopened_service_cannot_blindly_relaunch_or_write_a_live_pty(self):
        self.launch();other=NativeTerminals(self.rt,self.sessions);self.addCleanup(other.shutdown)
        row=other.snapshot(self.workspace,self.key)
        self.assertEqual(row['state'],'RECOVERY_REQUIRED');self.assertTrue(row['process_alive'])
        with self.assertRaises(Blocked):other.input(self.workspace,self.key,1,'repeat\n')

    def test_partial_write_stays_unknown_after_the_reader_exits(self):
        self.launch()
        with patch('studio.native_terminals.os.write',return_value=1):
            with self.assertRaises(Blocked):self.manager.input(self.workspace,self.key,1,'ambiguous\n')
        self.manager.entries[self.key]['thread'].join(timeout=5)
        row=self.manager.snapshot(self.workspace,self.key)
        self.assertEqual(row['state'],'RECOVERY_REQUIRED')
        self.assertEqual(row['input_status'],'UNKNOWN')
        self.assertEqual(row['input_sequence'],1)
        with self.assertRaises(Blocked):self.manager.input(self.workspace,self.key,1,'ambiguous\n')

    def test_lost_persisted_receipt_never_replays_a_written_input(self):
        self.launch();change=self.rt.flow.change
        def lose_receipt(kind,key,value):
            if value.get('input_status')=='SENT':raise sqlite3.OperationalError('test injected lost receipt')
            return change(kind,key,value)
        with patch.object(self.rt.flow,'change',side_effect=lose_receipt):
            with self.assertRaises(Blocked):self.manager.input(self.workspace,self.key,1,'written-once\n')
        self.manager.entries[self.key]['thread'].join(timeout=5)
        row=self.manager.snapshot(self.workspace,self.key)
        self.assertEqual(row['state'],'RECOVERY_REQUIRED')
        self.assertEqual(row['input_status'],'UNKNOWN')
        with self.assertRaises(Blocked):self.manager.input(self.workspace,self.key,1,'written-once\n')

    def test_stop_preserves_history_and_releases_lease(self):
        self.launch();path=self.rt.db.get('session-handle',self.session['handle'])['native']['path'];before=Path(path).read_bytes()
        self.manager.close(self.workspace,self.key)
        self.wait(lambda:self.manager.snapshot(self.workspace,self.key)['state']=='CANCELLED')
        self.assertFalse(self.manager.snapshot(self.workspace,self.key)['process_alive'])
        self.assertEqual(Path(path).read_bytes(),before)
        self.assertFalse(self.sessions.busy({'home':str(Path(path).parents[1])},self.session['session_id']))


if __name__=='__main__':unittest.main()

class NativeTerminalOuterIsolationTests(unittest.TestCase):
    def test_native_codex_uses_probed_external_mode_and_claude_retains_read_tools(self):
        from devflower_native_terminal import native_cli_argv
        common=dict(executable='/SYNTHETIC/native',model='opaque-model',effort='high')
        args=native_cli_argv(dict(common,provider='codex'),'SYNTHETIC-UUID')
        self.assertEqual(args[args.index('--sandbox')+1],'danger-full-access')
        self.assertEqual(args[args.index('--ask-for-approval')+1],'never')
        args=native_cli_argv(dict(common,provider='claude'),'SYNTHETIC-UUID')
        self.assertEqual(args[args.index('--permission-mode')+1],'plan')
        self.assertEqual(args[args.index('--tools')+1],'Read,Glob,Grep')

    def test_outer_wrapper_actually_denies_source_authority_and_credential_writes(self):
        from devflower_native_terminal import wrap_native_cli
        from orchestration.common import clean_env
        import tempfile,subprocess,json,sys
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);source=root/'source';home=root/'native';scratch=root/'scratch'
            for p in [source,home,scratch]:p.mkdir()
            (source/'README.md').write_text('SYNTHETIC readable source\n')
            authority=root/'private-authority';authority.write_text('SYNTHETIC AUTHORITY')
            credential=root/'private-credential';credential.write_text('SYNTHETIC CREDENTIAL')
            binding=dict(read_paths=[],readonly_files=[(str(credential),'.codex/auth.json')])
            checks=[source/'unexpected-source-write',authority,home/'.codex/auth.json']
            script='''import pathlib,json,sys
source,home,scratch=map(pathlib.Path,sys.argv[1:4]);targets=list(map(pathlib.Path,sys.argv[4:]));denied=[]
for p in targets:
 try:p.write_text('UNEXPECTED')
 except OSError:denied.append(True)
 else:denied.append(False)
(scratch/'allowed-private-scratch').write_text('ok')
print(json.dumps(dict(read=(source/'README.md').read_text(),denied=denied,scratch=True)))
'''
            argv=wrap_native_cli(binding,str(source),home,str(scratch),[sys.executable,'-B','-c',script,str(source),str(home),str(scratch),*[str(p) for p in checks]])
            env=clean_env(home);env['TMPDIR']=str(scratch)
            result=subprocess.run(argv,capture_output=True,text=True,env=env,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            proof=json.loads(result.stdout);self.assertEqual(proof['read'],'SYNTHETIC readable source\n');self.assertEqual(proof['denied'],[True,True,True]);self.assertTrue(proof['scratch'])
            self.assertFalse(checks[0].exists());self.assertEqual(authority.read_text(),'SYNTHETIC AUTHORITY');self.assertEqual(credential.read_text(),'SYNTHETIC CREDENTIAL')

    def test_unknown_native_provider_is_never_a_shell_fallback(self):
        from devflower_native_terminal import native_cli_argv
        from orchestration.common import Blocked
        with self.assertRaises(Blocked):native_cli_argv(dict(provider='unsupported'),'SYNTHETIC')
