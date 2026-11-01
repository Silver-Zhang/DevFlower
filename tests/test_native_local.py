"""Canonical native roots and writer locks; synthetic fixtures, not model proof."""
import copy
import fcntl
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path[:0]=[str(Path(__file__).parent),str(Path(__file__).resolve().parents[1]/'tools')]
from test_workbench_runtime import fixture,structured_session
from studio.native_local import configured,bind,profile_key,session_lease,native_busy,codex_history,preferred_profile,native_environment,own_busy,key
from orchestration.common import Blocked,save,digest


class NativeLocalTests(unittest.TestCase):
    def setUp(self):
        self.base,self.rt=fixture(self)
        self.home=self.base.source.parent/'canonical-native-home';self.home.mkdir()
        (self.home/'.codex').mkdir();(self.home/'.claude').mkdir()
        op=copy.deepcopy(self.rt.operator)
        op['native_local']={p:dict(home=str(self.home),config_root=str(self.home/('.'+p))) for p in ('codex','claude')}
        save(self.rt.operator_file,op);self.rt.operator=op;self.rt.operator_sha256=digest(self.rt.operator_file.read_bytes())
        self.binding=bind(self.rt,op['profiles']['default-codex'],self.base.project['id'])

    def test_explicit_native_binding_uses_the_same_canonical_home(self):
        v=self.rt.node_binding(self.base.project['id'],dict(profile='default-codex',mode='Ask'),admission=False)['binding']
        self.assertEqual(v['home'],str(self.home));self.assertEqual(v['execution_backend'],'native-local')
        self.assertFalse(v['external_sandbox']);self.assertEqual(v['isolation'],'native-local')

    def test_native_root_mismatch_or_link_is_not_adopted(self):
        self.rt.operator['native_local']['codex']['config_root']=str(self.home/'.claude')
        with self.assertRaises(Blocked):configured(self.rt,'codex')

    def test_malformed_local_configuration_fails_closed(self):
        self.rt.operator['native_local']['codex']=True
        with self.assertRaises(Blocked):configured(self.rt,'codex')

    def test_unverified_native_extensions_are_not_advertised(self):
        from studio.agent_extensions import AgentExtensions
        value=AgentExtensions(self.rt).catalog(self.base.project['id'],'default-codex')
        self.assertFalse(value['capabilities']['skills']);self.assertFalse(value['capabilities']['subagents'])
        self.assertEqual(value['skills'],[])

    def test_native_config_rotation_invalidates_exact_execution_scope(self):
        from studio.model_admission import ModelAdmission
        gate=ModelAdmission(self.rt);cwd=str(self.base.source)
        config=self.home/'.codex/config.toml';config.write_text('model_provider="first"\n')
        before=gate.key(self.binding,cwd)
        config.write_text('model_provider="second"\n')
        self.assertNotEqual(gate.key(self.binding,cwd),before)

    def test_default_native_profile_is_explicit_not_dictionary_order(self):
        self.rt.operator['profiles']={'z-other':dict(self.binding,model='different'),**self.rt.operator['profiles']}
        name,p=preferred_profile(self.rt,'codex')
        self.assertEqual(name,'default-codex');self.assertNotEqual(p['model'],'different')

    def test_canonical_claude_does_not_redirect_the_global_onboarding_file(self):
        b=bind(self.rt,self.rt.operator['profiles']['default-claude'],self.base.project['id'])
        self.assertNotIn('CLAUDE_CONFIG_DIR',native_environment(b))
        self.assertEqual(native_environment(self.binding),{'CODEX_HOME':str(self.home/'.codex')})

    def test_native_claude_auth_rotation_invalidates_exact_scope(self):
        from studio.model_admission import ModelAdmission
        b=bind(self.rt,self.rt.operator['profiles']['default-claude'],self.base.project['id']);gate=ModelAdmission(self.rt)
        f=self.home/'.claude/.credentials.json';f.write_text('{"accessToken":"synthetic-one"}')
        before=gate.key(b,str(self.base.source));f.write_text('{"accessToken":"synthetic-two"}')
        self.assertNotEqual(before,gate.key(b,str(self.base.source)))

    def test_unavailable_native_mutation_does_not_leave_pending_journal(self):
        from studio.workbench_sessions import WorkbenchSessions
        manager=WorkbenchSessions(self.rt)
        with self.assertRaises(Blocked):manager.rpc(self.binding,str(self.base.source),'thread/fork',{'threadId':'anything'})
        self.assertEqual(self.rt.flow.list('native-mutation'),[])

    def test_different_native_uuids_share_home_but_not_profile_or_lock(self):
        a=dict(self.binding,session_id='00000000-0000-4000-8000-000000000001')
        b=dict(self.binding,session_id='00000000-0000-4000-8000-000000000002')
        self.assertNotEqual(profile_key(a),profile_key(b))
        with session_lease(a):
            with session_lease(b):pass
            with self.assertRaises(Blocked):
                with session_lease(a):pass

    def test_two_workbench_instances_share_the_same_native_writer_lease(self):
        from types import SimpleNamespace
        other=SimpleNamespace(operator=self.rt.operator,storage=self.rt.storage/'another-installation')
        first=bind(self.rt,self.rt.operator['profiles']['default-claude'],self.base.project['id'])
        second=bind(other,self.rt.operator['profiles']['default-claude'],self.base.project['id'])
        first['session_id']=second['session_id']='00000000-0000-4000-8000-000000000001'
        self.assertEqual(first['local_lock_root'],second['local_lock_root'])
        with session_lease(first):
            with self.assertRaises(Blocked):
                with session_lease(second):pass

    def test_busy_probe_does_not_create_a_lock_directory_or_file(self):
        sid='00000000-0000-4000-8000-000000000001'
        self.assertFalse(own_busy(self.rt,self.binding,sid))
        self.assertFalse(Path(self.binding['local_lock_root']).exists())

    def test_busy_probe_leaves_existing_lock_bytes_unchanged(self):
        sid='00000000-0000-4000-8000-000000000001';root=Path(self.binding['local_lock_root']);root.mkdir(mode=0o700)
        path=root/(key(self.binding,sid)+'.lock');path.write_bytes(b'existing native lease metadata\n')
        self.assertFalse(own_busy(self.rt,self.binding,sid));self.assertEqual(path.read_bytes(),b'existing native lease metadata\n')

    def check_unsafe_lock(self,hardlink):
        b=dict(self.binding,session_id='00000000-0000-4000-8000-000000000001');root=Path(b['local_lock_root']);root.mkdir(mode=0o700)
        victim=self.home/'.claude/.credentials.json';before=b'{"accessToken":"SYNTHETIC_KEEP_UNCHANGED"}\n';victim.write_bytes(before)
        target=root/(key(b)+'.lock')
        if hardlink:os.link(victim,target)
        else:target.symlink_to(victim)
        with self.assertRaises(Blocked):own_busy(self.rt,b,b['session_id'])
        self.assertEqual(victim.read_bytes(),before)
        with self.assertRaises(Blocked):
            with session_lease(b):pass
        self.assertEqual(victim.read_bytes(),before)

    def test_symlinked_lease_never_changes_credential_bytes(self):self.check_unsafe_lock(False)
    def test_hardlinked_lease_never_changes_credential_bytes(self):self.check_unsafe_lock(True)

    def test_native_store_lock_blocks_only_the_owned_thread(self):
        root=self.home/'.codex/thread-writer-locks';root.mkdir()
        a='00000000-0000-4000-8000-000000000001';b='00000000-0000-4000-8000-000000000002'
        with (root/(a+'.lock')).open('w') as stream:
            fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
            self.assertTrue(native_busy(self.binding,a));self.assertFalse(native_busy(self.binding,b))
        self.assertFalse(native_busy(self.binding,a))

    def test_native_history_reads_provider_items_and_never_hidden_reasoning(self):
        sid='00000000-0000-4000-8000-000000000001';cwd=str(self.base.source)
        value=dict(thread=dict(id=sid,cwd=cwd,turns=[dict(id='actual-turn',items=[
            dict(type='userMessage',id='u',content=[dict(type='text',text='question')]),
            dict(type='reasoning',id='private',summary='DO_NOT_SHOW'),
            dict(type='agentMessage',id='a',text='answer')])]))
        with patch('studio.native_codex_rpc.metadata',return_value=value) as rpc:
            rows=codex_history(self.binding,cwd,sid)
        self.assertEqual([r['text'] for r in rows],['question','answer'])
        self.assertEqual(rows[0]['turn_id'],'actual-turn');self.assertEqual(rpc.call_args.args[2],'thread/read')
        self.assertFalse((self.home/'copied-history').exists())

    def test_native_history_mismatch_cannot_cross_workspace(self):
        value=dict(thread=dict(id='other',cwd='/other',turns=[]))
        with patch('studio.native_codex_rpc.metadata',return_value=value):
            with self.assertRaises(Blocked):codex_history(self.binding,str(self.base.source),'00000000-0000-4000-8000-000000000001')

    def test_provider_turn_clock_keeps_external_native_continuation_in_default_view(self):
        from studio.conversation_projection import native_turns
        sid='00000000-0000-4000-8000-000000000001';cwd=str(self.base.source)
        value=dict(thread=dict(id=sid,cwd=cwd,turns=[dict(id='external-turn',startedAt=100,completedAt=110,items=[
            dict(type='userMessage',id='u',content=[dict(type='text',text='from native client')]),
            dict(type='agentMessage',id='a',text='real external answer')])]))
        with patch('studio.native_codex_rpc.metadata',return_value=value):rows=codex_history(self.binding,cwd,sid)
        turns=native_turns([],[],rows,rows,True)
        self.assertEqual([t['messages'][0]['text'] for t in turns],['from native client','real external answer'])
        self.assertEqual(turns[0]['created_at'],100)
        self.assertEqual(turns[1]['created_at'],100)

    def test_exact_native_turn_identity_beats_coarse_process_time_windows(self):
        from studio.conversation_projection import native_turns
        rows=[dict(role=role,text=text,turn_id=turn,native_at=100) for turn in ('managed','external')
              for role,text in [('user',turn+' question'),('assistant',turn+' answer')]]
        operation=dict(receipt={'native_turn_id':'managed'},native_started_at=99.9,native_finished_at=101.2)
        turns=native_turns([], [operation], rows, rows, True)
        self.assertEqual([t['messages'][0]['text'] for t in turns],['external question','external answer'])

    def test_claude_write_mode_is_not_declared_verified_from_a_prompt(self):
        with self.assertRaises(Blocked):self.rt.node_binding(self.base.project['id'],dict(profile='default-claude',mode='Agent'),admission=False)

    def test_legacy_profile_keys_and_homes_keep_original_semantics(self):
        home=str(self.home/'legacy');old=dict(home=home,provider='codex',session_id='anything')
        self.assertEqual(profile_key(old),digest(home.encode()))
        self.assertFalse(native_busy(self.binding,None))


