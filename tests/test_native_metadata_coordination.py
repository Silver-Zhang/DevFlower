"""Synthetic metadata clients and real filesystem coordination; no model calls."""
import os
import multiprocessing
from contextlib import contextmanager
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from orchestration.common import Blocked
from studio.native_codex_exec import check_protected_hardlinks
from studio.native_codex_rpc import metadata,metadata_guard

def hold_guard(folder,ready,release):
    with metadata_guard(folder,2):
        ready.set();release.wait(3)


class MetadataCoordinationTests(unittest.TestCase):
    def test_original_guard_allows_regular_metadata_but_refuses_new_symlink(self):
        for symlink in (False,True):
            with self.subTest(symlink=symlink),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);real=os.fwalk;calls=0
                def walk(*args,**kwargs):
                    nonlocal calls
                    calls+=1;yield from real(*args,**kwargs)
                    if calls==1:
                        if symlink:(root/'shim').symlink_to('/synthetic/cli')
                        else:(root/'metadata').write_text('synthetic')
                with patch('studio.native_codex_exec.os.fwalk',walk):
                    if symlink:
                        with self.assertRaises(Blocked):check_protected_hardlinks([str(root)])
                    else:check_protected_hardlinks([str(root)])

    def test_scan_waits_for_metadata_and_new_metadata_waits_for_scan(self):
        with tempfile.TemporaryDirectory() as folder:
            entered=threading.Event();finish=threading.Event();scan=threading.Event();errors=[];calls=[]
            class RPC:
                def __init__(self,*a):calls.append(a)
                def __enter__(self):return self
                def __exit__(self,*a):pass
                def request(self,method,params):entered.set();finish.wait(2);return {'value':method}
            binding=dict(executable='synthetic',home='/synthetic/home',config_root='/synthetic/config',_metadata_guard=folder)
            def read():
                try:metadata(binding,'/synthetic/workspace','model/list',{})
                except Exception as e:errors.append(e)
            def check():
                try:
                    with metadata_guard(folder,2):scan.set()
                except Exception as e:errors.append(e)
            with patch('studio.native_codex_rpc.CodexRPC',RPC):
                first=threading.Thread(target=read);first.start();self.assertTrue(entered.wait(1))
                second=threading.Thread(target=check);second.start();self.assertFalse(scan.wait(.1))
                finish.set();first.join(2);second.join(2);self.assertTrue(scan.is_set())
                self.assertFalse(first.is_alive());self.assertFalse(second.is_alive());self.assertFalse(errors)
                entered.clear();finish.clear()
                with metadata_guard(folder,2):
                    third=threading.Thread(target=read);third.start();self.assertFalse(entered.wait(.1))
                self.assertTrue(entered.wait(1));finish.set();third.join(2);self.assertFalse(third.is_alive())
            self.assertEqual(len(calls),2)

    def test_metadata_requests_are_isolated_allowlisted_and_never_retried(self):
        with tempfile.TemporaryDirectory() as folder:
            events=[]
            class RPC:
                def __init__(self,*args):events.append(('init',args))
                def __enter__(self):return self
                def __exit__(self,*args):events.append(('close',))
                def request(self,method,params):events.append(('request',method,params));raise Blocked('synthetic uncertain response')
            binding=dict(executable='synthetic',home='/synthetic/A',config_root='/synthetic/A/.codex',_metadata_guard=folder)
            with patch('studio.native_codex_rpc.CodexRPC',RPC):
                for method in ('thread/start','thread/resume','turn/start','config/read'):
                    with self.assertRaises(Blocked):metadata(binding,'/synthetic/work',method,{})
                self.assertEqual(events,[])
                with self.assertRaises(Blocked):metadata(binding,'/synthetic/work','thread/read',{})
                self.assertEqual([x[0] for x in events],['init','request','close'])
                with self.assertRaises(Blocked):metadata(dict(binding,home='/synthetic/B'),'/synthetic/other','account/read',{})
                self.assertEqual([x[0] for x in events],['init','request','close','init','request','close'])
                self.assertEqual(events[0][1][1:4],('/synthetic/A','/synthetic/A/.codex','/synthetic/work'))
                self.assertEqual(events[3][1][1:4],('/synthetic/B','/synthetic/A/.codex','/synthetic/other'))

    def test_workbench_metadata_cannot_create_shim_during_protected_scan(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder);root=base/'protected';root.mkdir();storage=base/'storage';storage.mkdir()
            started=threading.Event();created=threading.Event();errors=[]
            class RPC:
                def __init__(self,*args):
                    (root/'shim').symlink_to('/synthetic/cli');created.set()
                def __enter__(self):return self
                def __exit__(self,*args):pass
                def request(self,method,params):return {}
            binding=dict(executable='synthetic',home='/synthetic/home',config_root='/synthetic/config',_metadata_guard=str(storage))
            def reader():
                try:started.set();metadata(binding,'/synthetic/work','thread/list',{})
                except Exception as exc:errors.append(exc)
            with patch('studio.native_codex_rpc.CodexRPC',RPC):
                with metadata_guard(storage):
                    worker=threading.Thread(target=reader);worker.start();self.assertTrue(started.wait(1))
                    check_protected_hardlinks([str(root)]);self.assertFalse(created.is_set())
                worker.join(2);self.assertFalse(worker.is_alive());self.assertTrue(created.is_set());self.assertFalse(errors)

    def test_timeout_and_unsafe_lock_fail_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            with metadata_guard(folder):
                with self.assertRaisesRegex(Blocked,'超时'):
                    with metadata_guard(folder,.03):pass
                with self.assertRaisesRegex(Blocked,'取消'):
                    with metadata_guard(folder,1,lambda:True):pass
            p=Path(folder)/'.native-metadata-preflight.lock';p.unlink();p.symlink_to('/synthetic/outside')
            with self.assertRaises(Blocked):
                with metadata_guard(folder):pass

    def test_lock_replacement_during_coordination_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            lock=Path(folder)/'.native-metadata-preflight.lock'
            with self.assertRaises(Blocked):
                with metadata_guard(folder):
                    lock.rename(Path(folder)/'original');lock.write_text('replacement')

    def test_lock_is_shared_between_owned_processes(self):
        with tempfile.TemporaryDirectory() as folder:
            ctx=multiprocessing.get_context('fork');ready=ctx.Event();release=ctx.Event()
            worker=ctx.Process(target=hold_guard,args=(folder,ready,release));worker.start()
            try:
                self.assertTrue(ready.wait(2))
                with self.assertRaises(Blocked):
                    with metadata_guard(folder,.05):pass
            finally:
                release.set();worker.join(3)
            self.assertFalse(worker.is_alive());self.assertEqual(worker.exitcode,0)
            with metadata_guard(folder,.2):pass

    def test_all_six_read_methods_use_fresh_clients_with_exact_scope(self):
        with tempfile.TemporaryDirectory() as folder:
            opened=[];closed=[]
            class RPC:
                def __init__(self,*args):opened.append(args)
                def __enter__(self):return self
                def __exit__(self,*args):closed.append(True)
                def request(self,method,params):return {'method':method}
            binding=dict(executable='synthetic',home='/synthetic/home',config_root='/synthetic/config',_metadata_guard=folder)
            methods=('model/list','account/read','thread/list','thread/read','thread/turns/list','thread/items/list')
            with patch('studio.native_codex_rpc.CodexRPC',RPC):
                for method in methods:self.assertEqual(metadata(binding,'/synthetic/cwd',method,{})['method'],method)
            self.assertEqual(len(opened),6);self.assertEqual(len(closed),6)
            self.assertTrue(all(x[:4]==('synthetic','/synthetic/home','/synthetic/config','/synthetic/cwd') and 0<x[4]<=30 for x in opened))

    def test_execution_config_probe_and_preflight_are_coordinated_separately(self):
        from studio.local_codex_adapter import LocalCodex
        with tempfile.TemporaryDirectory() as folder:
            sid='00000000-0000-4000-8000-000000000001';events=[]
            binding=dict(executable='synthetic',home=folder,config_root=folder,provider='codex',model='fixture',effort='high',role='reviewer',session_id=sid,project='fixture',conversation_format='text',readonly_files=[],native_interaction='ordinary',native_protected=[folder],_metadata_guard=folder)
            @contextmanager
            def gate(path,timeout,cancelled):
                events.append('lock');yield timeout;events.append('unlock')
            class RPC:
                def __init__(self,*args):pass
                def __enter__(self):return self
                def __exit__(self,*args):pass
                def request(self,method,params):events.append(method);return {'config':{}}
            rows=[dict(type='thread.started',thread_id=sid),dict(type='item.completed',item=dict(type='agent_message',text='synthetic')),dict(type='turn.completed',usage={})]
            raw=('\n'.join(json.dumps(row) for row in rows)+'\n').encode()
            def execute(*args,**kwargs):
                events.append('execute');kwargs['on_stdout'](raw)
                return dict(exit=0,timeout=False,overflow=False,cancelled=False),raw,b''
            with patch('studio.native_codex_rpc.metadata_guard',gate),patch('studio.local_codex_adapter.CodexRPC',RPC),patch('studio.native_codex_exec.check_protected_hardlinks',side_effect=lambda x:events.append('scan')),patch('studio.native_codex_exec_probe.require_exec_capability'),patch('studio.native_codex_exec_probe.require_native_write_capability'),patch('orchestration.process.execute',side_effect=execute):
                LocalCodex(binding,dict(step_timeout_seconds=10,max_log_bytes=100000)).start('synthetic',folder,folder)
            self.assertEqual(events,['lock','config/read','unlock','lock','scan','unlock','execute'])

if __name__=='__main__':unittest.main()
