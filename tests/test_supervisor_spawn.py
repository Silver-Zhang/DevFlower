"""Actual harmless OS children: post-spawn publication faults must never orphan."""
from pathlib import Path
import os,signal,sqlite3,subprocess,sys,time,unittest
from unittest.mock import patch
sys.path[:0]=[str(Path(__file__).parent),str(Path(__file__).parents[1]/'tools')]
from test_workbench_runtime import fixture,structured_session
from studio.native_terminals import NativeTerminals
from studio.workbench_sessions import WorkbenchSessions
from studio.configuration_assistant import ConfigurationAssistant
from orchestration.process import group_alive
from orchestration.store import process_identity


class SupervisorSpawnTests(unittest.TestCase):
 def setUp(self):
  self.base,self.rt=fixture(self,admission=True);self.w=self.base.project['id'];self.children=[];self.real=subprocess.Popen
  self.addCleanup(self.cleanup)
 def cleanup(self):
  for p in self.children:
   if p.poll() is None:
    try:os.killpg(p.pid,signal.SIGKILL)
    except ProcessLookupError:pass
   p.wait(timeout=3)
 def child(self,*args,**kwargs):
  if args and isinstance(args[0],(list,tuple)) and not any(str(v).endswith(('workbench_worker.py','session_worker.py','configuration_worker.py','devflower_native_terminal.py')) for v in args[0]):
   return self.real(*args,**kwargs)
  p=self.real([sys.executable,'-B','-c','import time;time.sleep(30)'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True);self.children.append(p);return p
 def assert_reaped(self):
  self.assertTrue(self.children)
  for p in self.children:self.assertIsNotNone(p.returncode,'owned child must be terminated AND reaped before returning')
 def exercise(self,caller,error):
  if caller=='workflow':
   key='spawn-failure';self.rt.flow.put('run',key,dict(id=key,workspace=self.w,state='QUEUED',created_at=1,graph={'budget':{'calls':1,'seconds':10}}));original=self.rt.flow.change
   def change(kind,key,changes,**kwargs):
    if kind=='run' and 'worker' in changes:raise error('post-spawn publication fault')
    return original(kind,key,changes,**kwargs)
   with patch('studio.workbench_runtime.subprocess.Popen',side_effect=self.child),patch.object(self.rt.flow,'change',side_effect=change):
    with self.assertRaises(Exception):self.rt.spawn(key)
   row=self.rt.flow.get('run',key)
  elif caller=='session':
   p=self.rt.operator['profiles']['default-codex'];permit=self.rt.session_center.prepare(dict(workspace=self.w,handle='new',profile='default-codex',prompt='Controlled synthetic publication fault',permission='read',model=p['model'],effort=p['effort'],mode='Ask'));original=self.rt.db.update
   def update(kind,key,changes,**kwargs):
    if kind=='session-operation' and 'worker' in changes:raise error('post-spawn publication fault')
    return original(kind,key,changes,**kwargs)
   with patch.object(self.rt.session_center,'_spawn',side_effect=self.child),patch.object(self.rt.db,'update',side_effect=update):
    try:self.rt.session_center.launch(permit['id'],permit['sha256'],'Controlled synthetic test')
    except Exception:pass
   row=self.rt.db.listing('session-operation',limit=None)[-1]
  elif caller=='configuration':
   ai=ConfigurationAssistant(self.rt);from studio.workbench_examples import examples
   choices={k:dict(provider=p['provider'],models=[dict(id=p['model'],efforts=[p['effort']])]) for k,p in self.rt.operator['profiles'].items()}
   with patch.object(ai,'choices',return_value=choices):
    p=self.rt.operator['profiles']['default-codex'];permit=ai.prepare(dict(workspace=self.w,graph=examples(self.rt.operator['profiles'])[0],goal='Synthetic fault; do not call any model',profile='default-codex',model=p['model'],effort=p['effort']))
   from studio.model_admission import ModelAdmission
   selected=permit['data']['chosen'];gate=ModelAdmission(self.rt);key=gate.key(selected['binding'],selected['cwd'])
   self.rt.db.put('model-admission',key,dict(state='VERIFIED',verified_at=time.time(),scope=gate.scope(selected['binding'],selected['cwd']),evidence='SYNTHETIC publication fault fixture; no model request'))
   original=self.rt.db.update
   def update(kind,key,changes,**kwargs):
    if kind=='session-operation' and 'worker' in changes:raise error('post-spawn publication fault')
    return original(kind,key,changes,**kwargs)
   with patch('studio.configuration_assistant.subprocess.Popen',side_effect=self.child),patch.object(self.rt.db,'update',side_effect=update):
    try:ai.start(permit['id'],permit['sha256'],'Controlled synthetic test')
    except Exception:pass
   row=self.rt.db.listing('session-operation',limit=None)[-1]
  else:
   _,session=structured_session(self.base,self.rt,'publication-fault','codex');term=NativeTerminals(self.rt,WorkbenchSessions(self.rt))
   def terminal_cleanup():
    # Old defective candidates may have an entry with no reader. Clean only
    # after assertions; retain the failure instead of crashing test teardown.
    for key,entry in list(term.entries.items()):
     if 'thread' not in entry:
      os.close(entry['master']);term.entries.pop(key)
    term.shutdown()
   self.addCleanup(terminal_cleanup);permit=term.prepare(dict(workspace=self.w,handle=session['handle']));original=self.rt.flow.change
   def change(kind,key,changes,**kwargs):
    if kind=='terminal' and changes.get('state')=='RUNNING':raise error('post-spawn publication fault')
    return original(kind,key,changes,**kwargs)
   with patch('studio.native_terminals.subprocess.Popen',side_effect=self.child),patch.object(self.rt.flow,'change',side_effect=change):
    try:term.start(dict(id=permit['id'],sha256=permit['sha256'],answer='Controlled synthetic test'))
    except Exception:pass
   row=self.rt.flow.list('terminal')[-1]
   self.assertFalse(term.entries,'failed publication cannot leave a terminal entry without a reader')
  self.assert_reaped();self.assertIn(row['state'],('FAILED','RECOVERY_REQUIRED'))
 def test_workflow_oserror(self):self.exercise('workflow',OSError)
 def test_workflow_sqlite_error(self):self.exercise('workflow',sqlite3.OperationalError)
 def test_session_oserror(self):self.exercise('session',OSError)
 def test_session_sqlite_error(self):self.exercise('session',sqlite3.OperationalError)
 def test_configuration_oserror(self):self.exercise('configuration',OSError)
 def test_configuration_sqlite_error(self):self.exercise('configuration',sqlite3.OperationalError)
 def test_terminal_oserror(self):self.exercise('terminal',OSError)
 def test_terminal_sqlite_error(self):self.exercise('terminal',sqlite3.OperationalError)
 def test_non_os_publication_errors_are_also_cleaned_up(self):
  for caller in ('workflow','session','configuration','terminal'):
   with self.subTest(caller=caller):self.exercise(caller,RuntimeError)
 def test_missing_child_identity_is_not_published_or_left_live(self):
  names={'workflow':'workbench_runtime','session':'session_center','configuration':'configuration_assistant','terminal':'native_terminals'}
  for caller,module in names.items():
   with self.subTest(caller=caller),patch('studio.'+module+'.process_identity',side_effect=lambda pid:process_identity(pid) if pid==os.getpid() else None):self.exercise(caller,OSError)
 def test_persistent_storage_failure_cannot_prevent_process_cleanup(self):
  key='permanent-storage-failure';self.rt.flow.put('run',key,dict(id=key,workspace=self.w,state='QUEUED',created_at=1))
  with patch('studio.workbench_runtime.subprocess.Popen',side_effect=self.child),patch.object(self.rt.flow,'change',side_effect=sqlite3.OperationalError('disk unavailable')):
   with self.assertRaises(sqlite3.OperationalError):self.rt.spawn(key)
  self.assert_reaped();self.assertNotIn(key,self.rt.children)
 def test_cleanup_terminates_the_fresh_session_descendants(self):
  from studio.spawn_ownership import reap_unpublished
  path=self.base.root/'descendant.pid'
  code='import subprocess,sys,time;from pathlib import Path;p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(30)"]);Path('+repr(str(path))+').write_text(str(p.pid));time.sleep(30)'
  p=self.real([sys.executable,'-B','-c',code],start_new_session=True);self.children.append(p)
  for _ in range(100):
   if path.exists():break
   time.sleep(.01)
  self.assertTrue(path.exists());record=process_identity(p.pid);reap_unpublished(p,record)
  self.assertFalse(group_alive(record));self.assertIsNotNone(p.returncode)
  descendant=process_identity(int(path.read_text()));self.assertTrue(descendant is None or descendant['state']=='Z')

if __name__=='__main__':unittest.main()
