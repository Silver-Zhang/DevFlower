"""Real SQLite metadata no-op and synthetic Journal receipt regressions."""
from contextlib import closing,contextmanager
import json
from pathlib import Path
import sqlite3
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from orchestration.common import canonical
from studio.usable_store import Storage
from studio.workbench_store import WorkbenchStore
from studio.workbench_scheduler import Journal
from studio.native_local import profile_key

class MetadataNoopTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.root=Path(self.tmp.name);self.store=Storage(self.root/'storage')
  self.store.db=lambda:closing(sqlite3.connect(self.store.path,timeout=.01))
  self.value={'workspace':'synthetic','binding':{'x':1}}
  self.store.put('chat-profile','p',self.value)
  self.writer=sqlite3.connect(self.store.path,timeout=.01);self.addCleanup(self.writer.close)
 def hold(self):self.writer.execute('BEGIN IMMEDIATE')
 def test_same_canonical_is_read_only_during_real_writer(self):
  observer=sqlite3.connect(self.store.path);self.addCleanup(observer.close)
  before=observer.execute('PRAGMA data_version').fetchone()[0]
  row=observer.execute('SELECT rowid,data FROM objects WHERE kind=? AND id=?',('chat-profile','p')).fetchone()
  statements=[]
  def traced():
   db=sqlite3.connect(self.store.path,timeout=.01);db.set_trace_callback(statements.append);return closing(db)
  self.hold();t=time.monotonic()
  with patch.object(self.store,'db',side_effect=traced):self.store.put('chat-profile','p',dict(binding={'x':1},workspace='synthetic'))
  self.assertLess(time.monotonic()-t,1);self.assertTrue(self.writer.in_transaction)
  self.assertEqual(len(statements),1);self.assertTrue(statements[0].startswith('SELECT '))
  self.assertEqual(observer.execute('PRAGMA data_version').fetchone()[0],before)
  self.assertEqual(observer.execute('SELECT rowid,data FROM objects WHERE kind=? AND id=?',('chat-profile','p')).fetchone(),row)
 def test_changed_and_missing_still_fail_busy_then_commit(self):
  self.hold()
  for key,value in [('p',dict(self.value,workspace='changed')),('missing',self.value)]:
   with self.subTest(key=key),self.assertRaises(sqlite3.OperationalError) as caught:self.store.put('chat-profile',key,value)
   self.assertEqual(caught.exception.sqlite_errorcode & 255,sqlite3.SQLITE_BUSY)
  self.assertEqual(self.store.get('chat-profile','p'),self.value)
  self.writer.rollback()
  for key,value in [('p',dict(self.value,workspace='changed')),('missing',self.value)]:
   self.store.put('chat-profile',key,value);self.assertEqual(self.store.get('chat-profile',key),value)
 def test_noncanonical_text_is_rewritten_and_requires_writer(self):
  with self.store.db() as db,db:db.execute('UPDATE objects SET data=? WHERE id=?',(json.dumps(self.value,indent=2),'p'))
  self.hold()
  with self.assertRaises(sqlite3.OperationalError):self.store.put('chat-profile','p',self.value)
  self.writer.rollback();self.store.put('chat-profile','p',self.value)
  with self.store.db() as db:raw=db.execute('SELECT data FROM objects WHERE id=?',('p',)).fetchone()[0]
  self.assertEqual(raw,canonical(self.value).decode())
 def test_read_error_is_not_success_or_retried(self):
  error=sqlite3.OperationalError('synthetic read failure');error.sqlite_errorcode=sqlite3.SQLITE_IOERR
  class BadRead:
   def execute(self,*args):raise error
  @contextmanager
  def bad():yield BadRead()
  with patch.object(self.store,'db',side_effect=bad) as opened,patch('studio.usable_store.time.sleep') as sleep:
   with self.assertRaises(sqlite3.OperationalError):self.store.put('chat-profile','p',self.value)
   self.assertEqual(opened.call_count,1);sleep.assert_not_called()
 def test_nonbusy_write_errors_do_not_retry(self):
  for code in (sqlite3.SQLITE_LOCKED,sqlite3.SQLITE_IOERR):
   error=sqlite3.OperationalError('synthetic write failure');error.sqlite_errorcode=code;calls=[]
   class Cursor:
    def fetchone(self):return ('different',)
    def close(self):pass
   class BadWrite:
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def execute(self,sql,*args):
     if sql.startswith('SELECT'):return Cursor()
     raise error
   @contextmanager
   def db():
    calls.append(1);yield BadWrite()
   with self.subTest(code=code),patch.object(self.store,'db',side_effect=db),patch('studio.usable_store.time.sleep') as sleep:
    with self.assertRaises(sqlite3.OperationalError):self.store.put('chat-profile','p',{'changed':True})
    self.assertEqual(len(calls),1);sleep.assert_not_called()
 def test_competing_storage_update_after_read_has_original_ordering(self):
  other=Storage(self.root/'storage');other.db=lambda:closing(sqlite3.connect(other.path,timeout=.01))
  new={'workspace':'other'};calls=[]
  class Connection:
   def __init__(self,db):self.db=db
   def __enter__(self):self.db.__enter__();return self
   def __exit__(self,*args):return self.db.__exit__(*args)
   def execute(self,sql,*args):
    cursor=self.db.execute(sql,*args)
    if not sql.startswith('SELECT'):return cursor
    class Cursor:
     def fetchone(self):return cursor.fetchone()
     def close(self):
      cursor.close();calls.append(1)
      if len(calls)==1:other.put('chat-profile','p',new)
    return Cursor()
  @contextmanager
  def staged():
   with closing(sqlite3.connect(self.store.path,timeout=.01)) as db:yield Connection(db)
  with patch.object(self.store,'db',side_effect=staged):self.store.put('chat-profile','p',self.value)
  self.assertEqual(self.store.get('chat-profile','p'),new)
  self.store.put('chat-profile','p',self.value);calls.clear()
  with patch.object(self.store,'db',side_effect=staged):self.store.put('chat-profile','p',{'workspace':'last'})
  self.assertEqual(self.store.get('chat-profile','p'),{'workspace':'last'})
 def test_real_journal_completion_does_not_rewrite_same_profile(self):
  flow=WorkbenchStore(self.root/'flow.sqlite');flow.put('run','r',dict(id='r',state='RUNNING'))
  flow.put('activation','a',dict(id='a',run='r',state='RUNNING',chat_key='c'))
  binding=dict(provider='codex',home=str(self.root/'home'),config_root=str(self.root/'home/.codex'),execution_backend='native-local',session_id=None)
  flow.put('node-session','c',dict(id='c',workspace='w',binding=binding))
  journal=Journal(SimpleNamespace(db=flow,rt=SimpleNamespace(db=self.store),run='r'),'a')
  op=journal.intent('r','AGENT_REQUEST',dict(kind='codex',requested_session=None))
  proc=journal.intent('r','PROCESS',dict(context={'provider':'codex'}))
  process={'pid':123,'start':'synthetic'};journal.finish(proc,'RUNNING',process=process)
  journal.finish(proc,'EXITED',exit=0,timeout=False,cancelled=False,overflow=False)
  sid='00000000-0000-4000-8000-000000000045';journal.finish(op,'AGENT_SESSION',actual_session=sid)
  value=self.store.get('chat-profile',profile_key(dict(binding,session_id=sid)))
  self.hold();journal.finish(op,'AGENT_COMPLETED',actual_session=sid,transport={'mode':'synthetic-native-exec'})
  step=flow.get('native-step',op);activation=flow.get('activation','a');run=flow.get('run','r')
  self.assertEqual(step['status'],'AGENT_COMPLETED');self.assertEqual(step['data']['actual_session'],sid)
  self.assertEqual(activation['observed_session'],sid);self.assertEqual(activation['process'],process)
  self.assertEqual(activation['process_result']['exit'],0);self.assertEqual(run['actual_calls'],1)
  self.assertEqual(self.store.get('chat-profile',profile_key(dict(binding,session_id=sid))),value)
  self.assertTrue(self.writer.in_transaction)
