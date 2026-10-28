"""Synthetic same-PTY ownership tests: real SQLite and local sockets, no model."""
import os
import json
import io
from pathlib import Path
import sqlite3
import socket
from contextlib import contextmanager
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from orchestration.common import Blocked
from orchestration.common import canonical
from studio.native_attach import NativeAttach, AttachClient, receive
from studio.native_terminals import NativeTerminals
from studio.server import Conflict
from studio.workbench_store import WorkbenchStore


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='synthetic-attach-deadline-')
        self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name).resolve();self.db=WorkbenchStore(self.base/'state.sqlite')
        self.db.put('terminal','t',dict(id='t',workspace='w',state='RUNNING',provider='codex',
            session_id='synthetic',input_generation=0,input_owner='workbench',input_sequence=0))
        self.manager=NativeTerminals(SimpleNamespace(flow=self.db),None)
        self.readfd,self.writefd=os.pipe();self.addCleanup(os.close,self.readfd);self.addCleanup(os.close,self.writefd)
        self.manager.entries['t']=dict(proc=SimpleNamespace(poll=lambda:None),state='RUNNING',owner='workbench',
            generation=0,text='',first=0,next=0,inputs={},master=self.writefd)
        self.addCleanup(self.manager.shutdown)
        self.addCleanup(self.manager.entries.clear)

    def row(self):return self.manager.snapshot('w','t')

    def delayed(self,call):
        done=threading.Event();out={}
        def run():
            start=time.monotonic()
            try:out['result']=call()
            except Exception as exc:out['error']=exc
            out['elapsed']=time.monotonic()-start;done.set()
        thread=threading.Thread(target=run);thread.start()
        self.assertTrue(done.wait(2.5),'control did not finish within bounded budget')
        thread.join(1);self.assertFalse(thread.is_alive())
        return out


