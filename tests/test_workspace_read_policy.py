"""Synthetic ignore/sensitive policy regressions for client reader and UI."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from orchestration.common import Blocked
from studio.codex_narrow_read import NarrowReadScope


class WorkspaceReadPolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.work=self.base/'work';self.work.mkdir()
        (self.work/'ordinary.txt').write_text('SYNTHETIC_ORDINARY')
        self.binding=dict(read_paths=[],readonly_files=[])

    def read(self,scope,name):return scope.execute('devflower_read_file',dict(path=str(name)))
    def names(self,scope,path='.'):
        result=scope.execute('devflower_list_directory',dict(path=path));self.assertTrue(result['success'])
        return [r['name'] for r in json.loads(result['contentItems'][0]['text'])['entries']]

    def test_hidden_and_sensitive_files_neither_read_nor_list_by_default(self):
        names=['.env','credentials.txt','auth.json','id_rsa','private.pem','api-key.txt','token.json']
        for name in names:(self.work/name).write_text('SYNTHETIC_SENSITIVE')
        with NarrowReadScope(self.binding,self.work) as s:
            for name in names:self.assertFalse(self.read(s,name)['success'])
            self.assertEqual(self.names(s),['ordinary.txt'])
            self.assertTrue(self.read(s,'ordinary.txt')['success'])

    def test_sensitive_and_credential_single_file_grants_never_override_deny(self):
        for name in ('.env','credentials.txt','private.pem','auth.json'):
            p=self.work/name;p.write_text('SYNTHETIC_SENSITIVE')
            with self.subTest(name=name),self.assertRaises(Blocked):
                with NarrowReadScope(dict(read_paths=[str(p)]),self.work):pass

    def test_root_and_nested_ignore_rules_apply_to_reads_and_parent_lists(self):
        (self.work/'.gitignore').write_text('ignored.txt\ncache/\n**/hidden.txt\n')
        (self.work/'ignored.txt').write_text('SYNTHETIC_IGNORED')
        cache=self.work/'cache';cache.mkdir();(cache/'one').write_text('SYNTHETIC_IGNORED')
        nested=self.work/'nested';nested.mkdir();(nested/'.gitignore').write_text('/one.txt\nchild/*.log\n')
        (nested/'one.txt').write_text('SYNTHETIC_IGNORED');(nested/'hidden.txt').write_text('SYNTHETIC_IGNORED')
        child=nested/'child';child.mkdir();(child/'trace.log').write_text('SYNTHETIC_IGNORED');(child/'code.py').write_text('VISIBLE')
        (self.work/'one.txt').write_text('ROOT_VISIBLE')
        with NarrowReadScope(self.binding,self.work) as s:
            for name in ('ignored.txt','cache/one','nested/one.txt','nested/hidden.txt','nested/child/trace.log'):
                self.assertFalse(self.read(s,name)['success'],name)
            self.assertEqual(self.names(s),['nested','one.txt','ordinary.txt'])
            self.assertEqual(self.names(s,'nested'),['child'])
            self.assertEqual(self.names(s,'nested/child'),['code.py'])
            self.assertTrue(self.read(s,'one.txt')['success'])

    def test_explicit_non_sensitive_single_file_may_override_ignore_but_directory_cannot(self):
        outside=self.base/'external';outside.mkdir();(outside/'.gitignore').write_text('ignored.txt\n');p=outside/'ignored.txt';p.write_text('EXPLICIT_ONLY')
        with NarrowReadScope(dict(read_paths=[str(outside)]),self.work) as s:self.assertFalse(self.read(s,p)['success'])
        with NarrowReadScope(dict(read_paths=[str(outside),str(p)]),self.work) as s:
            self.assertTrue(self.read(s,p)['success']);self.assertFalse(s.execute('devflower_list_directory',dict(path=str(outside)))['success'])

    def test_negated_rules_do_not_silently_authorize_previously_ignored_files(self):
        (self.work/'.gitignore').write_text('*.dat\n!allow.dat\n')
        p=self.work/'allow.dat';p.write_text('EXPLICIT_ONLY')
        with NarrowReadScope(self.binding,self.work) as s:self.assertFalse(self.read(s,p)['success'])
        with NarrowReadScope(dict(read_paths=[str(p)]),self.work) as s:self.assertTrue(self.read(s,p)['success'])

    def test_double_star_matches_zero_or_multiple_directories(self):
        (self.work/'.gitignore').write_text('a/**/b/**/c.txt\n')
        paths=['a/b/c.txt','a/x/b/c.txt','a/b/y/c.txt','a/x/b/y/c.txt']
        for name in paths:
            p=self.work/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('SYNTHETIC_IGNORED')
        with NarrowReadScope(self.binding,self.work) as s:
            for name in paths:self.assertFalse(self.read(s,name)['success'],name)

    def test_non_git_workspace_and_dot_ignore_filter(self):
        self.assertFalse((self.work/'.git').exists())
        (self.work/'.ignore').write_text('*.log\n');(self.work/'trace.log').write_text('SYNTHETIC_IGNORED')
        with NarrowReadScope(self.binding,self.work) as s:
            self.assertFalse(self.read(s,'trace.log')['success']);self.assertEqual(self.names(s),['ordinary.txt'])

    def test_ignore_content_change_atomic_replacement_and_new_rule_abort_scope(self):
        policy=self.work/'.gitignore';policy.write_text('ignored.txt\n')
        with NarrowReadScope(self.binding,self.work) as s:
            policy.write_text('')
            with self.assertRaises(Blocked):self.read(s,'ordinary.txt')
        with NarrowReadScope(self.binding,self.work) as s:
            replacement=self.base/'replacement';replacement.write_text(policy.read_text());replacement.replace(policy)
            with self.assertRaises(Blocked):self.read(s,'ordinary.txt')
        policy.unlink()
        with NarrowReadScope(self.binding,self.work) as s:
            policy.write_text('ordinary.txt\n')
            with self.assertRaises(Blocked):self.read(s,'ordinary.txt')

    def test_unvisited_nested_ignore_cannot_change_after_snapshot(self):
        nested=self.work/'nested';nested.mkdir();policy=nested/'.gitignore';policy.write_text('one\n');(nested/'one').write_text('SYNTHETIC_IGNORED')
        with NarrowReadScope(self.binding,self.work) as s:
            policy.write_text('')
            with self.assertRaises(Blocked):self.read(s,'nested/one')

    def test_unparseable_linked_unreadable_or_large_ignore_fails_closed(self):
        policy=self.work/'.gitignore'
        for content in (b'\\#escaped\n',b'\xff',b'x'*16001):
            policy.write_bytes(content)
            with self.assertRaises(Blocked):
                with NarrowReadScope(self.binding,self.work):pass
        policy.unlink();external=self.base/'outside-policy';external.write_text('SYNTHETIC_OUTSIDE_POLICY')
        policy.symlink_to(external)
        with self.assertRaises(Blocked):
            with NarrowReadScope(self.binding,self.work):pass
        self.assertEqual(external.read_text(),'SYNTHETIC_OUTSIDE_POLICY')

    def test_explicit_file_still_rejects_sensitive_ancestor_in_workspace(self):
        folder=self.work/'secrets';folder.mkdir();p=folder/'ordinary.txt';p.write_text('SYNTHETIC_SENSITIVE')
        with self.assertRaises(Blocked):
            with NarrowReadScope(dict(read_paths=[str(p)]),self.work):pass

    def test_outside_explicit_file_and_directory_cannot_hide_sensitive_or_hidden_parents(self):
        for parent in ('secrets','.hidden','.ssh','.codex','auth'):
            folder=self.base/parent;folder.mkdir();child=folder/'ordinary';child.mkdir();file=child/'normal.txt';file.write_text('SYNTHETIC_PARENT_SENSITIVE')
            for grant in (file,child):
                with self.subTest(parent=parent,grant=str(grant)),self.assertRaises(Blocked):
                    with NarrowReadScope(dict(read_paths=[str(grant)]),self.work):pass

    def test_strong_authentication_directory_cannot_itself_be_the_workspace(self):
        for name in ('.ssh','.gnupg','.aws','.codex'):
            folder=self.base/name;folder.mkdir();(folder/'normal.txt').write_text('SYNTHETIC_AUTH')
            with self.assertRaises(Blocked):NarrowReadScope(dict(read_paths=[]),folder)

    def test_double_leading_slash_cannot_bypass_broad_root_denials(self):
        from studio.session_sources import clean_path
        self.assertEqual(clean_path('//tmp/normal'),'/tmp/normal')
        for grant in ('//','//tmp','//home','///tmp'):
            with self.subTest(grant=grant),self.assertRaises(Blocked):NarrowReadScope(dict(read_paths=[grant]),self.work)
        with NarrowReadScope(self.binding,self.work) as scope:
            self.assertTrue(self.read(scope,'/'+str(self.work/'ordinary.txt'))['success'])

    def test_double_leading_slash_cannot_bypass_protected_workspace_or_grant(self):
        from test_workbench_runtime import fixture
        base,rt=fixture(self)
        # Browsing protected folders is intentionally permitted; running an
        # Agent there is not. Preserve that distinction for both spellings.
        self.assertEqual(rt.readable_directory('/'+str(rt.storage)),str(rt.storage))
        for spelling in (str(rt.storage),'/'+str(rt.storage)):
            with self.assertRaises(Blocked):rt.validate_source(spelling)
        protected=self.base/'protected';protected.mkdir()
        with self.assertRaises(Blocked):NarrowReadScope(dict(read_paths=['/'+str(protected)],native_protected=[str(protected)]),self.work)

    def test_project_browser_uses_same_default_sensitive_and_ignore_filter(self):
        from test_workbench_runtime import fixture
        from studio.workspace_files import listing,preview
        base,rt=fixture(self);root=base.source;workspace=base.project['id']
        (root/'.gitignore').write_text('ignored.txt\n');(root/'ignored.txt').write_text('SYNTHETIC_IGNORED');(root/'.env').write_text('SYNTHETIC_SENSITIVE');(root/'ordinary.txt').write_text('VISIBLE')
        for name in ('ignored.txt','.env'):
            with self.assertRaises(Blocked):preview(rt,workspace,name)
        names=[x['name'] for x in listing(rt,workspace)['entries']]
        self.assertNotIn('ignored.txt',names);self.assertNotIn('.env',names);self.assertIn('ordinary.txt',names)


if __name__=='__main__':unittest.main(verbosity=2)
