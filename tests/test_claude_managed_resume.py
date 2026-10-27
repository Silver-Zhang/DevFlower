"""Synthetic authority provenance; no Provider is called."""
import subprocess,unittest
import test_native_local
from studio.native_local import bind
from orchestration.common import Blocked

class ClaudeManagedResume(unittest.TestCase):
 def setUp(self):
  self.base=test_native_local.NativeLocalTests();self.base.setUp();self.addCleanup(self.base.doCleanups)
  self.rt=self.base.rt;self.workspace=self.base.base.project;self.sid='00000000-0000-4000-8000-000000000001'
  self.binding=bind(self.rt,self.rt.operator['profiles']['default-claude'],self.workspace['id']);self.binding.update(runtime_profile='default-claude',session_id=self.sid)
 def test_no_unproved_managed_resume(self):
  from studio.managed_claude import require_managed
  with self.assertRaises(Blocked):require_managed(self.rt,self.binding,self.workspace['path'],'a'*64)
  from studio.workflow_inspection import inspect_workflow
  from studio.workbench_examples import examples
  graph=examples(self.rt.operator['profiles'])[0];node=next(n for n in graph['nodes'] if n['kind']=='agent');node.update(profile='default-claude',provider='claude',model=self.binding['model'],effort=self.binding['effort'],session='f'*64)
  issues=inspect_workflow(self.rt,graph,'task',workspace_path=self.workspace['path'],workspace_id=self.workspace['id'])['issues'];self.assertTrue(any(i.get('node')==node['id'] and i.get('field')=='session' and i['severity']=='error' for i in issues))
 def test_forged_profile_or_late_receipt_cannot_grant_resume(self):
  from studio.managed_claude import require_managed
  self.rt.db.put('chat-profile','fake',dict(workspace=self.workspace['id'],binding=self.binding))
  self.rt.db.put('managed-claude','fake',dict(workspace=self.workspace['id'],session_id=self.sid,home=self.binding['home'],cwd=self.workspace['path'],native_sha256='a'*64,origin='missing'))
  with self.assertRaises(Blocked):require_managed(self.rt,self.binding,self.workspace['path'],'a'*64)
 def test_real_child_journal_identity_and_history_are_required(self):
  from studio.managed_claude import record,require_managed
  from studio.native_local import session_lease
  from orchestration.store import process_identity
  exe=self.base.base.source/'fixture-cli';exe.write_text('#!/usr/bin/python3\nimport time\ntime.sleep(.4)\n');exe.chmod(0o700);self.binding['executable']=str(exe)
  argv=[str(exe),'--session-id',self.sid];child=subprocess.Popen(argv,start_new_session=True);pid=process_identity(child.pid);self.assertIsNotNone(pid);self.assertEqual(child.wait(),0)
  process=dict(operation_id='p1',exit=0,process=pid,timeout=False,cancelled=False,overflow=False);receipt=dict(session_id=self.sid,home=self.binding['home'],cwd=self.workspace['path'],provider='claude',permission_mode='native-plan/read-tools',process=process,requested_model=self.binding['model'],requested_effort=self.binding['effort'])
  self.rt.db.put('session-operation','owned',dict(id='owned',state='COMPLETED',workspace=self.workspace['id'],cwd=self.workspace['path'],binding=self.binding,native_session=self.sid,requested_session=self.sid,process_result=process,receipt=receipt))
  self.rt.db.put('session-step','r1',dict(id='r1',operation='owned',kind='AGENT_REQUEST',status='AGENT_COMPLETED',data=dict(requested_session=self.sid,actual_session=self.sid)))
  self.rt.db.put('session-step','p1',dict(id='p1',operation='owned',kind='PROCESS',status='EXITED',data=dict(operation_id='p1',process=pid,cwd=self.workspace['path'],context=dict(provider='claude'),argv=argv)))
  record(self.rt,self.binding,self.workspace['path'],'a'*64,'session','owned')
  with session_lease(self.binding):
   require_managed(self.rt,self.binding,self.workspace['path'],'a'*64)
   with self.assertRaises(Blocked):
    with session_lease(self.binding):pass
  for b,cwd,sha in [(self.binding,self.workspace['path'],'b'*64),(dict(self.binding,home=self.binding['home']+'-other'),self.workspace['path'],'a'*64),(self.binding,self.workspace['path']+'/other','a'*64)]:
   with self.assertRaises(Blocked):require_managed(self.rt,b,cwd,sha)
  self.rt.db.update('session-operation','owned',dict(state='FAILED'))
  with self.assertRaises(Blocked):require_managed(self.rt,self.binding,self.workspace['path'],'a'*64)
 def test_real_synthetic_cli_create_and_continue_same_uuid(self):
  from unittest.mock import patch
  from types import SimpleNamespace
  import os
  from orchestration.common import save,digest
  folder=self.base.base.source.parent;exe=folder/'synthetic-claude'
  exe.write_text('''#!/usr/bin/python3
import sys,os,json,re,time
from pathlib import Path
a=sys.argv
if '--help' in a:
 print('--strict-mcp-config --mcp-config --tools --permission-mode --settings');sys.exit(0)
if sys.stdin.isatty():
 assert '--strict-mcp-config' in a and a[a.index('--tools')+1]=='' and json.loads(a[a.index('--mcp-config')+1])=={'mcpServers':{}}
a=sys.argv;flag='--resume' if '--resume' in a else '--session-id';sid=a[a.index(flag)+1];cwd=os.getcwd();root=Path(os.environ['HOME'])/'.claude'/'projects'/re.sub(r'[^a-zA-Z0-9]','-',cwd);root.mkdir(parents=True,exist_ok=True)
time.sleep(.3)
with (root/(sid+'.jsonl')).open('a') as f:
 f.write(json.dumps({'type':'user','sessionId':sid,'cwd':cwd,'timestamp':'2026-10-20T01:00:00Z','version':'synthetic','message':{'role':'user','content':'synthetic'}})+'\\n')
 f.write(json.dumps({'type':'assistant','sessionId':sid,'timestamp':'2026-10-20T01:00:01Z','message':{'role':'assistant','content':'synthetic response'}})+'\\n')
result={'type':'result','subtype':'success','is_error':False,'session_id':sid,'result':'synthetic response'}
if '--json-schema' in a:result['structured_output']={'reply':'synthetic response','summary':'synthetic','accepted':flag=='--resume','result_json':'{}','artifacts':[],'evidence':[],'issues':[],'next':''}
print(json.dumps(result))
''');exe.chmod(0o700)
  op=self.rt.operator;op['profiles']['default-claude']['executable']=str(exe);op['dependencies'][str(exe)]=digest(exe.read_bytes());save(self.rt.operator_file,op);self.rt.operator_sha256=digest(self.rt.operator_file.read_bytes())
  def run(handle):
   data=dict(workspace=self.workspace['id'],handle=handle,profile='default-claude',prompt='synthetic request',permission='read',mode='Ask',interaction='click')
   permit=self.rt.session_center.prepare(data)
   with patch.object(self.rt.session_center,'_spawn',return_value=SimpleNamespace(pid=os.getpid())):
    launched=self.rt.session_center.launch(permit['id'],permit['sha256'],{'action':'send'})
   self.rt.session_center.execute(launched['id']);return self.rt.session_center.operation(launched['id'],self.workspace['id'])
  first=run('new');self.assertEqual(first['state'],'COMPLETED',first.get('reason'));sid=first['session_id'];self.assertTrue(sid)
  rows=self.rt.session_center.discover(self.workspace['id'],True)['discovery']['sessions'];row=next(x for x in rows if x['session_id']==sid);self.assertTrue(row['resume'],row.get('note'))
  second=run(row['handle']);self.assertEqual(second['state'],'COMPLETED',second.get('reason'));self.assertEqual(second['session_id'],sid)
  from studio.workbench_examples import examples
  from studio.workflow_inspection import inspect_workflow
  checked=examples(op['profiles'])[0];selected=next(n for n in checked['nodes'] if n['kind']=='agent');profile=op['profiles']['default-claude'];selected.update(profile='default-claude',provider='claude',model=profile['model'],effort=profile['effort'],session=row['handle'])
  self.rt.session_center.discover(self.workspace['id'],True)
  issues=inspect_workflow(self.rt,checked,'synthetic',workspace_path=self.workspace['path'],workspace_id=self.workspace['id'])['issues'];self.assertFalse(any(i.get('node')==selected['id'] and i.get('field')=='session' and i['severity']=='error' for i in issues),issues)
  import threading,time
  request=dict(workspace=self.workspace['id'],handle=row['handle'],profile='default-claude',prompt='concurrent synthetic',permission='read',mode='Ask',interaction='click')
  permits=[self.rt.session_center.prepare(request) for _ in range(2)]
  with patch.object(self.rt.session_center,'_spawn',return_value=SimpleNamespace(pid=os.getpid())):
   pending=[self.rt.session_center.launch(p['id'],p['sha256'],{'action':'send'}) for p in permits]
  worker=threading.Thread(target=self.rt.session_center.execute,args=(pending[0]['id'],));worker.start()
  for _ in range(200):
   if self.rt.db.get('session-operation',pending[0]['id'])['state']=='RUNNING':break
   time.sleep(.01)
  self.rt.session_center.execute(pending[1]['id']);worker.join(5);self.assertFalse(worker.is_alive())
  states=[self.rt.db.get('session-operation',v['id'])['state'] for v in pending];self.assertEqual(states,['COMPLETED','FAILED'])
  self.rt.session_center.discover(self.workspace['id'],True);native=self.rt.db.get('session-handle',row['handle'])['native']
  with open(native['path'],'a') as f:f.write('{"type":"user","sessionId":"'+sid+'","cwd":'+__import__('json').dumps(self.workspace['path'])+',"message":{"role":"user","content":"external change"}}\n')
  with self.assertRaises(Blocked):self.rt.session_center.prepare(dict(workspace=self.workspace['id'],handle=row['handle'],profile='default-claude',prompt='must not send',permission='read',mode='Ask',interaction='click'))
  issues=inspect_workflow(self.rt,checked,'synthetic',workspace_path=self.workspace['path'],workspace_id=self.workspace['id'])['issues'];self.assertTrue(any(i.get('node')==selected['id'] and i.get('field')=='session' and i['severity']=='error' for i in issues))
  from studio.workbench_examples import examples
  from studio.workbench_scheduler import Scheduler
  body=examples(op['profiles'])[4];agent=next(n for n in body['nodes'] if n['kind']=='agent');profile=op['profiles']['default-claude'];agent.update(profile='default-claude',provider='claude',model=profile['model'],effort=profile['effort'])
  graph=dict(version=3,name='Synthetic loop',nodes=[dict(id='start',kind='start',name='Start'),dict(id='cycle',kind='loop',name='Loop',body=body,max_rounds=2,until=dict(field='result.accepted',op='equals',value=True)),dict(id='result',kind='result',name='Result')],edges=[dict(id='a',source='start',target='cycle'),dict(id='b',source='cycle',target='result')],budget=dict(calls=4,seconds=120))
  permit=self.rt.prepare_flow(dict(workspace=self.workspace['id'],graph=graph,task='Synthetic loop',interaction='click'))
  with patch.object(self.rt,'spawn'):launched=self.rt.launch_flow(permit['id'],permit['sha256'],{'action':'start'})
  run_id=launched['run']['id'];Scheduler(self.rt,run_id).run_graph();state=self.rt.flow.get('run',run_id)
  self.assertEqual(state['state'],'COMPLETED',state.get('reason'));rounds=[a for a in self.rt.flow.list('activation') if a.get('run')==run_id and a.get('node')=='cycle/agent'];self.assertEqual(len(rounds),2);self.assertEqual(len({a['receipt']['session_id'] for a in rounds}),1)
  terminal_session=run('new');self.assertEqual(terminal_session['state'],'COMPLETED',terminal_session.get('reason'))
  from studio.native_terminals import NativeTerminals
  from studio.workbench_sessions import WorkbenchSessions
  import time
  manager=NativeTerminals(self.rt,WorkbenchSessions(self.rt));self.addCleanup(manager.shutdown)
  rows=self.rt.session_center.discover(self.workspace['id'],True)['discovery']['sessions'];terminal_row=next(x for x in rows if x['session_id']==terminal_session['session_id'])
  permit=manager.prepare(dict(workspace=self.workspace['id'],handle=terminal_row['handle'],interaction='click'))
  opened=manager.start(dict(id=permit['id'],sha256=permit['sha256'],answer={'action':'open-terminal'}));terminal_id=opened['id']
  for _ in range(150):
   terminal=manager.snapshot(self.workspace['id'],terminal_id)
   if terminal['state'] not in ('STARTING','RUNNING'):break
   time.sleep(.05)
  self.assertEqual(terminal['state'],'COMPLETED',terminal);self.assertEqual(terminal['session_id'],terminal_session['session_id'])
  rows=self.rt.session_center.discover(self.workspace['id'],True)['discovery']['sessions']
  after=next(x for x in rows if x['session_id']==terminal_session['session_id'])
  evidence=self.rt.db.get('managed-terminal',terminal_id)
  self.assertEqual(evidence['exit'],0);self.assertEqual(evidence['child']['pgid'],evidence['wrapper']['pid'])
  self.rt.flow.change('terminal',terminal_id,dict(state='RUNNING'))
  with self.assertRaises(Blocked):self.rt.session_center.prepare(dict(workspace=self.workspace['id'],handle=after['handle'],profile='default-claude',prompt='not sent',permission='read',mode='Ask',interaction='click'))
  self.rt.flow.change('terminal',terminal_id,dict(state='COMPLETED'))
  self.rt.db.put('managed-terminal',terminal_id,dict(evidence,exit=1))
  with self.assertRaises(Blocked):self.rt.session_center.prepare(dict(workspace=self.workspace['id'],handle=after['handle'],profile='default-claude',prompt='not sent',permission='read',mode='Ask',interaction='click'))
  self.rt.db.put('managed-terminal',terminal_id,evidence)
  resumed=run(after['handle']);self.assertEqual(resumed['state'],'COMPLETED',resumed.get('reason'))

 def test_long_terminal_chain_and_invalid_links(self):
  from unittest.mock import patch
  from studio.managed_claude import require_managed
  from types import SimpleNamespace
  cwd=self.workspace['path'];b=self.binding;sid=self.sid
  identity=dict(workspace=b['project'],home=b['home'],config_root=b['config_root'],cwd=cwd,session_id=sid)
  physical=dict(pid=1,start=1,boot_id='boot',host='host',pgid=1,sid=1)
  process=dict(operation_id='p',exit=0,process=physical)
  receipt=dict(session_id=sid,home=b['home'],cwd=cwd,provider='claude',permission_mode='native-plan/read-tools',process=process,requested_model=b.get('model'),requested_effort=b.get('effort'))
  op=dict(state='COMPLETED',workspace=b['project'],cwd=cwd,binding=b,native_session=sid,requested_session=sid,process_result=process,receipt=receipt)
  steps=[dict(kind='AGENT_REQUEST',status='AGENT_COMPLETED',data=dict(requested_session=sid,actual_session=sid)),dict(kind='PROCESS',status='EXITED',data=dict(operation_id='p',process=physical,cwd=cwd,context=dict(provider='claude'),argv=[b['executable'],'--resume',sid]))]
  prior=dict(identity,native_sha256='base',kind='session',origin='base');evidences={};terminals={}
  for i in range(70):
   origin=str(i);sha='sha'+origin
   evidences[origin]=dict(exit=0,native_sha256=sha,child=physical,wrapper=physical,argv=['cli'],prior=prior)
   terminals[origin]=dict(state='COMPLETED',exit=0,workspace=b['project'],cwd=cwd,session_id=sid,provider='claude',process=physical)
   prior=dict(identity,native_sha256=sha,kind='terminal',origin=origin)
  def get(kind,key):
   if kind=='managed-claude':return prior
   if kind=='managed-terminal':return evidences[key]
   if kind=='session-operation':return op
   raise KeyError(key)
  fake=SimpleNamespace(db=SimpleNamespace(get=get,listing=lambda *a,**k:steps),flow=SimpleNamespace(get=lambda kind,key:terminals[key]))
  with patch('devflower_native_terminal.native_cli_argv',return_value=['cli']):
   require_managed(fake,b,cwd,'sha69')
   original=evidences['0']['prior'];evidences['0']['prior']=dict(original,session_id='other')
   with self.assertRaises(Blocked):require_managed(fake,b,cwd,'sha69')
   evidences['0']['prior']=prior
   with self.assertRaises(Blocked):require_managed(fake,b,cwd,'sha69')
   evidences['0']['prior']=dict(original,origin='missing',kind='terminal')
   with self.assertRaises(Blocked):require_managed(fake,b,cwd,'sha69')
