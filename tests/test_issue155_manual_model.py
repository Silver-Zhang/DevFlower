"""#155: manual model registration while the live catalog is unavailable."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'tools'), str(ROOT/'tests')]

from studio.graph import Invalid  # noqa: E402
from studio.model_registration import ModelRegistration  # noqa: E402
from test_workbench_runtime import fixture  # noqa: E402


class ManualModelTests(unittest.TestCase):
    def setUp(self):
        self.base, self.rt = fixture(self)
        self.workspace = self.base.project['id']
        self.profile = next(iter(self.rt.operator['profiles']))
        self.registration = ModelRegistration(self.rt)

    def manual(self, **changes):
        data = dict(workspace=self.workspace, profile=self.profile, model='gpt-5.4', effort='medium')
        data.update(changes)
        return self.registration.manual(data)

    def test_manual_entry_becomes_a_workspace_choice(self):
        value = self.manual()
        self.assertTrue(value['configured'])
        self.assertFalse(value['execution_allowed'])
        self.assertFalse(value['discovered'])
        self.assertEqual(value['capability_source'], 'manual entry; live catalog unavailable')
        choices = self.registration.choices(self.workspace, self.profile)
        self.assertEqual(choices['gpt-5.4']['effort'], 'medium')
        self.assertEqual(choices['gpt-5.4']['state'], 'CONFIGURED')

    def test_manual_entry_rejects_bad_input(self):
        for bad in (dict(effort='turbo'), dict(model=''), dict(model='x'*201), dict(model='bad\nname')):
            with self.subTest(bad=bad), self.assertRaises(Invalid):
                self.manual(**bad)

    def test_manual_entry_is_scoped_to_the_workspace(self):
        self.manual()
        rows = [r for r in self.rt.flow.list('model-registration')
                if r.get('workspace') == self.workspace and r.get('model') == 'gpt-5.4']
        self.assertEqual(len(rows), 1)
        self.assertNotEqual(rows[0]['state'], 'STABLE')

    def test_manual_entry_is_refused_when_the_model_is_really_discovered(self):
        import json, time
        from orchestration.common import digest
        from studio.model_registration import profile_identity
        profile = self.rt.operator['profiles'][self.profile]
        self.rt.flow.put('model-discovery', self.profile, dict(
            id=self.profile, profile_sha256=digest(json.dumps(profile, sort_keys=True).encode()),
            environment_sha256=profile_identity(profile), at=time.time(),
            models=[dict(id='gpt-5.4', efforts=['medium'])], source='proxy /v1/models', error=None))
        with self.assertRaisesRegex(Invalid, '已在当前目录中'):
            self.manual()


if __name__ == '__main__':
    unittest.main()
