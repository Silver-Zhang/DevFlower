"""In-process synthetic capability cache; no CLI, model or network."""
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from orchestration.common import Blocked
from studio import native_capability_cache as cache
from native_protocol_mechanics import LocalCodex

SID='00000000-0000-4000-8000-000000000001'
RAW=('\n'.join(json.dumps(x) for x in [dict(type='thread.started',thread_id=SID),dict(type='item.completed',item=dict(type='agent_message',text='synthetic')),dict(type='turn.completed',usage={})])+'\n').encode()
class RPC:
    def __init__(self,*a):pass
    def __enter__(self):return self
    def __exit__(self,*a):pass
    def request(self,*a):return {'config':{}}

class CapabilityCacheTests(unittest.TestCase):
    def setUp(self):
        cache.clear();self.addCleanup(cache.clear)
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.exe=self.root/'fixture-exe';self.exe.write_bytes(b'first')
        self.art=self.root/'artifacts';self.art.mkdir(mode=0o700)
    def key(self,**changes):
        values=dict(executable=str(self.exe),scope=str(self.root),artifacts=str(self.art),ordinary=True,no_tools=False,write=False,deadline=time.monotonic()+5,cancelled=lambda:False)
        values.update(changes);return cache.identity(**values)
    def call(self,key,probe,timeout=5,cancelled=lambda:False,recheck=None):
        return cache.run(key,recheck or (lambda:key),time.monotonic()+timeout,cancelled,probe)
    def test_cold_hit_and_ttl_and_capacity(self):
        key=self.key();self.assertIsNotNone(key);calls=[]
        def probe():calls.append(1);return True
        self.assertTrue(self.call(key,probe));self.assertTrue(self.call(key,probe));self.assertEqual(calls,[1])
        with patch.object(cache,'TTL',.01):
            cache.clear();self.call(key,probe);time.sleep(.02);self.call(key,probe)
        self.assertEqual(len(calls),3)
        cache.clear()
        with patch.object(cache,'CAPACITY',2):
            for k in ('a','b','c'):self.call(k,probe)
            self.call('a',probe)
        self.assertEqual(len(calls),7)
    def test_failure_cancel_nontrue_never_cached(self):
        key=self.key();calls=[]
        def bad():calls.append(1);raise Blocked('fixture failure')
        for _ in range(2):
            with self.assertRaises(Blocked):self.call(key,bad)
        with self.assertRaises(Blocked):self.call(key,lambda:None)
        with self.assertRaises(Blocked):self.call(key,lambda:True,cancelled=lambda:True)
        self.assertEqual(len(calls),2);self.assertTrue(self.call(key,lambda:True))
    def test_scope_and_type_and_executable_identity(self):
        key=self.key();self.assertIsNotNone(key)
        for changes in (dict(no_tools=True),dict(write=True)):
            self.assertNotEqual(key,self.key(**changes))
        self.assertIsNone(self.key(ordinary=False));self.assertIsNone(self.key(scope=None))
        self.exe.write_bytes(b'other');self.assertNotEqual(key,self.key())
        alias=self.root.parent/(self.root.name+'-link');alias.symlink_to(self.root,target_is_directory=True)
        self.addCleanup(alias.unlink,missing_ok=True)
        self.assertIsNone(self.key(scope=str(alias),artifacts=str(alias/'artifacts')))
        os.chmod(self.root,0o755);self.assertIsNone(self.key());os.chmod(self.root,0o700)
        old=self.root.with_name(self.root.name+'-old');self.root.rename(old);self.addCleanup(old.rename,self.root)
        self.root.mkdir(mode=0o700);(self.root/'artifacts').mkdir();(self.root/'fixture-exe').write_bytes(b'other')
        self.assertNotEqual(key,self.key())
        import shutil
        shutil.rmtree(self.root)
    def test_distinct_artifact_leaves_share_only_equivalent_safe_scope(self):
        other=self.root/'other';other.mkdir(mode=0o700)
        self.assertEqual(self.key(),self.key(artifacts=str(other)))
        other_scope=self.root/'different-storage';other_scope.mkdir(mode=0o700)
        other_art=other_scope/'artifacts';other_art.mkdir(mode=0o700)
        self.assertNotEqual(self.key(),self.key(scope=str(other_scope),artifacts=str(other_art)))
        symlink=self.root/'linked';symlink.symlink_to(self.art,target_is_directory=True)
        self.assertIsNone(self.key(artifacts=str(symlink)))
    def test_namespace_filesystem_and_template_invalidate(self):
        key=self.key()
        with patch.object(cache,'_namespace',return_value=('changed',)):self.assertNotEqual(key,self.key())
        with patch.object(cache,'_mount_identity',return_value=('different',)):self.assertNotEqual(key,self.key())
        with patch.object(cache,'TEMPLATE','changed'):self.assertNotEqual(key,self.key())
        with patch.object(cache,'_namespace',side_effect=OSError('unknown')):self.assertIsNone(self.key())
    def test_singleflight_failure_and_waiter_cancel_or_deadline(self):
        for outcome in ('success','failure'):
            cache.clear();key=self.key();ready=threading.Event();release=threading.Event();calls=[];results=[]
            def probe():
                calls.append(1);ready.set();release.wait(3)
                if outcome=='failure':raise Blocked('owner failed')
                return True
            def run():
                try:results.append(self.call(key,probe))
                except Blocked:results.append('blocked')
            owner=threading.Thread(target=run);waiter=threading.Thread(target=run);owner.start();self.assertTrue(ready.wait(2));waiter.start()
            with self.assertRaises(Blocked):self.call(key,probe,timeout=.05)
            with self.assertRaises(Blocked):self.call(key,probe,cancelled=lambda:True)
            release.set();owner.join(3);waiter.join(3)
            self.assertFalse(owner.is_alive());self.assertFalse(waiter.is_alive());self.assertEqual(calls,[1])
            self.assertEqual(results.count(True),2 if outcome=='success' else 0)
            self.assertEqual(results.count('blocked'),0 if outcome=='success' else 2)
    def test_recheck_change_does_not_publish_success(self):
        key=self.key()
        with self.assertRaises(Blocked):self.call(key,lambda:True,recheck=lambda:'different')
        calls=[];self.assertTrue(self.call(key,lambda:(calls.append(1) or True)));self.assertEqual(calls,[1])
    def test_inflight_capacity_is_bounded(self):
        ready=threading.Event();release=threading.Event();errors=[]
        def owner():
            try:self.call('first',lambda:(ready.set(),release.wait(2),True)[-1])
            except Exception as exc:errors.append(exc)
        with patch.object(cache,'CAPACITY',1):
            thread=threading.Thread(target=owner);thread.start();self.assertTrue(ready.wait(1))
            with self.assertRaisesRegex(Blocked,'并发已满'):self.call('second',lambda:True)
            release.set();thread.join(3)
        self.assertFalse(thread.is_alive());self.assertEqual(errors,[])
    def test_cancelled_owner_does_not_publish_or_retry_for_waiter(self):
        key=self.key();ready=threading.Event();release=threading.Event();cancel=threading.Event();out=[];calls=[]
        def probe():calls.append(1);ready.set();release.wait(2);return True
        def invoke(is_owner):
            try:self.call(key,probe,cancelled=cancel.is_set if is_owner else lambda:False);out.append('success')
            except Blocked:out.append('blocked')
        first=threading.Thread(target=invoke,args=(True,));first.start();self.assertTrue(ready.wait(2))
        second=threading.Thread(target=invoke,args=(False,));second.start();time.sleep(.05);cancel.set();release.set()
        first.join(3);second.join(3)
        self.assertFalse(first.is_alive());self.assertFalse(second.is_alive());self.assertEqual(calls,[1]);self.assertCountEqual(out,['blocked','blocked'])
        self.assertTrue(self.call(key,lambda:True))
    def test_waiter_rejects_owner_proof_after_original_expiry_without_retry(self):
        key=('stale-waiter',);clock=[100.0];ready=threading.Event();release=threading.Event();calls=[];out={}
        def probe():calls.append(threading.current_thread().name);ready.set();release.wait(2);return True
        def invoke(name):
            try:out[name]=cache.run(key,lambda:key,1000,lambda:False,probe)
            except Blocked:out[name]='blocked'
        with patch.object(cache.time,'monotonic',side_effect=lambda:clock[0]):
            owner=threading.Thread(target=invoke,args=('owner',),name='owner');owner.start();self.assertTrue(ready.wait(1))
            flight=cache._flights[key];real_wait=flight['event'].wait
            def delayed_wait(timeout=None):
                ok=real_wait(timeout)
                if ok and threading.current_thread().name=='waiter':clock[0]=221.0
                return ok
            flight['event'].wait=delayed_wait
            waiter=threading.Thread(target=invoke,args=('waiter',),name='waiter');waiter.start();time.sleep(.03);release.set()
            owner.join(2);waiter.join(2)
        self.assertFalse(owner.is_alive());self.assertFalse(waiter.is_alive());self.assertEqual(out,{'owner':True,'waiter':'blocked'});self.assertEqual(calls,['owner'])
    def test_hit_expiring_during_identity_check_is_rejected(self):
        clock=[100.0];calls=[];key=('slow-hit',)
        with patch.object(cache.time,'monotonic',side_effect=lambda:clock[0]):
            self.assertTrue(cache.run(key,lambda:key,1000,lambda:False,lambda:(calls.append(1) or True)))
            def late():clock[0]=221.0;return key
            with self.assertRaisesRegex(Blocked,'过期'):
                cache.run(key,late,1000,lambda:False,lambda:(calls.append(2) or True))
        self.assertEqual(calls,[1])