if __name__=='__main__':unittest.main()

class ClaudeExternalResumeSafety(unittest.TestCase):
    def setUp(self):
        self.helper=NativeLocalTests();self.helper.setUp();self.addCleanup(self.helper.doCleanups)
        self.rt=self.helper.rt;self.workspace=self.helper.base.project;self.home=self.helper.home
    def test_unmanaged_claude_transcript_cannot_be_adopted_even_if_no_process_is_seen(self):
        from studio.native_local import configured
        cfg=configured(self.rt,'claude');sid='00000000-0000-4000-8000-000000000001'
        handle=dict(profile=dict(provider='claude',home=cfg['home'],config_root=cfg['config_root']),native=dict(cwd=self.workspace['path'],cwd_changed=False,session_id=sid),ambiguous=False)
        with patch('studio.native_local.own_busy',return_value=False):binding,reason=self.rt.session_center._binding(handle,self.workspace)
        self.assertIsNone(binding);self.assertIn('只读',reason)
    def test_new_claude_conversation_remains_available(self):
        selected=self.rt.node_binding(self.workspace['id'],dict(profile='default-claude',mode='Ask'),admission=False,direct=True)
        self.assertIsNone(selected['binding']['session_id'])
    def test_native_claude_existing_uuid_is_rejected_at_dispatch_not_a_process_scan(self):
        from studio.native_local import require_safe_resume
        from studio.native_local import bind
        b=bind(self.rt,self.rt.operator['profiles']['default-claude'],self.workspace['id']);b['session_id']='00000000-0000-4000-8000-000000000001'
        with self.assertRaisesRegex(Blocked,'只读'):require_safe_resume(b)
        b['session_id']=None;require_safe_resume(b)
    def test_managed_old_uuid_is_not_reopened_after_terminal_exit(self):
        from studio.native_local import configured,bind
        cfg=configured(self.rt,'claude');sid='00000000-0000-4000-8000-000000000001'
        binding=bind(self.rt,self.rt.operator['profiles']['default-claude'],self.workspace['id']);binding.update(runtime_profile='default-claude',session_id=sid)
        self.rt.db.put('chat-profile','old-managed',dict(workspace=self.workspace['id'],binding=binding))
        handle=dict(profile=dict(provider='claude',home=cfg['home'],config_root=cfg['config_root']),native=dict(cwd=self.workspace['path'],cwd_changed=False,session_id=sid),ambiguous=False)
        selected,reason=self.rt.session_center._binding(handle,self.workspace)
        self.assertIsNone(selected);self.assertIn('只读',reason)