class DirectDeadlineTests(Fixture):
    def test_server_rejects_legacy_and_expired_messages_without_handoff(self):
        import struct
        hello=dict(token='synthetic-token',terminal='t',workspace='w',session_id='synthetic')
        messages=[hello,dict(op='claim',generation=0),dict(op='claim',generation=0,deadline=time.monotonic()-1)]
        class Stream:
            def __init__(self):self.input=io.BytesIO(b''.join(canonical(x)+b'\n' for x in messages));self.output=[]
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def readline(self,n):return self.input.readline(n)
            def write(self,b):self.output.append(b)
            def flush(self):pass
        stream=Stream()
        class Client:
            def getsockopt(self,*args):return struct.pack('3i',os.getpid(),os.getuid(),0)
            def settimeout(self,value):pass
            def makefile(self,*args):return stream
            def close(self):pass
        client=Client();server=NativeAttach.__new__(NativeAttach)
        server.terminals=self.manager;server.workspace='w';server.key='t';server.secret='synthetic-token'
        server.closed=threading.Event();server.guard=threading.Lock();server.clients={client}
        server._serve(client)
        replies=[json.loads(item) for item in stream.output]
        self.assertTrue(replies[0]['ok']);self.assertFalse(replies[1]['ok']);self.assertIn('版本过旧',replies[1]['error'])
        self.assertFalse(replies[2]['ok']);self.assertIn('超时',replies[2]['error'])
        self.assertEqual(self.manager.entries['t']['generation'],0);self.assertFalse(server.clients)

    def test_client_binds_control_deadline_before_sending(self):
        client=AttachClient.__new__(AttachClient);wire=[]
        client.stream=SimpleNamespace(write=lambda data:wire.append(data),flush=lambda:None)
        before=time.monotonic()
        with patch('studio.native_attach.receive',return_value={'ok':True,'result':{}}):
            client.request('claim',generation=0)
        request=json.loads(wire[0]);self.assertEqual(request['generation'],0)
        self.assertGreater(request['deadline'],before);self.assertLessEqual(request['deadline'],before+1.1)

    def test_expired_or_invalid_request_cannot_change_control(self):
        for deadline in (time.monotonic()-1,float('nan'),True,10**400):
            with self.subTest(deadline=deadline),self.assertRaises((Blocked,ValueError)):
                self.manager.claim('w','t',0,'local:one',deadline=deadline)
        self.assertEqual(self.row()['input_generation'],0)
        self.assertEqual(self.manager.entries['t']['owner'],'workbench')

    def test_client_transport_timeout_is_explicit_and_not_replayed(self):
        import socket
        client=AttachClient.__new__(AttachClient);client.stream=SimpleNamespace(write=lambda data:None,flush=lambda:None)
        with patch('studio.native_attach.receive',side_effect=socket.timeout()),patch.object(client,'close') as close:
            with self.assertRaisesRegex(Blocked,'结果未确认.*读取真实状态'):
                client.request('claim',generation=0)
            close.assert_called_once()

    def test_close_disables_socket_before_stream_can_flush(self):
        order=[];client=AttachClient.__new__(AttachClient)
        def stream_close():order.append('stream-close');raise OSError('synthetic failed flush')
        client.socket=SimpleNamespace(shutdown=lambda how:order.append('shutdown'),close=lambda:order.append('socket-close'))
        client.stream=SimpleNamespace(close=stream_close)
        client.close();self.assertEqual(order,['shutdown','stream-close','socket-close'])

    def test_database_contention_does_not_late_commit_and_explicit_retry_succeeds(self):
        writer=sqlite3.connect(self.db.path);writer.execute('BEGIN IMMEDIATE')
        try:
            out=self.delayed(lambda:self.manager.claim('w','t',0,'local:one'))
            self.assertIsInstance(out.get('error'),Blocked);self.assertLess(out['elapsed'],2.5)
            # The lock persists past the old three-second socket deadline.
            threading.Event().wait(2.2)
            self.assertEqual((self.manager.entries['t']['owner'],self.manager.entries['t']['generation']),('workbench',0))
            self.assertEqual(self.db.get('terminal','t')['input_generation'],0)
        finally:writer.rollback();writer.close()
        row=self.manager.claim('w','t',0,'local:one');self.assertEqual(row['input_generation'],1)
        with self.assertRaises(Conflict):self.manager.input('w','t',1,'old',generation=0)

    def test_manager_lock_timeout_never_commits_later(self):
        held=threading.Event();release=threading.Event()
        def lock():
            with self.manager.lock:held.set();release.wait(4)
        owner=threading.Thread(target=lock);owner.start();self.assertTrue(held.wait(1))
        try:
            out=self.delayed(lambda:self.manager.claim('w','t',0,'local:one'))
            self.assertIsInstance(out.get('error'),Blocked)
        finally:release.set();owner.join(2)
        self.assertFalse(owner.is_alive());self.assertEqual(self.row()['input_generation'],0)
        self.assertEqual(self.manager.entries['t']['owner'],'workbench')
        self.assertEqual(self.manager.claim('w','t',0,'local:one')['input_generation'],1)

    def test_deadline_expiry_inside_transaction_rolls_back(self):
        def slow(value):
            threading.Event().wait(1.1)
            return canonical(value)
        with patch('studio.native_terminals.canonical',side_effect=slow):
            out=self.delayed(lambda:self.manager.claim('w','t',0,'local:one'))
        self.assertIsInstance(out.get('error'),Blocked)
        self.assertEqual(self.db.get('terminal','t')['input_generation'],0)
        self.assertEqual((self.manager.entries['t']['owner'],self.manager.entries['t']['generation']),('workbench',0))
        self.assertEqual(self.manager.claim('w','t',0,'local:one')['input_generation'],1)

    def test_ambiguous_persistence_failure_revokes_input(self):
        real=self.db.tx
        @contextmanager
        def failing(*args,**kwargs):
            with real(*args,**kwargs) as conn:
                yield conn
                raise sqlite3.DatabaseError('synthetic persistence failure')
        with patch.object(self.db,'tx',side_effect=failing):
            with self.assertRaisesRegex(Blocked,'结果未确认'):
                self.manager.claim('w','t',0,'local:one')
        self.assertIsNone(self.manager.entries['t']['owner'])
        with self.assertRaises(Conflict):self.manager.input('w','t',1,'old',generation=0)

    def test_release_contention_preserves_owner_then_explicit_release(self):
        self.manager.claim('w','t',0,'local:one');writer=sqlite3.connect(self.db.path);writer.execute('BEGIN IMMEDIATE')
        try:
            out=self.delayed(lambda:self.manager.release('w','t',1,'local:one'))
            self.assertIsInstance(out.get('error'),Blocked)
            self.assertEqual((self.manager.entries['t']['owner'],self.manager.entries['t']['generation']),('local:one',1))
        finally:writer.rollback();writer.close()
        self.assertEqual(self.manager.release('w','t',1,'local:one')['input_owner'],'unclaimed')
        with self.assertRaises(Conflict):self.manager.input('w','t',1,'stale',owner='local:one',generation=1)

    def test_concurrent_claims_have_one_winner(self):
        barrier=threading.Barrier(2);results=[]
        def claim(owner):
            barrier.wait()
            try:self.manager.claim('w','t',0,owner);results.append('ok')
            except (Blocked,Conflict):results.append('blocked')
        threads=[threading.Thread(target=claim,args=(owner,)) for owner in ('local:a','local:b')]
        for thread in threads:thread.start()
        for thread in threads:thread.join(3);self.assertFalse(thread.is_alive())
        self.assertCountEqual(results,['ok','blocked']);self.assertEqual(self.row()['input_generation'],1)

    def test_detach_during_database_contention_revokes_input(self):
        self.manager.claim('w','t',0,'local:one');writer=sqlite3.connect(self.db.path);writer.execute('BEGIN IMMEDIATE')
        try:
            out=self.delayed(lambda:self.manager.detach('w','t','local:one'))
            self.assertNotIn('error',out);self.assertIsNone(self.manager.entries['t']['owner'])
            self.assertEqual(self.row()['input_owner'],'unclaimed')
            with self.assertRaises(Conflict):self.manager.input('w','t',1,'stale',owner='local:one',generation=1)
        finally:writer.rollback();writer.close()
        self.assertEqual(self.manager.claim('w','t',1)['input_generation'],2)


