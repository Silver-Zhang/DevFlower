"""Private descriptor checks with fake sockets; no native CLI or model."""
import json
import io
import os
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from orchestration.common import Blocked
from studio.native_attach import AttachClient,NativeAttach,descriptor_address
from studio.public_source_tool import PUBLIC_FILES,PublicSourceReader


class FakeListener:
    def __init__(self,*args):self.bound=None;self.closed=False
    def bind(self,address):self.bound=address
    def listen(self,*args):pass
    def settimeout(self,*args):pass
    def accept(self):raise OSError('synthetic closed')
    def close(self):self.closed=True

class PrivateAttachTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.storage=Path(self.tmp.name)/('long-storage-'+'x'*80);self.storage.mkdir(mode=0o700)
        self.manager=SimpleNamespace(rt=SimpleNamespace(storage=self.storage),snapshot=lambda *a:dict(session_id='synthetic-session',provider='codex',process_alive=True,state='RUNNING'))
    def make(self):
        with patch('studio.native_attach.socket.socket',FakeListener):server=NativeAttach(self.manager,'workspace','terminal')
        self.addCleanup(server.close);server.thread.join(2);return server
    def test_private_descriptor_and_abstract_address(self):
        server=self.make();data=json.loads(server.path.read_text())
        self.assertEqual(server.root.parent,self.storage/'native-attach')
        self.assertEqual(server.root.stat().st_mode&0o777,0o700)
        self.assertEqual(server.path.stat().st_mode&0o777,0o600)
        self.assertEqual(data['version'],2);self.assertEqual(server.listener.bound,'\0'+data['socket'])
        self.assertLess(len(server.listener.bound),108);self.assertEqual(descriptor_address(data,server.path),server.listener.bound)
        server.close();self.assertEqual(server.path.read_bytes(),b'');self.assertEqual(server.cleanup_info['status'],'retained_private_metadata')
    def test_replaced_root_not_deleted_and_original_capability_invalidated(self):
        server=self.make();moved=server.root.with_name('moved');server.root.rename(moved);server.root.mkdir(mode=0o700)
        marker=server.root/'other';marker.write_text('untouched')
        server.close();self.assertEqual(marker.read_text(),'untouched');self.assertEqual((moved/'attach.json').read_bytes(),b'')
        self.assertEqual(server.cleanup_info['status'],'identity_changed')
    def test_cleanup_failure_is_recorded_without_deleting_root(self):
        server=self.make()
        with patch('studio.native_attach.os.ftruncate',side_effect=OSError('synthetic cleanup failure')):server.close()
        self.assertEqual(server.cleanup_info['status'],'cleanup_failed')
        self.assertIn('synthetic cleanup failure',server.cleanup_info['reason'])
        self.assertTrue(server.root.is_dir())
    def test_storage_and_base_symlinks_refused(self):
        alias=self.storage.parent/'alias';alias.symlink_to(self.storage,target_is_directory=True)
        self.manager.rt.storage=alias
        with self.assertRaises((Blocked,OSError)):self.make()
        self.manager.rt.storage=self.storage
        (self.storage/'native-attach').symlink_to(self.storage,target_is_directory=True)
        with self.assertRaises((Blocked,OSError)):self.make()
    def test_unsafe_storage_and_base_modes_refused(self):
        os.chmod(self.storage,0o777)
        with self.assertRaises(Blocked):self.make()
        os.chmod(self.storage,0o700);(self.storage/'native-attach').mkdir(mode=0o755)
        os.chmod(self.storage/'native-attach',0o755)
        with self.assertRaises(Blocked):self.make()
    def test_v1_and_v2_strict_descriptor_format(self):
        root=self.storage/'native-attach'/'abc';root.mkdir(parents=True)
        path=root/'attach.json';data=dict(version=1,socket=str(root/'pty.sock'),token='fixture',terminal='t',workspace='w',session_id='s',provider='codex',server_pid=123)
        self.assertEqual(descriptor_address(data,path),str(root/'pty.sock'))
        new=dict(data,version=2,socket='df-native-'+'a'*32,token='a'*43)
        self.assertEqual(descriptor_address(new,path),'\0'+new['socket'])
        for bad in (dict(new,version=3),dict(new,extra=True),dict(new,socket='other'),dict(data,socket='/tmp/foreign'),dict(new,server_pid=True),dict(new,token='')):
            with self.subTest(bad=bad),self.assertRaises(Blocked):descriptor_address(bad,path)
    def test_client_rejects_mismatched_server_pid_or_uid(self):
        import struct
        server=self.make()
        class ClientSocket:
            def __init__(self,*args):pass
            def settimeout(self,*args):pass
            def connect(self,*args):pass
            def getsockopt(self,*args):return struct.pack('3i',os.getpid()+1,os.getuid(),0)
            def close(self):pass
        with patch('studio.native_attach.socket.socket',ClientSocket):
            with self.assertRaises(Blocked):AttachClient(server.path)
        class WrongUID(ClientSocket):
            def getsockopt(self,*args):return struct.pack('3i',os.getpid(),os.getuid()+1,0)
        with patch('studio.native_attach.socket.socket',WrongUID):
            with self.assertRaises(Blocked):AttachClient(server.path)
    def test_client_reads_v1_and_v2_descriptors_and_connects_expected_address(self):
        import struct
        server=self.make();root=self.storage/'legacy';root.mkdir(mode=0o700);path=root/'attach.json'
        legacy=dict(version=1,socket=str(root/'pty.sock'),token='legacy',terminal='t',workspace='w',session_id='s',provider='codex',server_pid=os.getpid())
        path.write_text(json.dumps(legacy));os.chmod(path,0o600)
        connected=[]
        class ClientSocket:
            def __init__(self,*args):pass
            def settimeout(self,*args):pass
            def connect(self,address):connected.append(address)
            def getsockopt(self,*args):return struct.pack('3i',os.getpid(),os.getuid(),0)
            def makefile(self,*args):return io.BytesIO()
            def shutdown(self,*args):pass
            def close(self):pass
        with patch('studio.native_attach.socket.socket',ClientSocket),patch('studio.native_attach.receive',side_effect=[dict(ok=True,session_id='s'),dict(ok=True,session_id='synthetic-session')]):
            one=AttachClient(path);one.close();two=AttachClient(server.path);two.close()
        self.assertEqual(connected,[legacy['socket'],'\0'+server.socket_path])
    def test_duplicate_descriptor_fields_rejected_before_connection(self):
        server=self.make();raw=server.path.read_text();server.path.write_text(raw[:-1]+',"version":2}')
        with patch('studio.native_attach.socket.socket') as listener:
            with self.assertRaises(ValueError):AttachClient(server.path)
            listener.assert_not_called()
    def test_server_rejects_bad_hello_and_dead_process(self):
        import struct
        server=self.make();out=[]
        class Stream:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def write(self,data):out.append(data)
            def flush(self):pass
        class Client:
            def getsockopt(self,*args):return struct.pack('3i',os.getpid(),os.getuid(),0)
            def settimeout(self,*args):pass
            def makefile(self,*args):return Stream()
            def close(self):pass
        hello=dict(token=server.secret,terminal='terminal',workspace='workspace',session_id='synthetic-session')
        for change in (dict(token='wrong'),dict(workspace='other'),dict(session_id='other')):
            client=Client();server.clients.add(client)
            with patch('studio.native_attach.receive',return_value=dict(hello,**change)):server._serve(client)
        self.manager.snapshot=lambda *a:dict(session_id='synthetic-session',provider='codex',process_alive=False,state='RUNNING')
        client=Client();server.clients.add(client)
        with patch('studio.native_attach.receive',return_value=hello):server._serve(client)
        self.assertEqual(out,[])
    def test_no_listener_no_fallback_and_cleanup(self):
        with patch('studio.native_attach.socket.socket',side_effect=PermissionError('synthetic denied')):
            with self.assertRaises(PermissionError):NativeAttach(self.manager,'workspace','terminal')
        entries=list((self.storage/'native-attach').iterdir());self.assertEqual(len(entries),1)
        self.assertEqual(list(entries[0].iterdir()),[])
    def test_static_public_manifest_includes_skill_module(self):
        self.assertIn('tools/studio/native_skill_pack.py',PUBLIC_FILES)
        rt=SimpleNamespace(check_operator=lambda:None,candidate_sha256='fixture')
        self.assertIn('Per-call frozen Skill',PublicSourceReader(rt).read('tools/studio/native_skill_pack.py',1,2)['text'])
        self.assertNotIn('tools/studio/private-receipt.json',PUBLIC_FILES)

    def test_original_public_manifest_assertion_without_git_fixture(self):
        from test_public_source_tool import PublicSourceToolTests
        PublicSourceToolTests('test_manifest_covers_shipped_public_modules_without_runtime_discovery').test_manifest_covers_shipped_public_modules_without_runtime_discovery()
