"""Synthetic file errors stay informative, read-only and path-private."""
import errno,json,stat,threading,unittest
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from unittest.mock import patch
from test_workbench_runtime import fixture
from studio import workspace_files
from studio.session_sources import Refused
from studio.workbench_server import create_server
from orchestration.common import Blocked

class ProjectReadPermissionTests(unittest.TestCase):
 def setUp(self):
  self.base,self.rt=fixture(self);self.w=self.base.project['id'];self.denied=self.base.source/'denied.txt';self.denied.write_text('SYNTHETIC DENIED CONTENT');self.denied.chmod(0);self.normal=self.base.source/'normal.txt';self.normal.write_text('SYNTHETIC NORMAL CONTENT');self.directory=self.base.source/'unreadable-dir';self.directory.mkdir();self.directory.chmod(0)
  self.addCleanup(lambda:self.denied.chmod(0o600) if self.denied.exists() else None);self.addCleanup(lambda:self.directory.chmod(0o700) if self.directory.exists() else None)
 def test_denied_preview_explains_read_permission_without_changing_file(self):
  before=self.denied.stat()
  with self.assertRaisesRegex(PermissionError,'无法读取.*文件.*读取权限'):workspace_files.preview(self.rt,self.w,'denied.txt')
  after=self.denied.stat();self.assertEqual(stat.S_IMODE(after.st_mode),0);self.assertEqual((after.st_ino,after.st_size,after.st_mtime_ns),(before.st_ino,before.st_size,before.st_mtime_ns));self.assertEqual(workspace_files.preview(self.rt,self.w,'normal.txt')['text'],'SYNTHETIC NORMAL CONTENT')
 def test_denied_directory_has_specific_recovery_message(self):
  with self.assertRaisesRegex(PermissionError,'无法读取.*文件夹.*目录'):workspace_files.listing(self.rt,self.w,'unreadable-dir')
  self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode),0)
 def test_os_error_filename_is_not_exported_in_message(self):
  with patch.object(workspace_files,'opened',side_effect=PermissionError(errno.EACCES,'synthetic denied','/synthetic/private/DO_NOT_EXPOSE')):
   try:workspace_files.preview(self.rt,self.w,'normal.txt')
   except PermissionError as e:self.assertIn('读取权限',str(e));self.assertNotIn('DO_NOT_EXPOSE',str(e));self.assertNotIn('/synthetic/private',str(e))
   else:self.fail('PermissionError swallowed')
 def test_http_returns_nonempty_403_and_readable_preview_recovers(self):
  server,token=create_server(self.rt);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  def call(path,auth=token):
   req=Request('http://127.0.0.1:'+str(server.server_port)+path,headers={'Authorization':'Bearer '+auth})
   try:
    with urlopen(req,timeout=10) as response:return response.status,json.load(response)
   except HTTPError as response:
    with response:return response.code,json.load(response)
  try:
   for path,label in [('denied.txt','文件'),('unreadable-dir','文件夹')]:
    route='/api/project-file' if label=='文件' else '/api/project-files';code,value=call(route+'?workspace='+self.w+'&path='+path);self.assertEqual(code,403);self.assertIn('无法读取',value['error']);self.assertIn('读取权限',value['error']);self.assertNotIn(str(self.base.source),value['error']);self.assertNotIn('SYNTHETIC DENIED CONTENT',json.dumps(value))
   code,value=call('/api/project-file?workspace='+self.w+'&path=normal.txt');self.assertEqual(code,200);self.assertEqual(value['text'],'SYNTHETIC NORMAL CONTENT');self.assertTrue(value['readonly']);self.assertEqual(stat.S_IMODE(self.denied.stat().st_mode),0)
   code,value=call('/api/project-file?workspace='+self.w+'&path=normal.txt',auth='SYNTHETIC WRONG CAPABILITY');self.assertEqual(code,403);self.assertIn('本机启动入口',value['error']);self.assertNotIn('文件夹',value['error'])
  finally:server.shutdown();server.server_close();thread.join(timeout=3)
 def test_escape_and_symlink_refusals_remain(self):
  outside=self.base.root/'outside.txt';outside.write_text('SYNTHETIC OUTSIDE');(self.base.source/'linked.txt').symlink_to(outside)
  for value in ['../outside.txt','linked.txt']:
   with self.subTest(value=value),self.assertRaises((Blocked,OSError,Refused)):workspace_files.preview(self.rt,self.w,value)
  self.assertEqual(outside.read_text(),'SYNTHETIC OUTSIDE')

if __name__=='__main__':unittest.main(verbosity=2)
