"""Templates, read-only foreign archives and exact local package scope."""
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace
from unittest import TestCase, skipUnless
from unittest.mock import Mock, patch
from zipfile import ZipFile

from orchestration.common import Blocked, canonical, digest
from orchestration.sandbox import abi
from studio.graph import Invalid
from studio.server import Conflict
from v2.development_evidence import current_candidate
from v2.git_delivery import KIND as GIT_DELIVERY, GitDelivery, direct_tip
from v2.git_workspace import git_read
from v2.history_archives import HistoryArchives
from v2.local_delivery import package
from v2.recovery import project
from v2.runtime import V2Runtime
from v2.server import create_server
from v2.session_operations import operation_id
from v2.store import DraftStore
from v2 import tool_outputs
from v2.workflow_templates import WorkflowTemplates, starter


class WorkflowPortabilityTests(TestCase):
    def setUp(self):
        self.base = Path(self.enterContext(TemporaryDirectory()))
        self.workspaces = [self.base/name for name in ('A','B')]
        for path in self.workspaces:
            path.mkdir()
        self.store = DraftStore(self.base/'state', self.workspaces)
        self.a, self.b = [self.store.open_workspace(str(path))['id'] for path in self.workspaces]

    @skipUnless(abi() >= 6 or os.environ.get('DEVFLOWER_REQUIRE_BWRAP_VERIFIER') == '1',
                'requires Linux Landlock ABI >= 6')
    def test_workspace_json_output_uses_scoped_path_and_fences_uncertain_effects(self):
        root = self.workspaces[0]
        (root/'nested').mkdir()
        foreign = self.workspaces[1]/'protected.json'
        foreign.write_text('untouched')
        (root/'escape.json').symlink_to(foreign)
        rt = V2Runtime(self.store, str(self.base/'no-provider'))
        graph = dict(version=1, name='Write checked JSON', nodes=[
            dict(id='Start', name='Start', kind='start', position={'x':0, 'y':0}, config_version=1,
                 config={'initial_data':'{"value":7}'}),
            dict(id='Write', name='Write', kind='tool', position={'x':160, 'y':0}, config_version=1,
                 config={'tool':'write-file', 'permission':'workspace-write', 'path':'result.json'}),
            dict(id='End', name='End', kind='output', position={'x':320, 'y':0}, config_version=1,
                 config={'format':'json'}),
        ], edges=[
            dict(id='Data', source='Start', target='Write', source_port='out', target_port='in'),
            dict(id='Receipt', source='Write', target='End', source_port='out', target_port='in'),
        ])

        def finish(saved, intent):
            start = subprocess.Popen
            processes = []

            def track(*args, **kwargs):
                process = start(*args, **kwargs)
                processes.append(process)
                return process

            with patch('v2.runtime.subprocess.Popen', side_effect=track):
                run = rt.launch(self.a, saved['id'], saved['revision'], intent, '')['run']['id']
            try:
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    state = rt.snapshot(self.a, run)
                    if state['state'] in ('COMPLETED', 'FAILED', 'RECOVERY_REQUIRED') and not state['worker_alive']:
                        return run, state
                    time.sleep(.04)
                self.fail('Tool workflow did not settle')
            finally:
                for process in processes:
                    process.wait(timeout=5)

        draft = self.store.save(self.a, None, None, graph)
        run, success = finish(draft, 'write-result-approved')
        file = root/'result.json'
        self.assertEqual(success['state'], 'COMPLETED',
                         [(row['node'], row.get('reason')) for row in self.store.db.list('activation')])
        self.assertEqual(success['run']['actual_calls'], 0)
        self.assertEqual(json.loads(file.read_bytes()), {'value':7})
        written = next(node for node in success['nodes'] if node['node'] == 'Write')
        self.assertTrue(written['tool_output_verified'])
        self.assertEqual(written['tool_output_intent']['sha256'], digest(file.read_bytes()))
        self.assertEqual(rt.launch(self.a,draft['id'],draft['revision'],'write-result-approved','')['run']['id'], run)
        _, duplicate_destination = finish(draft, 'write-result-new-intent')
        self.assertEqual(duplicate_destination['state'], 'FAILED')
        self.assertEqual(json.loads(file.read_bytes()), {'value':7})

        graph['nodes'][1]['config']['path'] = 'nested/result.json'
        nested = self.store.save(self.a, None, None, graph)
        _, nested_result = finish(nested, 'write-result-nested')
        self.assertEqual(nested_result['state'], 'COMPLETED')
        self.assertEqual(json.loads((root/'nested/result.json').read_bytes()), {'value':7})
        graph['nodes'][1]['config']['path'] = 'escape.json'
        escaped = self.store.save(self.a, None, None, graph)
        _, refused = finish(escaped, 'write-result-outside')
        self.assertEqual(refused['state'], 'FAILED')
        self.assertEqual(foreign.read_text(), 'untouched')

        actual = self.store.db.get('run', run)
        definition = self.store.db.get('definition', actual['definition_id'])
        pending = dict(self.store.db.get('activation', written['id']), state='STARTING', effect_uncertain=True)
        pending.pop('tool_output_verified')
        recovered = project(rt, dict(actual, state='RUNNING'), definition, [pending], [])
        self.assertEqual(recovered['state'], 'RECOVERY_REQUIRED')
        self.assertEqual(recovered['provider_outcome'], 'not_sent')
        self.assertFalse(recovered['run']['resume_available'])
        self.assertEqual(recovered['nodes'][0]['state'], 'RECOVERY_REQUIRED')

    @skipUnless(abi() >= 6 or os.environ.get('DEVFLOWER_REQUIRE_BWRAP_VERIFIER') == '1',
                'requires Linux Landlock ABI >= 6')
    def test_workspace_output_helper_refuses_parent_moved_outside(self):
        rt, activation = self.output_activation()
        parent = self.workspaces[0]/'nested'
        outside = self.base/'outside'
        parent.mkdir()
        link = tool_outputs.publish_link

        def move_before_link(*args):
            parent.rename(outside)
            parent.mkdir()
            return link(*args)

        with patch('v2.tool_outputs.publish_link', side_effect=move_before_link):
            with self.assertRaisesRegex(Blocked, '隔离写入拒绝'):
                tool_outputs.write_result(rt, 'tool-run', activation, {'path':'nested/result.json'}, {'value':7})
        self.assertFalse((outside/'result.json').exists())
        self.assertFalse((parent/'result.json').exists())
        row = self.store.db.get('activation', activation['id'])
        self.assertFalse(row['effect_uncertain'])
        self.assertEqual(row['tool_output_intent']['path'], 'nested/result.json')

    @skipUnless(abi() >= 6 or os.environ.get('DEVFLOWER_REQUIRE_BWRAP_VERIFIER') == '1',
                'requires Linux Landlock ABI >= 6')
    def test_workspace_output_helper_preserves_concurrent_file(self):
        rt, activation = self.output_activation()
        file = self.workspaces[0]/'result.json'
        link = tool_outputs.publish_link

        def create_before_link(*args):
            file.write_text('other writer')
            return link(*args)

        with patch('v2.tool_outputs.publish_link', side_effect=create_before_link):
            with self.assertRaisesRegex(Conflict, '其他写者'):
                tool_outputs.write_result(rt, 'tool-run', activation, {'path':'result.json'}, {'value':7})
        self.assertEqual(file.read_text(), 'other writer')
        self.assertFalse(self.store.db.get('activation', activation['id'])['effect_uncertain'])

    def test_workspace_output_has_no_unisolated_fallback(self):
        rt, activation = self.output_activation()
        with patch('v2.tool_outputs.subprocess.run', side_effect=FileNotFoundError):
            with self.assertRaisesRegex(Blocked, 'Landlock'):
                tool_outputs.write_result(rt, 'tool-run', activation, {'path':'result.json'}, {'value':7})
        self.assertFalse((self.workspaces[0]/'result.json').exists())
        row = self.store.db.get('activation', activation['id'])
        self.assertFalse(row['effect_uncertain'])
        self.assertEqual(row['tool_output_intent']['path'], 'result.json')

    def test_workspace_output_unknown_link_result_stays_reserved(self):
        rt, activation = self.output_activation()
        with patch('v2.tool_outputs.publish_link', return_value=-9) as link:
            with self.assertRaisesRegex(Blocked, '未能核验'):
                tool_outputs.write_result(rt, 'tool-run', activation, {'path':'result.json'}, {'value':7})
            with self.assertRaisesRegex(Conflict, '已有记录'):
                tool_outputs.write_result(rt, 'tool-run', activation, {'path':'result.json'}, {'value':7})
            link.assert_called_once()
        self.assertTrue(self.store.db.get('activation', activation['id'])['effect_uncertain'])

    def output_activation(self):
        self.store.db.put('run', 'tool-run', {'id':'tool-run', 'workspace':self.a, 'state':'RUNNING'})
        activation = {'id':'tool-write', 'run':'tool-run', 'state':'STARTING'}
        self.store.db.put('activation', activation['id'], activation)
        return SimpleNamespace(store=self.store, flow=self.store.db, check_operator=lambda:None), activation

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

    def test_local_git_delivery_requires_confirmation_and_recovers_by_readback(self):
        root = self.workspaces[0]
        bare = self.base/'delivery.git'

        def git(folder, *args):
            return subprocess.check_output(['/usr/bin/git','-C',str(folder),*args],
                                           stderr=subprocess.DEVNULL, text=True, umask=0o077).strip()

        git(root, 'init', '-q')
        git(root, '-c','user.email=fixture@example.invalid','-c','user.name=Fixture','commit','--allow-empty','-qm','base')
        git(self.base, 'init', '-q', '--bare', str(bare))
        git(root, 'push', str(bare), 'HEAD:refs/heads/main')
        base = git(root, 'rev-parse', 'HEAD')
        (root/'app.py').write_text('print("approved")\n')
        identity = current_candidate(root)
        run, activation = 'a'*32, 'b'*64
        (self.store.state/'flow-artifacts'/run).mkdir(parents=True)
        self.store.db.put('run', run, {'created_at': time.time()})
        status = dict(state='LOCAL_GATE_ACCEPTED',run=run,gate_activation=activation,
                      gate=dict(**identity,verification_sha256='c'*64))
        delivery = GitDelivery(SimpleNamespace(store=self.store, delivery=Mock(return_value=status)),
                               ['fixture='+str(bare)])
        data = dict(workspace=self.a, run=run, activation=activation, candidate_sha256=identity['candidate_sha256'],
                    target='fixture', intent_key='approved-git-delivery', authorization='confirm-local-git-delivery')
        ref = 'refs/heads/work/devflower-'+run
        with self.assertRaises(Invalid):
            delivery.publish(dict(data,authorization='none'))
        with self.assertRaises(Conflict):
            delivery.publish(dict(data,candidate_sha256='d'*64))
        self.assertIsNone(direct_tip(bare,ref))

        updates = []

        def lost_reply(path, args, **options):
            result = git_read(path,args,**options)
            if 'update-ref' in args:
                updates.append(args)
                raise TimeoutError('lost local ref acknowledgement')
            return result

        with patch('v2.git_delivery.git_read', side_effect=lost_reply):
            completed = delivery.publish(data)
            self.assertEqual(delivery.publish(data), completed)
            self.assertEqual(len(updates),1)
        self.assertEqual(completed['state'],'COMPLETED')
        self.assertEqual(direct_tip(bare,ref),completed['commit'])
        self.assertEqual(git(bare, 'rev-parse', completed['commit']+'^'),base)
        self.assertEqual(git(bare, 'show', completed['commit']+':app.py'),'print("approved")')
        self.assertEqual(git(bare, 'rev-parse', 'refs/heads/main'),base)
        self.assertEqual(git(root, 'rev-parse', 'HEAD'),base)
        with self.assertRaises(Conflict):
            delivery.publish(dict(data,intent_key='second-git-delivery'))

        next_run = 'e'*32
        next_ref = 'refs/heads/work/devflower-'+next_run
        next_data = dict(data, run=next_run, intent_key='swapped-target-delivery')
        status['run'] = next_run
        self.store.db.put('run', next_run, {'created_at': time.time()})
        (self.store.state/'flow-artifacts'/next_run).mkdir()
        substitute = self.base/'substitute.git'
        displaced = self.base/'enrolled.git'
        git(self.base, 'init', '-q', '--bare', str(substitute))
        git(root, 'push', str(substitute), 'HEAD:refs/heads/main')

        def swap_path(path, args, **options):
            if 'update-ref' in args:
                bare.rename(displaced)
                substitute.rename(bare)
            return git_read(path,args,**options)

        with patch('v2.git_delivery.git_read', side_effect=swap_path):
            self.assertEqual(delivery.publish(next_data)['state'],'RECOVERY_REQUIRED')
        self.assertIsNone(direct_tip(bare,next_ref))
        self.assertIsNotNone(direct_tip(displaced,next_ref))
        bare.rename(substitute)
        displaced.rename(bare)
        self.assertEqual(delivery.read(self.a,next_data['intent_key'])['state'],'COMPLETED')

        phantom_ref = 'refs/heads/work/devflower-'+'f'*32
        git(bare, 'push', str(substitute), completed['ref']+':'+phantom_ref)
        prior = self.store.db.get(GIT_DELIVERY, operation_id(self.a,data['intent_key']))
        probe_intent = 'forged-ref-readback'
        probe_id = operation_id(self.a,probe_intent)
        self.store.db.put(GIT_DELIVERY,probe_id,dict(prior,id=probe_id,ref=phantom_ref,state='PUBLISHING'))

        def spoof_readback(path, args, **options):
            if 'symbolic-ref' in args and phantom_ref in args:
                bare.rename(displaced)
                substitute.rename(bare)
            return git_read(path,args,**options)

        with patch('v2.git_delivery.git_read', side_effect=spoof_readback):
            self.assertEqual(delivery.read(self.a,probe_intent)['state'],'RECOVERY_REQUIRED')
        self.assertIsNone(direct_tip(displaced,phantom_ref))
        self.assertEqual(direct_tip(bare,phantom_ref),completed['commit'])

    def test_local_git_delivery_refuses_parent_discovery_and_native_home(self):
        parent = self.base/'parent.git'
        subprocess.run(['/usr/bin/git','init','-q','--bare',str(parent)],check=True,umask=0o077)
        child = parent/'child'
        (child/'objects').mkdir(parents=True,mode=0o700)
        (child/'refs').mkdir(mode=0o700)
        child.chmod(0o700)
        (child/'config').write_bytes((parent/'config').read_bytes())
        with self.assertRaises(Blocked):
            GitDelivery(SimpleNamespace(store=self.store), ['bad='+str(child)])

        home = self.base/'account'
        config = home/'.codex'
        home.mkdir(mode=0o700)
        config.mkdir(mode=0o700)
        settings = config/'config.toml'
        settings.write_text('model="fixture"\ncli_auth_credentials_store="file"\n')
        settings.chmod(0o600)
        nested = config/'destination.git'
        subprocess.run(['/usr/bin/git','init','-q','--bare',str(nested)],check=True,umask=0o077)
        runtime = V2Runtime(self.store, str(self.base/'no-provider'), native_homes=['codex:account='+str(home)])
        with self.assertRaisesRegex(Invalid,'原生账户'):
            create_server(self.store, runtime=runtime, git_delivery_targets=['bad='+str(nested)])

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
