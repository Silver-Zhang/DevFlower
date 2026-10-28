"""Host-only AF_UNIX integration; synthetic PTY manager, no provider."""
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import unittest

from orchestration.common import canonical
from studio.native_attach import AttachClient,NativeAttach,receive
from test_native_attach_deadlines import Fixture


class PrivateSocketTests(Fixture):
    def test_long_storage_observe_claim_release_and_close_keeps_process(self):
        storage=self.base/('long-'+'a'*90);storage.mkdir(mode=0o700);self.manager.rt.storage=storage
        server=NativeAttach(self.manager,'w','t');self.addCleanup(server.close)
        client=AttachClient(server.path);self.addCleanup(client.close)
        self.assertEqual(client.request('observe')['input_generation'],0)
        self.assertEqual(client.request('claim',generation=0)['input_generation'],1)
        self.assertEqual(client.request('release',generation=1)['input_owner'],'unclaimed')
        client.close();server.close();server.thread.join(3)
        self.assertFalse(server.thread.is_alive());self.assertTrue(self.row()['process_alive'])
        self.assertEqual(server.path.read_bytes(),b'')

    def test_client_connects_old_v1_file_socket(self):
        with tempfile.TemporaryDirectory(prefix='df-old-attach-',dir='/tmp') as folder:
            root=Path(folder);path=root/'attach.json';sockpath=str(root/'pty.sock')
            listener=socket.socket(socket.AF_UNIX);listener.bind(sockpath);listener.listen(1)
            self.addCleanup(listener.close)
            data=dict(version=1,socket=sockpath,token='old-token',terminal='t',workspace='w',session_id='old',provider='codex',server_pid=os.getpid())
            path.write_bytes(canonical(data));os.chmod(path,0o600);errors=[]
            def serve():
                try:
                    conn,_=listener.accept()
                    with conn,conn.makefile('rwb') as stream:
                        hello=receive(stream)
                        if hello!={k:data[k] for k in ('token','terminal','workspace','session_id')}:raise AssertionError('wrong hello')
                        stream.write(canonical(dict(ok=True,session_id='old',provider='codex',owner='fixture'))+b'\n');stream.flush()
                except Exception as exc:errors.append(exc)
            thread=threading.Thread(target=serve);thread.start()
            try:
                client=AttachClient(path);self.assertEqual(client.hello['session_id'],'old');client.close()
            finally:thread.join(3)
            self.assertFalse(thread.is_alive());self.assertEqual(errors,[])