class LocalCodexCacheTests(CapabilityCacheTests):
    def test_actual_local_codex_reuses_probes_but_not_real_request_checks(self):
        binding=dict(executable=str(self.exe),home=str(self.root),config_root=str(self.root),provider='codex',model='m',effort='high',role='reviewer',session_id=None,project='test',conversation_format='text',readonly_files=[],native_interaction='ordinary',native_protected=[str(self.root)],_metadata_guard=str(self.root))
        calls=[];barrier=threading.Barrier(2);errors=[];configs=[];spawn_checks=[]
        class RecordingRPC(RPC):
            def request(self,*a):configs.append('config/read');return super().request(*a)
        class Store:
            def get(self,run):return {'data':{'cancel_requested':False}}
            def check_before_spawn(self):spawn_checks.append('checked')
            def intent(self,*a):return 'fixture'
            def finish(self,*a,**kw):pass
        def send(*args,**kw):
            calls.append('execute');barrier.wait(3);kw['on_stdout'](RAW)
            return dict(exit=0,timeout=False,overflow=False,cancelled=False),RAW,b''
        def invoke():
            try:LocalCodex(binding,dict(step_timeout_seconds=10,max_log_bytes=100000)).start('synthetic',str(self.root),self.art,store=Store(),run='fixture')
            except Exception as exc:errors.append(exc)
        with patch('studio.local_codex_adapter.CodexRPC',RecordingRPC),patch('studio.native_codex_exec_probe.require_exec_capability',return_value=True) as exec_probe,patch('studio.native_codex_exec_probe.require_native_write_capability',return_value=True) as write_probe,patch('studio.native_codex_exec.check_protected_hardlinks_isolated') as scan,patch('studio.model_registration.direct_environment_identity',return_value='fixture') as env,patch('orchestration.process.execute',side_effect=send):
            threads=[threading.Thread(target=invoke) for _ in range(2)]
            for t in threads:t.start()
            for t in threads:t.join(8)
            self.assertFalse(any(t.is_alive() for t in threads));self.assertEqual(errors,[])
            self.assertEqual(exec_probe.call_count,1);self.assertEqual(write_probe.call_count,1)
            self.assertEqual(scan.call_count,2);self.assertEqual(env.call_count,4);self.assertEqual(calls,['execute','execute'])
            self.assertEqual(len(configs),2);self.assertEqual(len(spawn_checks),2)
    def test_warm_cache_still_refuses_changed_environment_before_transport(self):
        binding=dict(executable=str(self.exe),home=str(self.root),config_root=str(self.root),provider='codex',model='m',effort='high',role='reviewer',session_id=None,project='test',conversation_format='text',readonly_files=[],native_interaction='ordinary',native_protected=[str(self.root)],_metadata_guard=str(self.root))
        def send(*args,**kw):kw['on_stdout'](RAW);return dict(exit=0,timeout=False,overflow=False,cancelled=False),RAW,b''
        client=LocalCodex(binding,dict(step_timeout_seconds=10,max_log_bytes=100000))
        with patch('studio.local_codex_adapter.CodexRPC',RPC),patch('studio.native_codex_exec_probe.require_exec_capability',return_value=True) as probe,patch('studio.native_codex_exec_probe.require_native_write_capability',return_value=True),patch('studio.native_codex_exec.check_protected_hardlinks_isolated'),patch('studio.model_registration.direct_environment_identity',side_effect=['same','same','before','after']),patch('orchestration.process.execute',side_effect=send) as transport:
            client.start('one',str(self.root),self.art)
            with self.assertRaisesRegex(Blocked,'预检期间变化'):client.start('two',str(self.root),self.art)
            self.assertEqual(probe.call_count,1);self.assertEqual(transport.call_count,1)
    def test_unprotected_scope_never_enables_cache(self):
        binding=dict(executable=str(self.exe),home=str(self.root),config_root=str(self.root),provider='codex',model='m',effort='high',role='reviewer',session_id=None,project='test',conversation_format='text',readonly_files=[],native_interaction='ordinary',native_protected=[str(self.art)],_metadata_guard=str(self.root))
        def send(*args,**kw):kw['on_stdout'](RAW);return dict(exit=0,timeout=False,overflow=False,cancelled=False),RAW,b''
        client=LocalCodex(binding,dict(step_timeout_seconds=10,max_log_bytes=100000))
        with patch('studio.local_codex_adapter.CodexRPC',RPC),patch('studio.native_codex_exec_probe.require_exec_capability',return_value=True) as probe,patch('studio.native_codex_exec_probe.require_native_write_capability',return_value=True),patch('studio.native_codex_exec.check_protected_hardlinks_isolated'),patch('orchestration.process.execute',side_effect=send):
            client.start('one',str(self.root),self.art);client.start('two',str(self.root),self.art)
            self.assertEqual(probe.call_count,2)