class UnixSocketDeadlineTests(Fixture):
    def setUp(self):
        super().setUp()
        # /proc/self/fd supplies a short local pathname despite long CI worktrees.
        self.rootfd=os.open(self.base,os.O_RDONLY|os.O_DIRECTORY);self.addCleanup(os.close,self.rootfd)
        short=f'/proc/self/fd/{self.rootfd}/attach';os.mkdir(short,0o700)
        with patch('studio.native_attach.tempfile.mkdtemp',return_value=short):
            self.server=NativeAttach(self.manager,'w','t')
        self.addCleanup(self.close_server)

    def close_server(self):
        self.server.close();self.server.thread.join(3)
        self.assertFalse(self.server.thread.is_alive())
        until=time.monotonic()+3
        while self.server.clients and time.monotonic()<until:threading.Event().wait(.02)
        self.assertFalse(self.server.clients,'socket handlers must terminate')

    def connect(self):
        client=AttachClient(self.server.path);self.addCleanup(client.close);return client

    def test_sqlite_lock_returns_error_before_client_timeout_no_late_claim(self):
        client=self.connect();self.assertEqual(client.request('observe')['input_generation'],0)
        writer=sqlite3.connect(self.db.path);writer.execute('BEGIN IMMEDIATE')
        try:
            out=self.delayed(lambda:client.request('claim',generation=0))
            self.assertIsInstance(out.get('error'),Blocked);self.assertLess(out['elapsed'],2.5)
            threading.Event().wait(2.2)
            self.assertEqual(self.manager.entries['t']['generation'],0)
        finally:writer.rollback();writer.close()
        self.assertEqual(client.request('claim',generation=0)['input_generation'],1)

    def test_sqlite_lock_returns_release_error_without_late_release(self):
        client=self.connect();client.request('claim',generation=0)
        owner=self.manager.entries['t']['owner'];writer=sqlite3.connect(self.db.path);writer.execute('BEGIN IMMEDIATE')
        try:
            out=self.delayed(lambda:client.request('release',generation=1))
            self.assertIsInstance(out.get('error'),Blocked);self.assertLess(out['elapsed'],2.5)
            threading.Event().wait(2.2)
            self.assertEqual((self.manager.entries['t']['owner'],self.manager.entries['t']['generation']),(owner,1))
        finally:writer.rollback();writer.close()
        self.assertEqual(client.request('release',generation=1)['input_owner'],'unclaimed')
        with self.assertRaises(Blocked):client.request('input',generation=1,sequence=1,text='stale')

    def test_manager_lock_timeout_and_two_claims_one_winner(self):
        a=self.connect();b=self.connect();held=threading.Event();release=threading.Event()
        def hold():
            with self.manager.lock:held.set();release.wait(4)
        guard=threading.Thread(target=hold);guard.start();self.assertTrue(held.wait(1))
        try:
            out=self.delayed(lambda:a.request('claim',generation=0));self.assertIsInstance(out.get('error'),Blocked)
        finally:release.set();guard.join(2)
        self.assertFalse(guard.is_alive());self.assertEqual(self.row()['input_generation'],0)
        barrier=threading.Barrier(2);results=[]
        def claim(c):
            barrier.wait()
            try:c.request('claim',generation=0);results.append('ok')
            except Blocked:results.append('blocked')
        threads=[threading.Thread(target=claim,args=(c,)) for c in (a,b)]
        for t in threads:t.start()
        for t in threads:t.join(3);self.assertFalse(t.is_alive())
        self.assertCountEqual(results,['ok','blocked'])
        with self.assertRaises(Conflict):self.manager.input('w','t',1,'old',generation=0)
        winner=a if self.manager.entries['t']['owner']==a.hello['owner'] else b
        winner.close();deadline=time.monotonic()+3
        while self.manager.entries['t']['owner'] is not None and time.monotonic()<deadline:threading.Event().wait(.02)
        self.assertIsNone(self.manager.entries['t']['owner']);self.assertEqual(self.row()['input_owner'],'unclaimed')
        row=self.manager.claim('w','t',self.row()['input_generation']);self.assertEqual(row['input_owner'],'workbench')


