"""Templates, read-only foreign archives and exact local package scope."""
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase, skipUnless
from unittest.mock import Mock, patch
from zipfile import ZipFile

from orchestration.common import Blocked, canonical, digest
from studio.graph import Invalid
from studio.server import Conflict
from v2.development_evidence import current_candidate
from v2.history_archives import HistoryArchives
from v2.local_delivery import package
from v2.store import DraftStore
from v2.workflow_templates import WorkflowTemplates, starter


class WorkflowPortabilityTests(TestCase):
    def setUp(self):
        self.base = Path(self.enterContext(TemporaryDirectory()))
        self.workspaces = [self.base/name for name in ('A','B')]
        for path in self.workspaces:
            path.mkdir()
        self.store = DraftStore(self.base/'state', self.workspaces)
        self.a, self.b = [self.store.open_workspace(str(path))['id'] for path in self.workspaces]

    def test_templates_preserve_placement_rebind_cas_and_restore(self):
        library = WorkflowTemplates(self.store)
        graph = starter()
        graph['nodes'][1].update(agent_id='a'*32,agent_revision='b'*64)
        request = dict(workspace=self.a,document=graph,name='Reusable flow',description='search term',id=None,expected=None)
        saved = library.save(request)
        row = library.get(self.a,saved['id'],saved['revision'])
        node = row['graph']['nodes'][1]
        self.assertEqual(node['position'],graph['nodes'][1]['position'])
        self.assertNotIn('agent_id',node)
        self.assertNotIn('agent_revision',node)
        self.assertEqual(row['graph']['edges'],graph['edges'])
        self.assertNotIn('graph',library.listing(self.a)['templates'][0])
        self.assertTrue(library.listing(self.a)['examples'])
        with self.assertRaises(Conflict):
            library.get(self.b,saved['id'],saved['revision'])
        draft = self.store.save(self.a,None,None,row['graph'])
        draft['graph']['name'] = 'Derivative'
        self.store.save(self.a,draft['id'],draft['revision'],draft['graph'])
        self.assertEqual(library.get(self.a,saved['id'],saved['revision'])['name'],'Reusable flow')
        metadata = dict(workspace=self.a,id=saved['id'],expected=saved['revision'])
        trashed = library.manage(dict(metadata,operation='trash'))
        with self.assertRaises(Conflict):
            library.manage(dict(metadata,operation='restore'))
        with self.assertRaises(Conflict):
            library.get(self.a,saved['id'],trashed['revision'])
        restored = library.manage(dict(metadata,operation='restore',expected=trashed['revision']))
        self.assertEqual(library.get(self.a,saved['id'],restored['revision'])['graph'],row['graph'])
        self.assertEqual(len(self.store.db.list('workflow-template-version')),1)
        self.assertEqual(self.store.db.list('run'),[])

    def test_archive_checks_digest_and_never_registers_live_authority(self):
        archives = HistoryArchives(self.store)
        run = 'b'*32
        definition = dict(workspace=self.a,graph_sha256='c'*64)
        definition['id'] = digest(canonical(definition))
        self.store.db.put('definition',definition['id'],definition)
        original = dict(id=run,workspace=self.a,definition_id=definition['id'],state='COMPLETED',
                        output={'answer':'archived output'})
        self.store.db.put('run',run,original)
        raw = archives.export(self.a,run)
        outer = json.loads(raw)
        outer['detail']['run']['output']['answer'] = 'altered'
        with self.assertRaisesRegex(Invalid,'归档校验失败'):
            archives.import_file(self.b,canonical(outer))
        first = archives.import_file(self.b,raw)
        self.assertEqual(archives.import_file(self.b,raw),first)
        self.assertFalse(first['authority'])
        self.assertEqual(first['source_workspace'],self.a)
        self.assertEqual(archives.listing(self.b)['archives'],[first])
        self.assertEqual(archives.read(self.b,first['id'])['detail']['run']['output'],original['output'])
        with self.assertRaises(Conflict):
            archives.read(self.a,first['id'])
        # A matching digest proves file integrity only. Private fields are still filtered.
        outer['detail']['run']['output']['api_token'] = 'private-fixture-literal'
        outer.pop('sha256')
        outer['sha256'] = digest(canonical(outer))
        imported = archives.import_file(self.b,canonical(outer))
        self.assertNotIn('private-fixture-literal',str(archives.read(self.b,imported['id'])))
        self.assertEqual(self.store.db.list('run'),[original])
        self.assertEqual(self.store.db.list('node-session'),[])

    @skipUnless(os.environ.get('DEVFLOWER_REQUIRE_BWRAP_VERIFIER')=='1','requires Linux Bubblewrap')
    def test_owned_sandbox_process_observes_explicit_cancellation(self):
        from orchestration.process import execute

        root = self.workspaces[0]
        entry = root/'check.py'
        entry.write_text('import time\nprint("process-started", flush=True)\ntime.sleep(12)\n')
        chunks = []
        facts, _, _ = execute(['/usr/bin/python3','-I','-B','check.py'],root,self.base/'sandbox-home',
            read=[str(root)],timeout=8,backend='bwrap',deny_network=True,
            on_stdout=chunks.append,cancel_check=lambda: bool(chunks))
        self.assertIn(b'process-started',b''.join(chunks))
        self.assertTrue(facts['cancelled'])
        self.assertFalse(facts['timeout'])

    def test_local_package_requires_confirmation_and_exact_current_candidate(self):
        root = self.workspaces[0]
        def git(*args):
            subprocess.run(['/usr/bin/git','-C',str(root),*args],check=True,stdout=subprocess.DEVNULL,
                           stderr=subprocess.PIPE)
        git('init','-q')
        git('-c','user.email=fixture@example.invalid','-c','user.name=Fixture','commit','--allow-empty','-qm','fixture')
        source = root/'app.txt'
        source.write_text('disposable candidate')
        identity = current_candidate(root)
        run, activation = '1'*32, '2'*64
        (self.store.state/'flow-artifacts'/run).mkdir(parents=True)
        status = dict(state='LOCAL_GATE_ACCEPTED',run=run,gate_activation=activation,
                      gate=dict(**identity,verification_sha256='3'*64))
        platform = SimpleNamespace(store=self.store,delivery=Mock(return_value=status))
        data = dict(workspace=self.a,run=run,activation=activation,candidate_sha256=identity['candidate_sha256'],
                    authorization='confirm-local-candidate-download')
        with self.assertRaises(Invalid):
            package(platform,dict(data,authorization='none'))
        platform.delivery.assert_not_called()
        with self.assertRaises(Conflict):
            package(platform,dict(data,activation='4'*64))
        with patch('v2.local_delivery.task_literals',return_value={'must-not-be-packaged'}):
            archive = package(platform,data)
            self.assertEqual(package(platform,data),archive)
            with ZipFile(BytesIO(archive)) as files:
                self.assertEqual(sorted(files.namelist()),['candidate/app.txt','devflower-delivery.json'])
                self.assertTrue(all(member.date_time == (1980, 1, 1, 0, 0, 0) for member in files.infolist()))
                manifest = json.loads(files.read('devflower-delivery.json'))
                self.assertEqual(manifest['candidate_sha256'],identity['candidate_sha256'])
                self.assertEqual(manifest['gate_activation'],activation)
                self.assertEqual(manifest['entries'][0]['sha256'],digest(files.read('candidate/app.txt')))
            source.write_text('new bytes')
            with self.assertRaises(Conflict):
                package(platform,data)
            source.write_text('must-not-be-packaged')
            updated = current_candidate(root)
            status['gate'].update(updated)
            with self.assertRaisesRegex(Blocked,'受保护内容'):
                package(platform,dict(data,candidate_sha256=updated['candidate_sha256']))
        self.assertEqual(list((self.store.state/'flow-artifacts'/run).iterdir()),[])
