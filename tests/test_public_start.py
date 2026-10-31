"""Public startup with temporary HOME, synthetic CLIs and real HTTP/runtime.

The fake executables accept metadata probes only. These tests certify no actual
Provider, native account, model inference, release approval or deployment.
"""
import contextlib
import fcntl
import http.client
import io
import json
import multiprocessing
import os
from pathlib import Path
import shutil
import signal
import stat
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
import devflower_start as start
from orchestration.common import tree_hash
from studio import session_runtime, workbench_runtime, workbench_server
from studio.native_codex_rpc import metadata_guard
from studio.public_source_tool import PUBLIC_FILES, PublicSourceReader

REPO = Path(__file__).resolve().parents[1]


def quiet_start(args, root, bwrap):
    # A fresh interpreter matches the public CLI. fork would inherit a previous
    # Controller test's irreversible non-dumpable flag, unlike a real exec.
    with contextlib.ExitStack() as stack:
        for item in (patch.object(start, 'ROOT', root), patch.object(start, 'BWRAP', bwrap),
                     patch.object(workbench_runtime, 'ROOT', root), patch.object(session_runtime, 'ROOT', root),
                     patch.object(workbench_server, 'STATIC', root/'tools/studio/static'),
                     patch('orchestration.sandbox.abi', return_value=6)):
            stack.enter_context(item)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            raise SystemExit(start.main(args))


class PublicStartTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='devflower-public-start-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base/'source'
        self.root.mkdir()
        # Copy only repository public source, never local .git/.codex/.aws or
        # private session artifacts from the development checkout.
        for name in ('tools', 'docs', 'tests', 'templates', 'examples', '.github'):
            shutil.copytree(REPO/name, self.root/name, ignore=shutil.ignore_patterns('__pycache__'))
        for name in ('README.md', 'AGENTS.md', 'CLAUDE.md', 'WORKFLOW.md', '.gitignore'):
            shutil.copyfile(REPO/name, self.root/name)
        self.home = self.base/'home'
        self.home.mkdir()
        for name in ('.codex', '.claude'): (self.home/name).mkdir()
        (self.home/'.codex/config.toml').write_text('model="SYNTHETIC-codex-choice"\nmodel_reasoning_effort="synthetic-effort"\n')
        (self.home/'.claude/settings.json').write_text(json.dumps(dict(model='SYNTHETIC-claude-choice', effortLevel='synthetic-effort', env={'ANTHROPIC_AUTH_TOKEN': 'SYNTHETIC_DO_NOT_COPY'})))
        (self.home/'.codex/auth.json').write_text('SYNTHETIC_NATIVE_AUTH_DO_NOT_READ')
        (self.home/'.claude/.credentials.json').write_text('SYNTHETIC_NATIVE_AUTH_DO_NOT_READ')
        (self.home/'.claude.json').write_text('SYNTHETIC_NATIVE_GLOBAL_DO_NOT_CHANGE')
        self.native_before = self.native_bytes()
        self.bin = self.base/'bin'
        self.bin.mkdir()
        self.log = self.base/'probe-calls.jsonl'
        for name in ('git', 'bwrap', 'codex', 'claude'):
            path = self.bin/name
            path.write_text('#!'+sys.executable+'\n'+
                'import json,sys\nfrom pathlib import Path\n'+
                'name=Path(sys.argv[0]).name;args=sys.argv[1:]\n'+
                'with open('+repr(str(self.log))+',"a") as f:f.write(json.dumps([name,args])+"\\n")\n'+
                'if args==["--version"]:print("SYNTHETIC 1.2.3")\n'+
                'elif name=="codex" and args==["exec","--help"]:print("--json --model resume")\n'+
                'elif name=="codex" and args==["features","list"]:print("multi_agent unstable false")\n'+
                'elif name=="claude" and args==["--help"]:print("--output-format --model --effort --setting-sources")\n'+
                'elif name=="bwrap" and args[-2:]==["--","/bin/true"]:pass\n'+
                'else:sys.exit(97) # No auth, model-list, app-server, prompt or inference allowed.\n')
            path.chmod(0o700)
        self.state = self.base/'state'
        self.environment = dict(HOME=str(self.home), PATH=str(self.bin), XDG_STATE_HOME=str(self.base/'xdg'))
        patches = [patch.dict(os.environ, self.environment, clear=True), patch.object(start, 'ROOT', self.root),
                   patch.object(start, 'BWRAP', self.bin/'bwrap'), patch.object(workbench_runtime, 'ROOT', self.root),
                   patch.object(session_runtime, 'ROOT', self.root),
                   patch.object(workbench_server, 'STATIC', self.root/'tools/studio/static'),
                   patch('orchestration.sandbox.abi', return_value=6)]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        self.before_source = tree_hash(self.root)

    def tearDown(self):
        self.assertEqual(self.native_bytes(), self.native_before, 'original native bytes must remain unchanged')
        if self.log.exists():
            for name, args in map(json.loads, self.log.read_text().splitlines()):
                allowed = args == ['--version'] or name == 'codex' and args in (['exec', '--help'], ['features', 'list']) or name == 'claude' and args == ['--help'] or name == 'bwrap' and args[-2:] == ['--', '/bin/true']
                self.assertTrue(allowed, (name, args))

    def native_bytes(self):
        return {str(p.relative_to(self.home)):p.read_bytes() for p in self.home.rglob('*') if p.is_file()}

    def call(self, *extra):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = start.main(['--state-dir', str(self.state), *extra])
        return code, out.getvalue(), err.getvalue()

    def running(self):
        process = multiprocessing.get_context('spawn').Process(target=quiet_start,
            args=(['--state-dir', str(self.state), '--no-browser'], self.root, self.bin/'bwrap'))
        process.start()
        def stop():
            if process.is_alive():
                os.kill(process.pid, signal.SIGINT)
                process.join(5)
            if process.is_alive(): process.terminate();process.join(5)
            process.close()
        self.addCleanup(stop)
        deadline = time.monotonic()+15
        receipt = self.state/'runtime/service.json'
        while time.monotonic() < deadline:
            if receipt.is_file():
                self.receipt = json.loads(receipt.read_text())
                self.token = (self.state/'runtime/workbench-capability').read_text().strip()
                return process
            if not process.is_alive():
                self.fail('controlled launcher exited before serving: '+str(process.exitcode))
            time.sleep(.02)
        self.fail('controlled launcher did not become ready')

    def http(self, path, auth=True, host=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.receipt['port'], timeout=4)
        headers = {'Authorization':'Bearer '+self.token} if auth else {}
        if host: headers['Host'] = host
        try:
            connection.request('GET', path, headers=headers)
            response = connection.getresponse()
            return response.status, response.read()
        finally: connection.close()

    def test_check_does_not_create_state_or_start_service_or_infer(self):
        with patch.object(start.webbrowser, 'open') as browser:
            code, out, err = self.call('--check')
        self.assertEqual(code, 0, err)
        self.assertIn('认证与实际推理均未验证', out)
        self.assertFalse(self.state.exists())
        self.assertEqual(tree_hash(self.root), self.before_source)
        browser.assert_not_called()

    def test_xdg_default_check_creates_no_directories(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(start.main(['--check']), 0)
        args = start.parser().parse_args([])
        self.assertEqual(start.location(args, self.home), self.base/'xdg/devflower/workbench')
        self.assertFalse((self.base/'xdg').exists())

    def test_real_runtime_http_and_prebuilt_assets_without_node_or_native_changes(self):
        process = self.running()
        code, raw = self.http('/api/status')
        self.assertEqual(code, 200)
        status = json.loads(raw)
        self.assertEqual(status['release']['channel'], 'Development')
        self.assertEqual(status['candidate'], self.before_source)
        self.assertEqual(status['workspaces'], [])
        self.assertEqual({p['provider'] for p in status['profiles']}, {'codex','claude','copilot'})
        copilot = next(p for p in status['profiles'] if p['provider']=='copilot')
        self.assertFalse(copilot['available'])
        for profile in status['profiles']:
            if profile['provider']!='copilot':
                self.assertFalse(profile['inference_verified'])
                self.assertIsNone(profile['authentication']['logged_in'])
                self.assertEqual(profile['execution_backend'], 'native-local')
        for url, name in (('/', 'workbench.html'), ('/workbench.js','workbench.js'), ('/workbench.css','workbench.css')):
            code, raw = self.http(url, auth=False)
            self.assertEqual(code, 200)
            self.assertEqual(raw, (self.root/'tools/studio/static'/name).read_bytes())
        self.assertEqual(self.http('/api/status',auth=False)[0],403)
        op = json.loads((self.state/'config/operator.json').read_text())
        self.assertEqual(len(op['profiles']),2)
        self.assertEqual(op['allowed_roots'],[str(self.state/'projects')])
        self.assertEqual(list((self.state/'projects').iterdir()),[])
        self.assertEqual(op['native_local']['codex']['home'],str(self.home))
        self.assertEqual(op['native_local']['claude']['config_root'],str(self.home/'.claude'))
        self.assertEqual({p['model'] for p in op['profiles'].values()}, {'SYNTHETIC-codex-choice','SYNTHETIC-claude-choice'})
        for profile in op['profiles'].values():
            self.assertEqual(profile['readonly_files'],[] if profile['provider']=='codex' else [[str(self.home/'.claude/settings.json'),'.claude/settings.json']])
            self.assertEqual(profile['read_paths'],[])
        self.assertNotIn('stable',op)
        self.assertEqual(json.loads((self.state/'config/policy.json').read_text())['external_steps'],{})
        for name in ('runtime','native-cache'):
            self.assertTrue((self.state/name/'.devflower-session-center').is_file())
        for p in self.state.rglob('*'):
            self.assertEqual(stat.S_IMODE(p.stat().st_mode),0o700 if p.is_dir() else 0o600,str(p))
            if p.is_file(): self.assertNotIn(b'SYNTHETIC_DO_NOT_COPY',p.read_bytes())
        self.assertEqual(tree_hash(self.root),self.before_source)
        self.assertEqual(self.receipt['process']['pid'],process.pid)

    def test_saved_host_assistant_connection_survives_different_runtime_state(self):
        settings = self.home/'.config/devflower'
        settings.mkdir(parents=True,mode=0o700)
        connection = settings/'devflower-agent-connection.json'
        saved = json.dumps(dict(version=1,protocol='openai',base_url='http://127.0.0.1:12345',
            model='SYNTHETIC-saved-deepseek',effort='high',api_key='SYNTHETIC_HOST_SECRET')).encode()
        connection.write_bytes(saved);connection.chmod(0o600)
        self.native_before = self.native_bytes()
        first = self.running()
        code,raw = self.http('/api/devflower-agent/config')
        one = json.loads(raw)
        self.assertEqual(code,200);self.assertTrue(one['configured'])
        self.assertEqual(one['model'],'SYNTHETIC-saved-deepseek')
        self.assertNotIn(b'SYNTHETIC_HOST_SECRET',raw)
        first_state = self.state
        self.state = self.base/'second-installation-state'
        self.running()
        code,raw = self.http('/api/devflower-agent/config')
        two = json.loads(raw)
        self.assertEqual(code,200);self.assertEqual(two['fingerprint'],one['fingerprint'])
        self.assertEqual(connection.read_bytes(),saved)
        for state in (first_state,self.state):
            for path in state.rglob('*'):
                if path.is_file():self.assertNotIn(b'SYNTHETIC_HOST_SECRET',path.read_bytes())
        self.assertTrue(first.is_alive())

    def test_existing_live_service_reuses_pid_and_configuration(self):
        process = self.running()
        before = (self.state/'config/operator.json').read_bytes()
        code, out, err = self.call('--no-browser')
        self.assertEqual(code,0,err)
        self.assertIn(self.token,out)
        self.assertTrue(process.is_alive())
        self.assertEqual(json.loads((self.state/'runtime/service.json').read_text())['process']['pid'],process.pid)
        self.assertEqual((self.state/'config/operator.json').read_bytes(),before)
        self.assertEqual(self.call('--check')[0],0)

    def test_default_browser_gets_private_url_without_printing_token(self):
        self.running()
        with patch.object(start.webbrowser,'open',return_value=True) as browser:
            code,out,err = self.call()
        self.assertEqual(code,0,err)
        browser.assert_called_once_with(f"http://127.0.0.1:{self.receipt['port']}/#"+self.token,new=2)
        self.assertNotIn(self.token,out+err)

    def test_unknown_existing_directory_is_preserved(self):
        self.state.mkdir(mode=0o700)
        unknown = self.state/'unrelated.json'
        unknown.write_bytes(b'KEEP ME')
        self.assertEqual(self.call('--no-browser')[0],1)
        self.assertEqual(unknown.read_bytes(),b'KEEP ME')
        self.assertEqual(set(self.state.iterdir()),{unknown})

    def test_state_symlink_and_symlink_ancestor_are_rejected(self):
        target=self.base/'target';target.mkdir(mode=0o700)
        self.state.symlink_to(target,target_is_directory=True)
        self.assertEqual(self.call('--check')[0],1)
        self.assertEqual(list(target.iterdir()),[])
        self.state.unlink();self.state=self.base/'linked/child'
        (self.base/'linked').symlink_to(target,target_is_directory=True)
        self.assertEqual(self.call('--no-browser')[0],1)
        self.assertFalse((target/'child').exists())

    def test_nonprivate_existing_directory_is_not_chmodded(self):
        self.state.mkdir(mode=0o755)
        self.assertEqual(self.call('--no-browser')[0],1)
        self.assertEqual(stat.S_IMODE(self.state.stat().st_mode),0o755)

    def test_concurrent_startup_lock_is_rejected(self):
        self.state.mkdir(mode=0o700)
        fd=os.open(self.state,os.O_RDONLY|os.O_DIRECTORY)
        self.addCleanup(os.close,fd)
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        code,out,err=self.call('--no-browser')
        self.assertEqual(code,1)
        self.assertIn('另一个启动',err)
        self.assertEqual(list(self.state.iterdir()),[])

    def test_missing_cli_is_actionable_and_never_invents_provider(self):
        (self.bin/'codex').unlink();(self.bin/'claude').unlink()
        code,out,err=self.call('--check')
        self.assertEqual(code,1)
        self.assertIn('未找到 Codex/Claude CLI',err)
        self.assertFalse(self.state.exists())

    def test_missing_model_and_effort_require_explicit_choices(self):
        (self.home/'.codex/config.toml').write_text('')
        self.native_before=self.native_bytes()
        code,out,err=self.call('--check','--provider','codex')
        self.assertEqual(code,1);self.assertIn('--codex-model',err)
        code,out,err=self.call('--check','--provider','codex','--codex-model','chosen')
        self.assertEqual(code,1);self.assertIn('--codex-effort',err)
        self.assertEqual(self.call('--check','--provider','codex','--codex-model','chosen','--codex-effort','chosen-effort')[0],0)

    def test_missing_native_root_is_not_created(self):
        shutil.rmtree(self.home/'.codex');self.native_before=self.native_bytes()
        code,out,err=self.call('--check','--provider','codex')
        self.assertEqual(code,1);self.assertIn('先在自己的 CLI',err)
        self.assertFalse((self.home/'.codex').exists())

    def test_claude_env_preference_records_real_source_without_copying_auth(self):
        path=self.home/'.claude/settings.json'
        path.write_text(json.dumps({'env':{'ANTHROPIC_MODEL':'SYNTHETIC-env-choice','CLAUDE_CODE_EFFORT_LEVEL':'configured-effort','ANTHROPIC_AUTH_TOKEN':'SYNTHETIC_DO_NOT_COPY'}}))
        self.native_before=self.native_bytes()
        args=start.parser().parse_args(['--provider','claude'])
        profiles,local,models,pins,notices=start.discover(args,self.home)
        self.assertEqual(profiles['default-claude']['model'],'SYNTHETIC-env-choice')
        self.assertEqual(profiles['default-claude']['effort'],'configured-effort')
        self.assertIn('settings.json',models['claude'][0]['source'])
        self.assertEqual(profiles['default-claude']['readonly_files'],[[str(path),'.claude/settings.json']])
        from studio.model_catalog import configured_env
        self.assertEqual(configured_env(profiles['default-claude'])['ANTHROPIC_MODEL'],'SYNTHETIC-env-choice')
        self.assertNotIn('SYNTHETIC_DO_NOT_COPY',json.dumps([profiles,local,models,pins,notices]))

    def test_cli_without_required_metadata_flags_is_rejected(self):
        path=self.bin/'claude'
        path.write_text('#!'+sys.executable+'\nprint("SYNTHETIC old CLI")\n');path.chmod(0o700)
        self.assertIn('缺少现有适配器',self.call('--check','--provider','claude')[2])
        self.assertFalse(self.state.exists())

    def test_isolation_failure_and_missing_git_do_not_create_state(self):
        with patch.object(start,'probe',side_effect=start.Blocked('SYNTHETIC isolation unavailable')):
            self.assertEqual(self.call('--check')[0],1)
        (self.bin/'git').unlink()
        self.assertIn('缺少 Git',self.call('--check')[2])
        self.assertFalse(self.state.exists())

    def initialize_with_bind_failure(self):
        # Exercise the real Runtime/config initialization, then an explicit
        # socket failure. Never substitute a fake server for HTTP acceptance.
        with patch.object(workbench_server,'create_server',side_effect=start.Blocked('SYNTHETIC bind failure')):
            code,out,err=self.call('--no-browser')
        self.assertEqual(code,1)
        self.assertIn('SYNTHETIC bind failure',err)

    def test_runtime_initialization_keeps_siblings_private_and_native_bytes_unchanged(self):
        self.initialize_with_bind_failure()
        self.assertEqual(set(p.name for p in self.state.iterdir()),{'projects','config','runtime','native-cache'})
        self.assertEqual(list((self.state/'projects').iterdir()),[])
        for name in ('runtime','native-cache'):
            self.assertTrue((self.state/name/'.devflower-session-center').is_file())
        record=start.inspect_state(self.state)
        op=start.verify(self.state,record,self.before_source)
        self.assertEqual(op['allowed_roots'],[str(self.state/'projects')])
        self.assertEqual(len(op['profiles']),2)
        self.assertNotIn('stable',op)
        self.assertEqual(op['native_local']['codex']['home'],str(self.home))
        self.assertEqual(op['native_local']['claude']['config_root'],str(self.home/'.claude'))
        self.assertEqual(json.loads((self.state/'config/policy.json').read_text())['external_steps'],{})
        for p in self.state.rglob('*'):
            self.assertEqual(stat.S_IMODE(p.stat().st_mode),0o700 if p.is_dir() else 0o600,str(p))
            if p.is_file():self.assertNotIn(b'SYNTHETIC_DO_NOT_COPY',p.read_bytes())
        self.assertTrue((self.state/'runtime/start-intent.json').is_file())
        self.assertFalse((self.state/'runtime/service.json').exists())
        self.assertEqual(tree_hash(self.root),self.before_source)

    def test_failed_start_is_not_retried_or_overwritten(self):
        self.initialize_with_bind_failure()
        before={str(p.relative_to(self.state)):p.read_bytes() for p in self.state.rglob('*') if p.is_file()}
        with patch.object(workbench_server,'create_server') as factory:
            code,out,err=self.call('--no-browser')
        self.assertEqual(code,1);self.assertIn('上次启动结果未确认',err)
        factory.assert_not_called()
        self.assertEqual(before,{str(p.relative_to(self.state)):p.read_bytes() for p in self.state.rglob('*') if p.is_file()})

    def test_unknown_managed_runtime_file_and_hardlinks_are_refused(self):
        self.initialize_with_bind_failure()
        unknown=self.state/'runtime/foreign.json';unknown.write_text('{}');unknown.chmod(0o600)
        with self.assertRaisesRegex(start.Blocked,'运行目录含未知文件'):start.inspect_state(self.state)
        unknown.unlink()
        op=self.state/'config/operator.json';os.link(op,self.base/'alias')
        with self.assertRaises(start.Blocked):start.inspect_state(self.state)

    def test_runtime_metadata_guard_is_known_without_http(self):
        self.initialize_with_bind_failure()
        record=start.inspect_state(self.state)
        storage=self.state/'runtime'
        with metadata_guard(storage):
            lock=storage/'.native-metadata-preflight.lock'
            self.assertEqual(stat.S_IMODE(lock.stat().st_mode),0o600)
            self.assertEqual(lock.stat().st_nlink,1)
            self.assertEqual(start.inspect_state(self.state),record)
        self.assertEqual(start.inspect_state(self.state),record)
        start.verify(self.state,record,self.before_source)

    def test_runtime_managed_child_artifacts_are_known_without_http(self):
        self.initialize_with_bind_failure()
        record=start.inspect_state(self.state)
        artifacts=self.state/'runtime/managed-child-artifacts'
        artifacts.mkdir(mode=0o700)
        reservation=artifacts/'SYNTHETIC-reservation'
        reservation.mkdir(mode=0o700)
        start.write_new(reservation/'result.json',{'source':'SYNTHETIC; no child or model executed'})
        self.assertEqual(start.inspect_state(self.state),record)
        start.verify(self.state,record,self.before_source)

    def test_runtime_metadata_guard_reuses_healthy_service(self):
        process=self.running()
        receipt=(self.state/'runtime/service.json').read_bytes()
        with metadata_guard(self.state/'runtime'):pass
        start.inspect_state(self.state)
        self.assertEqual(self.http('/api/status')[0],200)
        code,out,err=self.call('--no-browser')
        self.assertEqual(code,0,err)
        self.assertTrue(process.is_alive())
        self.assertEqual((self.state/'runtime/service.json').read_bytes(),receipt)

    def test_runtime_managed_child_artifacts_reuse_healthy_service(self):
        process=self.running()
        receipt=(self.state/'runtime/service.json').read_bytes()
        artifacts=self.state/'runtime/managed-child-artifacts'
        artifacts.mkdir(mode=0o700)
        (artifacts/'SYNTHETIC-reservation').mkdir(mode=0o700)
        start.inspect_state(self.state)
        self.assertEqual(self.http('/api/status')[0],200)
        code,out,err=self.call('--no-browser')
        self.assertEqual(code,0,err)
        self.assertTrue(process.is_alive())
        self.assertEqual((self.state/'runtime/service.json').read_bytes(),receipt)

    def test_runtime_known_names_reject_symlinks_nonprivate_and_hardlinks(self):
        self.initialize_with_bind_failure()
        target=self.base/'unrelated-private.json'
        start.write_new(target,{'keep':'SYNTHETIC private bytes'})
        before=target.read_bytes()
        for name,is_dir in (('.native-metadata-preflight.lock',False),('managed-child-artifacts',True)):
            path=self.state/'runtime'/name
            with self.subTest(name=name,case='symlink'):
                path.symlink_to(target)
                with self.assertRaises(start.Blocked):start.inspect_state(self.state)
                if not is_dir:
                    with self.assertRaises(start.Blocked):
                        with metadata_guard(self.state/'runtime'):pass
                self.assertEqual(target.read_bytes(),before)
                path.unlink()
            with self.subTest(name=name,case='nonprivate'):
                if is_dir:path.mkdir(mode=0o700)
                else:start.write_new(path,{})
                path.chmod(0o755 if is_dir else 0o644)
                with self.assertRaises(start.Blocked):start.inspect_state(self.state)
                if is_dir:path.rmdir()
                else:path.unlink()
        lock=self.state/'runtime/.native-metadata-preflight.lock'
        os.link(target,lock)
        with self.assertRaises(start.Blocked):start.inspect_state(self.state)
        with self.assertRaises(start.Blocked):
            with metadata_guard(self.state/'runtime'):pass
        lock.unlink()
        artifacts=self.state/'runtime/managed-child-artifacts'
        artifacts.mkdir(mode=0o700)
        os.link(target,artifacts/'linked-result.json')
        with self.assertRaises(start.Blocked):start.inspect_state(self.state)
        self.assertEqual(target.read_bytes(),before)

    def test_runtime_known_names_do_not_accept_unknown_neighbours(self):
        self.initialize_with_bind_failure()
        for name,is_dir in (('.native-metadata-preflight.lock.extra',False),('managed-child-artifacts-extra',True)):
            path=self.state/'runtime'/name
            with self.subTest(name=name):
                if is_dir:path.mkdir(mode=0o700)
                else:start.write_new(path,{'keep':'unknown'})
                with self.assertRaisesRegex(start.Blocked,'运行目录含未知文件'):start.inspect_state(self.state)
                self.assertTrue(path.exists())
                if is_dir:path.rmdir()
                else:
                    self.assertEqual(json.loads(path.read_text()),{'keep':'unknown'})
                    path.unlink()

    def test_environment_only_gateway_or_ambiguous_preference_is_not_silently_changed(self):
        with patch.dict(os.environ,{'ANTHROPIC_BASE_URL':'https://SYNTHETIC.invalid'}):
            self.assertIn('不会复制认证或更换服务',self.call('--check','--provider','claude')[2])
        path=self.home/'.claude/settings.json'
        value=json.loads(path.read_text());value['env']['ANTHROPIC_MODEL']='SYNTHETIC-conflicting-choice'
        path.write_text(json.dumps(value));self.native_before=self.native_bytes()
        self.assertIn('冲突的模型',self.call('--check','--provider','claude')[2])
        self.assertEqual(self.call('--check','--provider','claude','--claude-model','chosen')[0],0)

    def test_private_native_directory_inside_install_is_not_hashed(self):
        (self.root/'.codex').mkdir()
        (self.root/'.codex/auth.json').write_text('SYNTHETIC_MUST_NOT_BE_READ')
        with patch.object(start,'manifest') as scanner:
            self.assertIn('干净的完整公开源码',self.call('--check')[2])
        scanner.assert_not_called()
        self.assertFalse(self.state.exists())

    def test_initialized_state_rejects_changed_source_without_http(self):
        self.initialize_with_bind_failure()
        before=(self.state/'config/operator.json').read_bytes()
        (self.root/'README.md').write_text('SYNTHETIC new source version\n')
        code,out,err=self.call('--no-browser')
        self.assertEqual(code,1);self.assertIn('源码版本已变化',err)
        self.assertEqual((self.state/'config/operator.json').read_bytes(),before)

    def test_initialized_state_rejects_changed_choice_and_dependency_without_http(self):
        self.initialize_with_bind_failure()
        self.assertIn('选择与已有状态不同',self.call('--no-browser','--codex-model','changed-choice')[2])
        path=self.bin/'codex';path.write_text(path.read_text()+'\n# SYNTHETIC dependency changed\n')
        self.assertIn('依赖/原生配置变化',self.call('--no-browser')[2])

    def test_preflight_checks_platform_bwrap_and_landlock_without_state(self):
        with patch.object(sys,'platform','unsupported'):
            self.assertIn('Python 3.11+ 和 Linux',self.call('--check')[2])
        with patch.object(start,'BWRAP',self.bin/'not-installed'):
            self.assertIn('安装 bubblewrap',self.call('--check')[2])
        with patch('orchestration.sandbox.abi',return_value=5):
            self.assertIn('Landlock ABI',self.call('--check')[2])
        self.assertFalse(self.state.exists())
        self.assertEqual(self.call('--check')[0],0)
        calls=[args for name,args in map(json.loads,self.log.read_text().splitlines()) if name=='bwrap']
        self.assertTrue(calls)
        for args in calls:
            self.assertIn('--unshare-user',args);self.assertIn('--unshare-pid',args)
            self.assertEqual(args[-2:],['--','/bin/true'])
            self.assertNotIn(str(self.home),args)

    def test_browser_announcement_does_not_print_capability(self):
        url='http://127.0.0.1:12345/#SYNTHETIC_PRIVATE_CAPABILITY'
        args=start.parser().parse_args([])
        output=io.StringIO()
        with contextlib.redirect_stdout(output),patch.object(start.webbrowser,'open',return_value=True) as browser:
            start.announce(url,args)
        browser.assert_called_once_with(url,new=2)
        self.assertNotIn('SYNTHETIC_PRIVATE_CAPABILITY',output.getvalue())

    def test_nonlocal_argument_is_refused_before_any_probe(self):
        with patch.object(start,'probe') as run,contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit) as result:
            start.main(['--host','0.0.0.0','--check'])
        self.assertEqual(result.exception.code,2);run.assert_not_called()
        self.assertEqual(self.call('--port','65536','--check')[0],1)
        self.assertFalse(self.state.exists())

    def test_source_version_change_cannot_reuse_service(self):
        self.running()
        config=(self.state/'config/operator.json').read_bytes()
        (self.root/'README.md').write_text('SYNTHETIC changed source\n')
        code,out,err=self.call('--no-browser')
        self.assertEqual(code,1);self.assertIn('源码版本已变化',err)
        self.assertEqual((self.state/'config/operator.json').read_bytes(),config)

    def test_changed_configuration_and_dependency_cannot_reuse(self):
        self.running()
        code,out,err=self.call('--no-browser','--codex-model','different-explicit-choice')
        self.assertEqual(code,1);self.assertIn('选择与已有状态不同',err)
        (self.bin/'codex').write_text((self.bin/'codex').read_text()+'\n# SYNTHETIC updated executable\n')
        self.assertIn('依赖/原生配置变化',self.call('--no-browser')[2])

    def test_unknown_config_file_or_capability_symlink_is_not_adopted(self):
        self.running()
        extra=self.state/'config/unknown.json';extra.write_text('{}');extra.chmod(0o600)
        self.assertIn('未知文件',self.call('--no-browser')[2]);extra.unlink()
        token=self.state/'runtime/workbench-capability';token.unlink();token.symlink_to(self.home/'.codex/auth.json')
        self.assertEqual(self.call('--no-browser')[0],1)

    def test_fake_pid_and_dead_service_are_not_reused_or_restarted(self):
        self.running()
        path=self.state/'runtime/service.json';value=json.loads(path.read_text())
        value['process']['start']='not-the-kernel-start-time';path.write_text(json.dumps(value))
        code,out,err=self.call('--no-browser')
        self.assertEqual(code,1);self.assertIn('不能确认',err)
        self.assertEqual(json.loads(path.read_text()),value)

    def test_valid_but_unrelated_pid_cannot_claim_listening_service(self):
        self.running()
        path=self.state/'runtime/service.json';value=json.loads(path.read_text())
        value['process']=start.process_identity(os.getpid());path.write_text(json.dumps(value))
        self.assertIn('PID 未持有',self.call('--no-browser')[2])

    def test_nonlocal_binding_and_host_header_are_rejected(self):
        with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit) as result:
            start.main(['--host','0.0.0.0','--check'])
        self.assertEqual(result.exception.code,2)
        self.assertFalse(self.state.exists())
        self.running()
        self.assertEqual(self.http('/',host='public.example')[0],403)

    def test_state_cannot_overlap_source_or_native_config(self):
        self.state=self.root/'runtime'
        self.assertEqual(self.call('--check')[0],1)
        self.state=self.home/'.codex/state'
        self.assertEqual(self.call('--check')[0],1)

    def test_public_manifest_adds_only_exact_launcher_and_keeps_private_files_out(self):
        self.assertIn('tools/devflower_start.py',PUBLIC_FILES)
        for name in ('tools/devflower_start_private.py','tests/test_public_start.py','tools/studio/private.json'):
            self.assertNotIn(name,PUBLIC_FILES)
        class Runtime:
            candidate_sha256='SYNTHETIC'
            def check_operator(self):pass
        result=PublicSourceReader(Runtime()).read('tools/devflower_start.py',1,20)
        self.assertIn('private local Development',result['text'])


if __name__=='__main__': unittest.main()