class UnixSocketPairTests(Fixture):
    # A socketpair exercises the same real AF_UNIX protocol and server threads;
    # unlike pathname bind it is available under the restricted host sandbox.
    test_sqlite_lock_returns_error_before_client_timeout_no_late_claim = UnixSocketDeadlineTests.test_sqlite_lock_returns_error_before_client_timeout_no_late_claim
    test_sqlite_lock_returns_release_error_without_late_release = UnixSocketDeadlineTests.test_sqlite_lock_returns_release_error_without_late_release
    test_manager_lock_timeout_and_two_claims_one_winner = UnixSocketDeadlineTests.test_manager_lock_timeout_and_two_claims_one_winner

    def setUp(self):
        super().setUp();self.server=NativeAttach.__new__(NativeAttach)
        self.server.terminals=self.manager;self.server.workspace='w';self.server.key='t'
        self.server.closed=threading.Event();self.server.clients=set();self.server.guard=threading.Lock()
        self.server.secret='synthetic-token';self.workers=[];self.addCleanup(self.close_workers)

    def connect(self):
        server_sock,client_sock=socket.socketpair(socket.AF_UNIX)
        with self.server.guard:self.server.clients.add(server_sock)
        worker=threading.Thread(target=self.server._serve,args=(server_sock,));worker.start();self.workers.append(worker)
        client=AttachClient.__new__(AttachClient);client.socket=client_sock;client.socket.settimeout(3)
        client.stream=client.socket.makefile('rwb')
        self.addCleanup(client.close)
        client.stream.write(canonical(dict(token='synthetic-token',terminal='t',workspace='w',session_id='synthetic'))+b'\n');client.stream.flush()
        client.hello=receive(client.stream);self.assertTrue(client.hello['ok'])
        return client

    def close_workers(self):
        self.server.closed.set()
        with self.server.guard:
            for client in list(self.server.clients):
                try:client.shutdown(socket.SHUT_RDWR)
                except OSError:pass
        for worker in self.workers:worker.join(3);self.assertFalse(worker.is_alive())
        self.assertFalse(self.server.clients)

    def test_legacy_and_expired_control_messages_fail_closed(self):
        client=self.connect()
        for request in (dict(op='claim',generation=0),dict(op='claim',generation=0,deadline=time.monotonic()-1)):
            client.stream.write(canonical(request)+b'\n');client.stream.flush()
            reply=receive(client.stream);self.assertFalse(reply['ok'])
            self.assertEqual(self.manager.entries['t']['generation'],0)
        self.assertEqual(client.request('claim',generation=0)['input_generation'],1)

if __name__=='__main__':unittest.main()
