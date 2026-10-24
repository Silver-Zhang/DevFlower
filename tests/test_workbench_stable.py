"""Stable entry boundaries. All approval/receipt bytes here are SYNTHETIC fixtures.

These checks never issue real release acceptance or invoke a native model.
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from orchestration.common import Blocked, canonical, digest, manifest, save, tree_hash
from studio.session_runtime import SessionRuntime
from studio.workbench_runtime import WorkbenchRuntime
from studio.workbench_server import create_server

ROOT = Path(__file__).resolve().parents[1]


class StableFixture:
    """Private temporary test inputs; no actual release approval is produced."""
    def __init__(self, owner):
        tmp = tempfile.TemporaryDirectory(prefix='SYNTHETIC-stable-entry-')
        owner.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.workspace = self.base / 'workspace'; self.workspace.mkdir()
        self.record = self.base / 'SYNTHETIC-NOT-A-RELEASE.json'
        self.operator_file = self.base / 'operator.json'
        self.policy_file = self.base / 'policy.json'
        executable = str(Path('/usr/bin/true').resolve())
        self.operator = dict(version=1, kind='development-sessions',
            storage=str(self.base / 'storage'), native=str(self.base / 'native'),
            allowed_roots=[str(self.workspace)], profiles={'fixture':dict(
                provider='codex', executable=executable, model='SYNTHETIC', effort='high',
                read_paths=[executable], readonly_files=[])},
            dependencies={executable:digest(Path(executable).read_bytes())})
        save(self.policy_file, dict(version=1, models={}, external_steps={}))
        self.candidate = tree_hash(ROOT)

    def approval(self, **changes):
        from stable_provenance_fixture import SyntheticProvenance
        self.evidence = SyntheticProvenance(self, ROOT, changes)
        return self.evidence.data

    def write_operator(self):
        save(self.operator_file, self.operator)
        return digest(self.operator_file.read_bytes())

    def launch(self):
        return WorkbenchRuntime(self.operator_file, self.write_operator(), self.candidate,
            self.policy_file, digest(self.policy_file.read_bytes()))


def serve(owner, rt):
    server, token = create_server(rt)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    owner.addCleanup(thread.join); owner.addCleanup(server.server_close); owner.addCleanup(server.shutdown)
    return 'http://127.0.0.1:'+str(server.server_port), token


class StableEntryTests(unittest.TestCase):
    def request(self, url, token, path, data=None):
        request = Request(url+path, headers={'Authorization':'Bearer '+token,
            'Content-Type':'application/json'}, data=canonical(data) if data is not None else None)
        try:
            with urlopen(request) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            return exc.code, json.load(exc)

    def test_externally_pinned_workbench_can_launch(self):
        f = StableFixture(self); f.approval(); rt = f.launch()
        self.assertEqual(rt.release_identity()['channel'], 'Stable')
        self.assertEqual(rt.trust.binding(), f.operator['stable'])
        self.assertIn(f.record, rt.protected)

    def test_http_branding_comes_from_verified_identity(self):
        f = StableFixture(self); f.approval(); rt = f.launch(); url, token = serve(self, rt)
        with urlopen(Request(url+'/api/status', headers={'Authorization':'Bearer '+token})) as response:
            status = json.load(response)
        self.assertTrue(status['stable_approved'])
        self.assertEqual(status['label'], 'Stable · Agent 工作台')
        self.assertEqual(status['release']['candidate_sha256'], f.candidate)
        self.assertNotIn(str(f.record), json.dumps(status))
        self.assertNotIn('approval_ref', json.dumps(status))

    def test_unapproved_and_operator_labels_cannot_promote_development(self):
        f = StableFixture(self)
        save(f.policy_file, dict(version=1, label='Stable', models={}, external_steps={}))
        rt = f.launch(); url, token = serve(self, rt)
        code, status = self.request(url, token, '/api/status')
        self.assertEqual(code, 200); self.assertFalse(status['stable_approved'])
        self.assertEqual(status['release']['channel'], 'Development')
        self.assertEqual(status['label'], 'Development · Agent 工作台')
        # No promotion/approval HTTP operation is added, even on the pinned runtime.
        for path in ('/api/promote', '/api/approve-release'):
            self.assertEqual(self.request(url, token, path, {'stable_approved':True})[0], 400)

    def test_invalid_or_forged_binding_is_refused_before_storage(self):
        for binding in (True, None, {}, {'record':'/tmp/forged','sha256':'f'*64},
                {'record':42,'sha256':'a'*64}, {'record':'/tmp/forged','sha256':42}):
            with self.subTest(binding=binding):
                f = StableFixture(self); f.operator['stable'] = binding
                with self.assertRaises((Blocked, OSError, ValueError)):
                    f.launch()
                self.assertFalse(Path(f.operator['storage']).exists())
                self.assertFalse(Path(f.operator['native']).exists())
        for key in ('stable_approved', 'release', 'review_verdict'):
            f = StableFixture(self); f.operator[key] = True
            with self.subTest(key=key), self.assertRaises(Blocked): f.launch()
            self.assertFalse(Path(f.operator['storage']).exists())

    def test_wrong_root_candidate_manifest_receipt_or_hash_is_refused(self):
        mutations = [dict(root='/tmp/forged-stable'), dict(candidate_sha256='0'*64),
            dict(files=[]), dict(review_verdict='NEEDS_CORRECTION'),
            dict(review_receipt_sha256=''), dict(approval_ref='')]
        for changes in mutations:
            with self.subTest(changes=changes):
                f = StableFixture(self); f.approval(**changes)
                with self.assertRaises(Blocked): f.launch()
                self.assertFalse(Path(f.operator['storage']).exists())
        f = StableFixture(self); f.approval(); f.operator['stable']['sha256'] = '0'*64
        with self.assertRaises(Blocked): f.launch()
        self.assertFalse(Path(f.operator['storage']).exists())
        # A valid receipt altered after its external pin cannot be substituted.
        f = StableFixture(self); value = f.approval()
        value['review_receipt_sha256'] = '9'*64; save(f.record, value)
        with self.assertRaises(Blocked): f.launch()

    def test_workspace_storage_native_authority_overlap_is_refused(self):
        for location in ('workspace', 'storage', 'native', 'authority'):
            with self.subTest(location=location):
                f = StableFixture(self)
                if location == 'authority':
                    # operator itself cannot double as an approval record.
                    f.record = f.operator_file
                else:
                    parent = f.workspace if location == 'workspace' else Path(f.operator[location])
                    parent.mkdir(exist_ok=True); f.record = parent/'fixture.json'
                f.approval()
                with self.assertRaises(Blocked): f.launch()
                self.assertFalse((Path(f.operator['storage'])/'usable.sqlite').exists())
        # A parent writable root exposes both the pinned approval and code.
        for writable in (str(ROOT.parent),):
            f = StableFixture(self); f.approval(); f.operator['allowed_roots'] = [writable]
            with self.assertRaises(Blocked): f.launch()
        f = StableFixture(self); f.approval(); f.operator['allowed_roots'] = [str(f.base)]
        with self.assertRaises(Blocked): f.launch()

    def test_policy_and_read_mount_overlap_are_refused_before_storage(self):
        f = StableFixture(self); f.approval()
        f.operator['storage'] = str(f.policy_file.parent)
        with self.assertRaises(Blocked): f.launch()
        f = StableFixture(self); f.approval()
        f.operator['profiles']['fixture']['read_paths'].append(str(f.record.parent))
        f.operator['dependency_trees'] = {str(f.record.parent):tree_hash(f.record.parent)}
        with self.assertRaises(Blocked): f.launch()
        self.assertFalse(Path(f.operator['storage']).exists())

    def test_record_symlink_hardlink_or_symlink_parent_is_refused(self):
        for kind in ('symlink', 'hardlink', 'parent'):
            with self.subTest(kind=kind):
                f = StableFixture(self); f.approval(); alias = f.base/'alias'
                if kind == 'parent':
                    alias.symlink_to(f.base, target_is_directory=True)
                    f.operator['stable']['record'] = str(alias/f.record.name)
                else:
                    if kind == 'symlink': alias.symlink_to(f.record)
                    else: os.link(f.record, alias)
                    f.operator['stable']['record'] = str(alias)
                with self.assertRaises((Blocked, ValueError, OSError)): f.launch()
                self.assertFalse(Path(f.operator['storage']).exists())

    def test_session_only_entry_rejects_stable(self):
        f = StableFixture(self); f.approval()
        with self.assertRaises(Blocked):
            SessionRuntime(f.operator_file, f.write_operator(), f.candidate)
        self.assertFalse(Path(f.operator['storage']).exists())

    def test_tampering_blocks_http_queries_and_both_worker_launches(self):
        f = StableFixture(self); f.approval(); rt = f.launch(); url, token = serve(self, rt)
        f.record.write_bytes(f.record.read_bytes()+b' ')
        for path, data in (('/api/status', None), ('/api/folders', None),
                ('/api/start', {'id':'fixture','sha256':'a'*64,'answer':'SYNTHETIC'}),
                ('/api/chat/start', {'id':'fixture','sha256':'a'*64,'answer':'SYNTHETIC'})):
            with self.subTest(path=path): self.assertEqual(self.request(url, token, path, data)[0], 400)
        with patch('studio.workbench_runtime.subprocess.Popen') as spawn:
            with self.assertRaises(Blocked): rt.spawn('fixture')
            with self.assertRaises(Blocked): rt.session_center._spawn('fixture')
            spawn.assert_not_called()
        with self.assertRaises(Blocked): rt.release_identity()

    def test_worker_commands_reconstruct_the_exact_bound_workbench(self):
        f = StableFixture(self); f.approval(); rt = f.launch()
        rt.flow.put('run','fixture',{'id':'fixture'})
        with patch('studio.workbench_runtime.subprocess.Popen') as spawn:
            spawn.return_value.pid = os.getpid(); rt.spawn('fixture')
            flow_argv = spawn.call_args.args[0]
        with patch('studio.session_center.subprocess.Popen') as spawn:
            rt.session_center._spawn('fixture'); session_argv = spawn.call_args.args[0]
        # Execute the actual entry scripts in fresh interpreters, stopping only
        # at the provider/scheduler boundary after real trust verification.
        harness = '''import json,runpy,sys
sys.path.insert(0,sys.argv[1]); script=sys.argv[2]; sys.argv=[script,*sys.argv[3:]]
from studio.session_center import SessionCenter
from studio.workbench_scheduler import Scheduler
def report(rt):
    print(json.dumps(dict(binding=rt.trust.binding(),candidate=rt.candidate_sha256,policy=rt.policy_sha,
        protected=str(rt.trust.record) in [str(p) for p in rt.protected])))
SessionCenter.execute=lambda self,key:report(self.runtime)
Scheduler.run_graph=lambda self:report(self.rt)
runpy.run_path(script,run_name='__main__')
'''
        for argv in (flow_argv, session_argv):
            with self.subTest(worker=argv[2]):
                child = subprocess.run([sys.executable, '-B', '-c', harness, str(ROOT/'tools'),
                    *argv[2:]], capture_output=True, text=True)
                self.assertEqual(child.returncode, 0, child.stderr)
                result = json.loads(child.stdout)
                self.assertEqual(result['binding'], f.operator['stable'])
                self.assertEqual(result['candidate'], f.candidate)
                self.assertEqual(result['policy'], rt.policy_sha)
                self.assertTrue(result['protected'])
        f.record.write_bytes(f.record.read_bytes()+b' ')
        for argv in (flow_argv, session_argv):
            child = subprocess.run([sys.executable, '-B', '-c', harness, str(ROOT/'tools'),
                *argv[2:]], capture_output=True, text=True)
            self.assertNotEqual(child.returncode, 0); self.assertEqual(child.stdout, '')

    def test_stable_binding_changes_model_admission_scope(self):
        from studio.model_admission import ModelAdmission
        f = StableFixture(self); development = f.launch()
        binding = dict(runtime_profile='fixture',provider='codex',model='SYNTHETIC',effort='high')
        before = ModelAdmission(development).key(binding, str(f.workspace))
        f.approval(); stable = f.launch()
        after = ModelAdmission(stable).key(binding, str(f.workspace))
        self.assertNotEqual(before, after)
        self.assertEqual(ModelAdmission(stable).scope(binding, str(f.workspace))['operator'],
                         digest(f.operator_file.read_bytes()))

    def test_native_and_external_profiles_can_share_pinned_readonly_dependencies(self):
        for stable in (False, True):
            with self.subTest(stable=stable):
                f = StableFixture(self)
                executable = f.operator['profiles']['fixture']['executable']
                save(f.policy_file, dict(version=1, models={}, external_steps={'fixture':dict(
                    kind='verify', argv=[executable], executable_sha256=f.operator['dependencies'][executable],
                    dependencies=f.operator['dependencies'])}))
                if stable: f.approval()
                rt = f.launch()
                self.assertIn(Path(executable), rt.protected)
                self.assertEqual(rt.release_identity()['channel'], 'Stable' if stable else 'Development')


if __name__ == '__main__':
    unittest.main()
