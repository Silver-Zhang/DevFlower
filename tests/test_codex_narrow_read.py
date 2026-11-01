"""Adversarial host reader fixtures, not native model acceptance."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from orchestration.common import Blocked
from studio.codex_narrow_read import NarrowReadScope


class NarrowReadTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.work=self.root/'work';self.work.mkdir()
        self.allowed=self.work/'文件.txt';self.allowed.write_text('SYNTHETIC_ALLOWED 内容')
        self.outside=self.root/'outside';self.outside.write_text('SYNTHETIC_NOT_GRANTED')
        self.explicit=self.root/'explicit';self.explicit.mkdir();(self.explicit/'one').write_text('EXPLICIT')
        self.single=self.root/'single';self.single.write_text('SINGLE')
        self.binding=dict(read_paths=[str(self.explicit),str(self.single)],readonly_files=[])

    def read(self,s,path):return s.execute('devflower_read_file',dict(path=str(path)))
    def text(self,response):return response['contentItems'][0]['text']

    def test_workspace_directory_and_single_explicit_grants(self):
        before=self.allowed.stat()
        with NarrowReadScope(self.binding,self.work) as s:
            for path,expected in [('文件.txt','SYNTHETIC_ALLOWED 内容'),(self.single,'SINGLE')]:
                result=self.read(s,path);self.assertTrue(result['success']);self.assertEqual(json.loads(self.text(result))['text'],expected)
            self.assertFalse(self.read(s,self.explicit/'one')['success'])
            self.assertFalse(s.execute('devflower_list_directory',dict(path=str(self.explicit)))['success'])
            for _ in range(2):
                result=s.execute('devflower_list_directory',dict(path='.'))
                self.assertEqual(json.loads(self.text(result))['entries'],[dict(name='文件.txt',type='file')])
        after=self.allowed.stat();self.assertEqual((before.st_mode,before.st_ino,before.st_mtime_ns),(after.st_mode,after.st_ino,after.st_mtime_ns))

    def test_outside_directory_does_not_override_explicit_child_file(self):
        child=self.explicit/'one';self.binding['read_paths'].append(str(child))
        with NarrowReadScope(self.binding,self.work) as scope:
            self.assertTrue(self.read(scope,child)['success'])
            self.assertFalse(scope.execute('devflower_list_directory',dict(path=str(self.explicit)))['success'])
            self.assertTrue(self.read(scope,'文件.txt')['success'])

    def test_absolute_relative_traversal_proc_and_ungranted_paths_return_no_bytes(self):
        with NarrowReadScope(self.binding,self.work) as s:
            for path in (self.outside,'../outside',self.single.parent,'/proc/self/fd/0','/etc/passwd','file:///etc/passwd','\x00'):
                with self.subTest(path=str(path)):
                    result=self.read(s,path);self.assertFalse(result['success'])
                    self.assertNotIn(str(self.root),self.text(result));self.assertNotIn('NOT_GRANTED',self.text(result))

    def test_symlink_chains_and_hardlinks_cannot_expose_sibling(self):
        (self.work/'link').symlink_to(self.outside);(self.work/'chain').symlink_to(self.work/'link')
        (self.work/'dir-link').symlink_to(self.explicit,target_is_directory=True)
        os.link(self.outside,self.work/'hard')
        with NarrowReadScope(self.binding,self.work) as s:
            for path in ('link','chain','hard','dir-link/one'):
                self.assertFalse(self.read(s,path)['success'])
            entries=json.loads(self.text(s.execute('devflower_list_directory',dict(path='.'))))['entries']
            self.assertEqual([r['name'] for r in entries],['文件.txt'])
        self.assertEqual(self.outside.read_text(),'SYNTHETIC_NOT_GRANTED')

    def test_protected_hidden_authority_and_explicit_secret_source_not_read(self):
        private=self.work/'.codex';private.mkdir();(private/'auth.json').write_text('CREDENTIAL_FIXTURE')
        self.binding['readonly_files']=[(str(self.single),'/run/credential')]
        with self.assertRaises(Blocked):
            with NarrowReadScope(self.binding,self.work):pass
        self.binding['read_paths']=[];self.binding['readonly_files']=[]
        with NarrowReadScope(self.binding,self.work) as s:
            self.assertFalse(self.read(s,private/'auth.json')['success'])

    def test_authorized_root_replacement_aborts_before_returning_content(self):
        with NarrowReadScope(self.binding,self.work) as s:
            self.work.rename(self.root/'old-work');self.work.symlink_to(self.explicit,target_is_directory=True)
            with self.assertRaises(Blocked):self.read(s,'one')

    def test_single_grant_replacement_and_new_hardlink_revoke_access(self):
        with NarrowReadScope(self.binding,self.work) as s:
            self.single.unlink();self.single.write_text('NEW_NOT_AUTHORIZED_INODE')
            with self.assertRaises(Blocked):self.read(s,self.single)
        with NarrowReadScope(self.binding,self.work) as s:
            os.link(self.single,self.root/'new-link')
            with self.assertRaises(Blocked):self.read(s,self.single)

    def test_permission_revocation_callback_and_closed_scope_stop(self):
        revoked=[]
        def check():
            if revoked:raise Blocked('revoked')
        with NarrowReadScope(self.binding,self.work,check=check) as s:
            self.assertTrue(self.read(s,'文件.txt')['success']);revoked.append(True)
            with self.assertRaisesRegex(Blocked,'revoked'):self.read(s,'文件.txt')
        with self.assertRaises(Blocked):self.read(s,'文件.txt')

    def test_binary_invalid_utf8_file_limit_and_mode000_do_not_leak_partial(self):
        for name,raw in [('binary',b'a\0b'),('invalid',b'\xff'),('large',b'x'*16001),('denied',b'denied')]:
            (self.work/name).write_bytes(raw)
        (self.work/'denied').chmod(0)
        self.addCleanup((self.work/'denied').chmod,0o600)
        with NarrowReadScope(self.binding,self.work) as s:
            for name in ('binary','invalid','large','denied'):
                self.assertFalse(self.read(s,name)['success'])

    def test_read_race_changing_inode_is_not_returned(self):
        real=os.pread
        def changed(fd,n,offset):
            raw=real(fd,n,offset);self.allowed.write_text('MODIFIED');return raw
        with NarrowReadScope(self.binding,self.work) as s,patch('studio.codex_narrow_read.os.pread',side_effect=changed):
            result=self.read(s,'文件.txt');self.assertFalse(result['success']);self.assertNotIn('SYNTHETIC_ALLOWED',self.text(result))

    def test_nested_directory_replacement_after_lookup_cannot_return_stale_bytes(self):
        sub=self.work/'sub';sub.mkdir();(sub/'one').write_text('ORIGINAL_APPROVED')
        real=os.open;swapped=[]
        def replace(name,flags,*args,**kwargs):
            fd=real(name,flags,*args,**kwargs)
            if name=='sub' and flags&os.O_PATH and not swapped:
                sub.rename(self.root/'moved');sub.symlink_to(self.explicit,target_is_directory=True);swapped.append(True)
            return fd
        with NarrowReadScope(self.binding,self.work) as s,patch('studio.codex_narrow_read.os.open',side_effect=replace):
            with self.assertRaisesRegex(Blocked,'变化'):self.read(s,'sub/one')
        self.assertEqual(swapped,[True])

    def test_directory_count_and_call_budgets_and_unknown_tools(self):
        with NarrowReadScope(self.binding,self.work) as s:
            with self.assertRaises(Blocked):s.execute('shell',dict(path='文件.txt'))
            for _ in range(63):self.assertTrue(self.read(s,'文件.txt')['success'])
            with self.assertRaises(Blocked):self.read(s,'文件.txt')
        for i in range(201):(self.work/str(i)).touch()
        with NarrowReadScope(self.binding,self.work) as s:
            self.assertFalse(s.execute('devflower_list_directory',dict(path='.'))['success'])

    def test_invalid_arguments_and_public_root_grants_refused(self):
        with NarrowReadScope(self.binding,self.work) as s:
            for args in ({'path':'文件.txt','write':True},[],{'path':False}):
                self.assertFalse(s.execute('devflower_read_file',args)['success'])
        for root in ('/','/tmp','/home'):
            with self.assertRaises(Blocked):NarrowReadScope(dict(read_paths=[root]),self.work)


if __name__=='__main__':unittest.main(verbosity=2)
