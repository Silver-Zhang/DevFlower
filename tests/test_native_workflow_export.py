"""Compiler/real local process tests with explicitly fake CLIs; zero inference."""
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

TOOLS = Path(__file__).resolve().parents[1] / 'tools'
spec = importlib.util.spec_from_file_location('devflower_export', TOOLS / 'devflower_export.py')
export = importlib.util.module_from_spec(spec)
spec.loader.exec_module(export)


def graph():
    return {'version': 1, 'name': '代码审核 / Code review', 'nodes': [
        {'id': 'Developer', 'name': '开发部门', 'provider': 'codex', 'model': None, 'effort': None,
         'round_prompt': 'Produce the deliverable.', 'initial_prompt': 'Act independently.', 'output_prompt': 'Report results.'},
        {'id': 'Reviewer', 'name': '审核部门', 'provider': 'claude', 'model': None, 'effort': None,
         'round_prompt': 'Review the upstream delivery.'}],
        'edges': [{'id': 'handoff', 'source': 'Developer', 'target': 'Reviewer'}]}


FAKE_CLI = r'''import json, os, pathlib, sys, time
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
prompt = sys.stdin.read()
with open(os.environ['FAKE_CALLS'], 'a', encoding='utf-8') as f:
    f.write(json.dumps({'name':name,'args':args,'prompt':prompt,'cwd':os.getcwd(),
                        'home':os.environ['HOME'],'custom':os.environ.get('NATIVE_SETTING')})+'\n')
if name == os.environ.get('FAIL_PROVIDER'): sys.exit(7)
if name == os.environ.get('SLOW_PROVIDER'): time.sleep(30)
if name == os.environ.get('EMPTY_PROVIDER'): sys.exit(0)
answer = 'DEVELOPER_RESULT' if name == 'codex' else 'REVIEWED: ' + prompt
if name == 'codex':
    pathlib.Path(args[args.index('--output-last-message')+1]).write_text(answer, encoding='utf-8')
    print('native progress must not become handoff')
else:
    print(json.dumps({'type':'result','is_error':False,'result':answer,'session_id':'fake-session'}))
'''


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        for name in ('codex','claude'):
            path = self.bin / name
            path.write_text('#!' + sys.executable + '\n' + FAKE_CLI, encoding='utf-8')
            path.chmod(0o700)
        self.home = self.root / 'native-home'
        self.home.mkdir()
        self.config = self.home / 'config-sentinel'
        self.config.write_text('native skills MCP and subagent configuration unchanged')
        self.env = dict(os.environ, PATH=str(self.bin), HOME=str(self.home),
                        NATIVE_SETTING='inherit-me', FAKE_CALLS=str(self.root/'calls.jsonl'))
        self.run_dir = self.root / 'run'

    def script(self, doc=None, records=None):
        name, source = export.compile_workflow(doc or graph(), records, self.project, native_config=True)
        destination = self.root / name
        destination.write_text(source, encoding='utf-8')
        return destination

    def run_script(self, doc=None, args=(), env=None):
        script = self.script(doc)
        return subprocess.run([sys.executable,'-I','-B',str(script),'--workspace',str(self.project),
                               '--run-dir',str(self.run_dir),*args], env=env or self.env,
                              capture_output=True,text=True,timeout=15)

    def calls(self):
        path = self.root / 'calls.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_deterministic_compilation_and_safe_name(self):
        doc=graph()
        first=export.compile_workflow(doc, workspace=self.project,native_config=True)
        self.assertEqual(first,export.compile_workflow(doc,workspace=self.project,native_config=True))
        self.assertEqual(Path(first[0]).name,first[0])
        self.assertIn('代码审核',first[0])
        self.assertNotIn('from v2',first[1])
        self.assertIn("results['Developer'] = invoke_agent",first[1])
        self.assertIn("results['Reviewer'] = invoke_agent",first[1])

    def test_explicit_native_policy_required(self):
        with self.assertRaisesRegex(export.ExportError,'native-config'):
            export.compile_workflow(graph())

    def test_independent_script_real_processes_and_exact_handoff(self):
        original=self.config.read_bytes()
        result=self.run_script(args=('--task','test user task'))
        self.assertEqual(result.returncode,0,result.stderr)
        calls=self.calls()
        self.assertEqual([c['name'] for c in calls],['codex','claude'])
        self.assertIn('DEVELOPER_RESULT',calls[1]['prompt'])
        self.assertNotIn('native progress',calls[1]['prompt'])
        self.assertTrue(all('test user task' in c['prompt'] for c in calls))
        self.assertTrue(all(c['home']==str(self.home) and c['custom']=='inherit-me' for c in calls))
        self.assertEqual(self.config.read_bytes(),original)
        self.assertEqual(self.run_dir.stat().st_mode & 0o777,0o700)
        self.assertTrue((self.run_dir/'result.txt').is_file())
        self.assertNotIn('NATIVE_SETTING',(self.run_dir/'events.jsonl').read_text())
        self.assertFalse(any('dangerously' in a or a=='--tools' for c in calls for a in c['args']))

    def test_dry_run_has_no_cli_or_filesystem_effect(self):
        env=dict(self.env,PATH=str(self.root/'missing'))
        result=self.run_script(args=('--dry-run',),env=env)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(len(json.loads(result.stdout)['calls']),2)
        self.assertFalse(self.run_dir.exists())
        self.assertEqual(self.calls(),[])

    def test_preflight_all_clis_before_any_call(self):
        (self.bin/'claude').unlink()
        result=self.run_script()
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(self.calls(),[])
        self.assertFalse(self.run_dir.exists())

    def test_failure_stops_downstream_without_retry(self):
        result=self.run_script(env=dict(self.env,FAIL_PROVIDER='codex'))
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(len(self.calls()),1)
        self.assertFalse((self.run_dir/'Reviewer').exists())
        self.assertIn('CLI_FAILED',(self.run_dir/'events.jsonl').read_text())

    def test_zero_exit_without_answer_is_unknown(self):
        result=self.run_script(env=dict(self.env,EMPTY_PROVIDER='codex'))
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(len(self.calls()),1)
        self.assertIn('UNKNOWN',(self.run_dir/'events.jsonl').read_text())

    def test_timeout_is_unknown_and_no_retry(self):
        doc=graph();doc['nodes'][0]['timeout']=1
        result=self.run_script(doc,env=dict(self.env,SLOW_PROVIDER='codex'))
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(len(self.calls()),1)
        self.assertIn('UNKNOWN',(self.run_dir/'events.jsonl').read_text())

    def test_prompt_is_data_not_python_or_shell(self):
        doc=graph();payload="'''\n__import__('os').system('touch NEVER_CREATED')\n$HOME; echo evil"
        doc['nodes'][0]['round_prompt']=payload
        doc['nodes'][0]['model']='model; touch NEVER_CREATED'
        result=self.run_script(doc)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn(payload,self.calls()[0]['prompt'])
        self.assertIn('model; touch NEVER_CREATED',self.calls()[0]['args'])
        self.assertFalse((self.project/'NEVER_CREATED').exists())

    def test_control_only_edge_does_not_send_upstream_answer(self):
        doc=graph();doc['edges'][0]['transfer']={'mode':'none','max_chars':10}
        result=self.run_script(doc)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertNotIn('DEVELOPER_RESULT',self.calls()[1]['prompt'])

    def test_saved_handoff_limit_fails_without_truncation(self):
        doc=graph();doc['edges'][0]['transfer']={'mode':'answer','max_chars':2}
        result=self.run_script(doc)
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(len(self.calls()),1)
        self.assertEqual((self.run_dir/'Developer/answer.txt').read_text(),'DEVELOPER_RESULT')

    def test_typed_endpoints_and_scrambled_array(self):
        doc=graph()
        doc['nodes'] += [{'id':'Start','name':'输入','kind':'start','config':{'initial_data':'{"value":42}'}},
                         {'id':'End','name':'结果','kind':'output','config':{'format':'text'}}]
        doc['nodes'].reverse()
        doc['edges'] += [{'id':'entry','source':'Start','target':'Developer'},
                         {'id':'exit','source':'Reviewer','target':'End'}]
        result=self.run_script(doc)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual([c['name'] for c in self.calls()],['codex','claude'])
        self.assertIn('42',self.calls()[0]['prompt'])

    def test_reject_unsupported_control_nodes(self):
        for kind in ('loop','condition','merge','router','approval','tool','validation'):
            doc=graph();doc['nodes'][0]['kind']=kind
            with self.subTest(kind=kind),self.assertRaisesRegex(export.ExportError,'not implemented'):
                export.compile_workflow(doc,native_config=True)

    def test_reject_non_chain_and_invalid_edges(self):
        examples=[]
        doc=graph();doc['edges']=[];examples.append(doc)
        doc=graph();doc['edges'].append({'id':'back','source':'Reviewer','target':'Developer'});examples.append(doc)
        doc=graph();doc['nodes'].append(dict(doc['nodes'][0],id='Third'));doc['edges'].append({'id':'fork','source':'Developer','target':'Third'});examples.append(doc)
        doc=graph();doc['edges'][0]['target']='Missing';examples.append(doc)
        doc=graph();doc['edges'][0]['source_port']='false';examples.append(doc)
        doc=graph();doc['nodes'][0]['id']='../escape';examples.append(doc)
        for doc in examples:
            with self.subTest(doc=doc),self.assertRaises(export.ExportError):
                export.compile_workflow(doc,native_config=True)

    def test_reject_unportable_semantics(self):
        for change in ({'native_profile':'other'},{'skills':['opaque-hash']},{'mode':'Plan'},
                       {'session':{'kind':'existing','handle':'opaque'}},{'artifact_paths':['a.txt']},
                       {'output_contract':[{'path':'x'}]},{'timeout':True}):
            doc=graph();doc['nodes'][0].update(change)
            with self.subTest(change=change),self.assertRaises(export.ExportError):
                export.compile_workflow(doc,native_config=True)
        for mode in ('fields','result','artifacts','summary','envelope'):
            doc=graph();doc['edges'][0]['transfer']={'mode':mode,'max_chars':20}
            with self.subTest(mode=mode),self.assertRaises(export.ExportError):
                export.compile_workflow(doc,native_config=True)

    def test_output_limit_is_not_silently_ignored(self):
        doc=graph();doc['nodes'].append({'id':'End','name':'End','kind':'output','config':{}})
        doc['edges'].append({'id':'final','source':'Reviewer','target':'End','transfer':{'mode':'envelope','max_chars':1}})
        result=self.run_script(doc)
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(len(self.calls()),2)
        self.assertFalse((self.run_dir/'result.txt').exists())

    def test_model_effort_and_relative_directory(self):
        (self.project/'sub').mkdir()
        doc=graph();doc['nodes'][0].update(cwd='sub',model='native-model',effort='high')
        doc['nodes'][1].update(model='configured-alias',effort='medium')
        result=self.run_script(doc)
        self.assertEqual(result.returncode,0,result.stderr)
        first,second=self.calls()
        self.assertEqual(first['cwd'],str(self.project/'sub'))
        self.assertIn('model_reasoning_effort="high"',first['args'])
        self.assertIn('configured-alias',second['args'])
        self.assertIn('--effort',second['args'])

    def test_existing_run_directory_not_overwritten(self):
        self.run_dir.mkdir();sentinel=self.run_dir/'sentinel';sentinel.write_text('keep')
        result=self.run_script()
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(self.calls(),[])
        self.assertEqual(sentinel.read_text(),'keep')

    def test_saved_canvas_read_only_snapshot_and_pinned_agent(self):
        doc=graph();cfg=doc['nodes'][0].copy();cfg['cwd']=str(self.project)
        ident,revision='a'*32,'b'*64
        doc['nodes'][0]={'id':'Developer','name':'开发','kind':'agent','agent_id':ident,'agent_revision':revision}
        saved={'id':'draft','workspace':'workspace','graph':doc,'revision':sha256(export.canonical(doc).encode()).hexdigest()}
        record={'id':ident,'revision':revision,'workspace':'workspace','config':cfg}
        database=self.root/'v2.sqlite'
        with sqlite3.connect(database) as db:
            db.execute('CREATE TABLE records(kind TEXT,id TEXT,data TEXT,PRIMARY KEY(kind,id))')
            for kind,key,value in [('workspace','workspace',{'id':'workspace','path':str(self.project)}),
                                   ('draft','draft',saved),('agent-config',ident,record)]:
                db.execute('INSERT INTO records VALUES(?,?,?)',(kind,key,json.dumps(value)))
        before=database.read_bytes()
        loaded,records,root=export.read_saved(database,'workspace','draft')
        script=self.script(loaded,records)
        self.assertTrue(script.exists())
        self.assertEqual(database.read_bytes(),before)
        records[ident]['revision']='c'*64
        with self.assertRaisesRegex(export.ExportError,'stale'):
            export.compile_workflow(loaded,records,root,native_config=True)
        with self.assertRaises(export.ExportError):
            export.read_saved(database,'other-workspace','draft')
        self.assertFalse((self.root/'missing.sqlite').exists())
        with self.assertRaises(FileNotFoundError):
            export.read_saved(self.root/'missing.sqlite','x','y')

    def test_export_command_never_overwrites_destination(self):
        raw=self.root/'graph.json';raw.write_text(json.dumps(graph()))
        args=['--document',str(raw),'--workspace',str(self.project),'--output-dir',str(self.root),'--native-config']
        self.assertEqual(export.main(args),0)
        self.assertEqual(export.main(args),2)


if __name__ == '__main__':
    unittest.main()
