"""Public handoffs cannot select or smuggle native/authorization receipts."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from studio.graph import Invalid
from studio.workbench_graph import transfer, request_text


class PublicHandoffTests(unittest.TestCase):
    def test_private_receipt_cannot_be_selected_as_a_field(self):
        with self.assertRaises(Invalid):
            transfer({'receipt': {'session_id': 'private'}}, {'transfer': 'field', 'field': 'receipt'})

    def test_nested_receipt_path_is_rejected(self):
        with self.assertRaises(Invalid):
            transfer({'result': {'receipt': {'secret': 'private'}}},
                     {'transfer': 'field', 'field': 'result.receipt.secret'})

    def test_nested_private_values_do_not_cross_full_result_edge(self):
        output = {'answer': 'public', 'result': {'score': 2, 'receipt': {'sid': 'private'}},
                  'sources': [{'content': {'authorization': 'private', 'answer': 'public'}}]}
        value = transfer(output, {'transfer': 'result'})
        self.assertEqual(value['result'], {'score': 2})
        self.assertEqual(value['sources'][0]['content'], {'answer': 'public'})
        self.assertIn('receipt', output['result'], 'handoff cleaning cannot mutate authoritative outputs')

    def test_credential_shaped_text_is_redacted(self):
        value = transfer({'answer': 'api_key=do-not-forward-this'}, {'transfer': 'answer'})
        self.assertNotIn('do-not-forward-this', value)

    def test_public_structured_field_keeps_json_type(self):
        self.assertIs(transfer({'result': {'accepted': False}},
                               {'transfer': 'field', 'field': 'result.accepted'}), False)

    def test_budget_truncation_remains_explicit(self):
        value = transfer({'answer': 'a' * 200}, {'transfer': 'answer', 'max_chars': 128})
        self.assertTrue(value['truncated'])
        self.assertEqual(value['budget'], 128)

    def test_role_and_background_are_actual_provider_input(self):
        node = dict(mode='Ask', responsibility='VISIBLE-RESPONSIBILITY', background='VISIBLE-BACKGROUND',
                    initial_prompt='INIT', round_prompt='ROUND', handoff_prompt='OUTPUT')
        text = request_text(node, [], first=False)
        self.assertIn('VISIBLE-RESPONSIBILITY', text)
        self.assertIn('VISIBLE-BACKGROUND', text)
        self.assertNotIn('INIT', text)



class DraftWorkspaceOwnershipTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from studio.workbench_store import WorkbenchStore
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=WorkbenchStore(Path(self.tmp.name)/'drafts.sqlite',scoped_drafts=True)
        self.graph={'version':3,'name':'Legacy graph; owner unknown','nodes':[],'edges':[]}
        from orchestration.common import canonical,digest
        self.legacy=dict(id='legacy',revision=digest(canonical(self.graph)),graph=self.graph)
        # Genuine pre-workspace persisted bytes, not a new unscoped API save.
        with self.store.tx() as db:
            raw=canonical(self.legacy).decode()
            db.execute("INSERT INTO records VALUES('draft',?,?)",('legacy',raw))
            db.execute('INSERT INTO drafts VALUES(?,?,?)',('legacy',self.legacy['revision'],raw))
        self.store.put('run','history',dict(id='history',state='COMPLETED',draft_id='legacy'))
        self.store.put('node-session','native-history',dict(id='native-history',session_id='original-native-session'))
    def revisions(self):
        with self.store.tx(read=True) as db:return db.execute('SELECT id,revision,data FROM drafts ORDER BY id,revision').fetchall()
    def test_neither_workspace_can_delete_or_rebind_legacy_original(self):
        from studio.server import Conflict
        original=self.revisions()
        for workspace in ('workspace-A','workspace-B'):
            with self.subTest(workspace=workspace):
                with self.assertRaises(Conflict):self.store.delete_draft('legacy',self.legacy['revision'],workspace)
                with self.assertRaises(Conflict):self.store.save('legacy',self.graph,self.legacy['revision'],workspace)
                self.assertEqual(self.store.get('draft','legacy'),self.legacy)
                self.assertEqual(self.revisions(),original)
        for call in (lambda:self.store.delete_draft('legacy',self.legacy['revision']),lambda:self.store.save('legacy',self.graph,self.legacy['revision'])):
            with self.assertRaises(Invalid):call()
        self.assertEqual(self.store.get('run','history')['state'],'COMPLETED')
        self.assertEqual(self.store.get('node-session','native-history')['session_id'],'original-native-session')
    def test_explicit_new_copies_have_distinct_ownership_and_preserve_legacy_revisions(self):
        from studio.server import Conflict
        old=self.revisions()
        a=self.store.save('copy-A',self.graph,None,'workspace-A');b=self.store.save('copy-B',self.graph,None,'workspace-B')
        self.assertEqual(a['workspace'],'workspace-A');self.assertEqual(b['workspace'],'workspace-B')
        with self.assertRaises(Conflict):self.store.save('copy-A',self.graph,a['revision'],'workspace-B')
        with self.assertRaises(Conflict):self.store.delete_draft('copy-A',a['revision'],'workspace-B')
        with self.assertRaises(Invalid):self.store.save('copy-A',self.graph,a['revision'])
        self.store.delete_draft('copy-A',a['revision'],'workspace-A')
        self.assertEqual(self.store.get('draft','copy-B'),b)
        self.assertEqual(self.store.get('draft','legacy'),self.legacy)
        self.assertEqual([r for r in self.revisions() if r[0]=='legacy'],old)
        self.assertEqual(len(self.revisions()),3,'Deleting a copy must retain every immutable revision')
    def test_workspace_api_store_never_creates_unassigned_drafts(self):
        for workspace in (None,'',[],{}):
            with self.subTest(workspace=workspace),self.assertRaises(Invalid):self.store.save('new',self.graph,None,workspace)
        self.assertEqual(self.store.list('draft'),[self.legacy])

class SQLiteSidecarRaceTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from studio.workbench_store import WorkbenchStore
        self.tmp=tempfile.TemporaryDirectory(prefix='devflower-sqlite-sidecar-test-');self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve();self.path=self.root/'library.sqlite'
        self.store=WorkbenchStore(self.path,scoped_drafts=True)
        self.expected=self.store.save('A',{'name':'retained draft','nodes':[],'edges':[]},None,'synthetic-A')

    def test_real_sidecar_disappearance_during_metadata_check_preserves_draft(self):
        import stat
        from unittest.mock import patch
        from studio.workbench_store import WorkbenchStore
        original=Path.lstat
        for suffix in ('-wal','-shm','-journal'):
            with self.subTest(suffix=suffix):
                sidecar=Path(str(self.path)+suffix)
                self.assertEqual(sidecar.parent,self.root)
                with sidecar.open('xb'):pass
                removed=[]
                def disappear(path,*args,**kwargs):
                    if path==sidecar and not removed:
                        metadata=original(path)
                        self.assertTrue(stat.S_ISREG(metadata.st_mode));self.assertEqual(metadata.st_nlink,1)
                        path.unlink();removed.append(path)
                    return original(path,*args,**kwargs)
                with patch.object(Path,'lstat',disappear):
                    self.assertEqual(WorkbenchStore(self.path).list('draft'),[self.expected])
                self.assertEqual(removed,[sidecar])
                self.assertEqual(self.store.get('draft','A'),self.expected)

    def test_2000_normal_concurrent_opens_and_closes_keep_fixed_draft(self):
        import concurrent.futures
        from studio.workbench_store import WorkbenchStore
        def worker(number):
            errors=[]
            for iteration in range(250):
                try:self.assertEqual(WorkbenchStore(self.path).list('draft'),[self.expected])
                except Exception as exc:errors.append((number,iteration,type(exc).__name__,str(exc)))
            return errors
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            errors=sum(pool.map(worker,range(8)),[])
        self.assertEqual(errors,[])
        self.assertEqual(self.store.get('draft','A'),self.expected)
        with self.store.tx(read=True) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM drafts').fetchone()[0],1)

    def test_database_and_all_sidecars_reject_links_fifo_and_directory_before_connect(self):
        import os,stat,tempfile
        from unittest.mock import patch
        from studio.workbench_store import WorkbenchStore
        for suffix in ('','-wal','-shm','-journal'):
            for kind in ('symlink','hardlink','fifo','directory'):
                with self.subTest(suffix=suffix,kind=kind),tempfile.TemporaryDirectory(dir=self.root) as name:
                    root=Path(name).resolve();database=root/'library.sqlite';node=Path(str(database)+suffix)
                    self.assertEqual(node.parent,root);self.assertTrue(root.is_relative_to(self.root))
                    target=root/'private-regular';target.write_bytes(b'SYNTHETIC unchanged private bytes')
                    metadata=target.lstat();self.assertTrue(stat.S_ISREG(metadata.st_mode));self.assertEqual(metadata.st_nlink,1)
                    if kind=='symlink':node.symlink_to(target)
                    elif kind=='hardlink':os.link(target,node)
                    elif kind=='fifo':os.mkfifo(node)
                    else:node.mkdir()
                    with patch('studio.workbench_store.sqlite3.connect') as connect:
                        with self.assertRaises(Invalid):WorkbenchStore(database)
                        connect.assert_not_called()
                    self.assertEqual(target.read_bytes(),b'SYNTHETIC unchanged private bytes')

    def test_metadata_errors_other_than_missing_file_propagate_without_connect(self):
        import errno
        from unittest.mock import patch
        from studio.workbench_store import WorkbenchStore
        original=Path.lstat
        sidecar=Path(str(self.path)+'-shm');self.assertEqual(sidecar.parent,self.root)
        for error in (PermissionError(errno.EACCES,'synthetic permission failure'),OSError(errno.EIO,'synthetic I/O failure'),NotADirectoryError(errno.ENOTDIR,'synthetic ancestor failure')):
            with self.subTest(error=type(error).__name__):
                def inaccessible(path,*args,**kwargs):
                    if path==sidecar:raise error
                    return original(path,*args,**kwargs)
                with patch.object(Path,'lstat',inaccessible),patch('studio.workbench_store.sqlite3.connect') as connect:
                    with self.assertRaises(type(error)) as raised:WorkbenchStore(self.path)
                    self.assertIs(raised.exception,error);connect.assert_not_called()
        self.assertEqual(self.store.get('draft','A'),self.expected)

    def test_unlinked_inode_snapshot_requires_fresh_single_link_metadata(self):
        import os,stat
        from unittest.mock import patch
        from studio.workbench_store import WorkbenchStore
        sidecar=Path(str(self.path)+'-shm');self.assertEqual(sidecar.parent,self.root)
        with sidecar.open('xb'):pass
        original=Path.lstat;metadata=original(sidecar)
        self.assertTrue(stat.S_ISREG(metadata.st_mode));self.assertEqual(metadata.st_nlink,1)
        values=list(metadata);values[3]=0;unlinked=os.stat_result(values)
        calls=[]
        def transient(path,*args,**kwargs):
            if path==sidecar:
                calls.append(path)
                if len(calls)==1:return unlinked
            return original(path,*args,**kwargs)
        with patch.object(Path,'lstat',transient):
            self.assertEqual(WorkbenchStore(self.path).list('draft'),[self.expected])
        self.assertEqual(len(calls),2)
        def still_unlinked(path,*args,**kwargs):
            return unlinked if path==sidecar else original(path,*args,**kwargs)
        with patch.object(Path,'lstat',still_unlinked),patch('studio.workbench_store.sqlite3.connect') as connect:
            with self.assertRaises(Invalid):WorkbenchStore(self.path)
            connect.assert_not_called()

    def test_absent_new_database_still_initializes_as_regular_single_link(self):
        import stat
        from studio.workbench_store import WorkbenchStore
        path=self.root/'new-library.sqlite';self.assertEqual(path.parent,self.root)
        self.assertFalse(path.exists());self.assertEqual(WorkbenchStore(path).list('draft'),[])
        metadata=path.lstat();self.assertTrue(stat.S_ISREG(metadata.st_mode));self.assertEqual(metadata.st_nlink,1)
        self.assertEqual(stat.S_IMODE(metadata.st_mode),0o600)


if __name__ == '__main__':
    unittest.main()
