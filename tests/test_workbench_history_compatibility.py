"""History stays readable when an old writer configuration is removed."""
import copy
import unittest
from unittest.mock import patch
import test_native_local


class HistoricalProfileTests(unittest.TestCase):
    def setUp(self):
        owner=test_native_local.NativeLocalTests();owner.setUp();self.addCleanup(owner.doCleanups)
        self.rt=owner.rt;self.project=owner.base.project
        self.previous=dict(owner.binding,runtime_profile='old-codex',session_id='00000000-0000-4000-8000-000000000001')
        self.rt.db.put('chat-profile','old-chat',dict(workspace=self.project['id'],binding=self.previous))
        self.handle=dict(profile=dict(provider='codex',home=self.previous['home'],config_root=self.previous['config_root']),
            native=dict(session_id=self.previous['session_id'],cwd=self.project['path'],cwd_changed=False))

    def test_removed_profile_does_not_break_discovery_and_is_not_remapped(self):
        binding,note=self.rt.session_center._binding(self.handle,self.project)
        self.assertIsNone(binding);self.assertIn('已移除',note)
        self.assertEqual(self.rt.db.get('chat-profile','old-chat')['binding'],self.previous)
        self.assertIn('default-codex',self.rt.operator['profiles'])

    def test_reused_name_with_another_provider_cannot_resume_old_uuid(self):
        self.rt.operator['profiles']['old-codex']=copy.deepcopy(self.rt.operator['profiles']['default-claude'])
        binding,note=self.rt.session_center._binding(self.handle,self.project)
        self.assertIsNone(binding);self.assertIn('更换 Provider',note)

    def test_changed_path_scope_keeps_history_readonly(self):
        current=copy.deepcopy(self.rt.operator['profiles']['default-codex'])
        current['write_paths']=['/different-scope']
        self.rt.operator['profiles']['old-codex']=current
        binding,note=self.rt.session_center._binding(self.handle,self.project)
        self.assertIsNone(binding);self.assertIn('路径权限已变化',note)

    def test_unchanged_profile_keeps_exact_uuid_model_and_home(self):
        self.rt.operator['profiles']['old-codex']=copy.deepcopy(self.rt.operator['profiles']['default-codex'])
        with patch('studio.native_local.own_busy',return_value=False):
            binding,_=self.rt.session_center._binding(self.handle,self.project)
        for field in ('provider','session_id','home','model','effort'):
            self.assertEqual(binding[field],self.previous[field])
