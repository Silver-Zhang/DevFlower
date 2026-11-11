"""Independent native Agent account admission and writer isolation."""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4

from orchestration.common import Blocked
from studio.graph import Invalid
from v2.agent_execution import freeze_agent
from v2.agent_records import AgentRecords
from v2.cli_providers import ClaudeCLI, ProviderRegistry
from v2.native_agent_homes import NativeAgentHomes
from v2.session_identity import SessionRefused
from v2.session_lease import production_session_gate
from v2.store import DraftStore


class NativeAgentHomeTests(TestCase):
    def setUp(self):
        self.base = Path(self.enterContext(TemporaryDirectory()))
        self.cwd = self.base/'workspace'
        self.cwd.mkdir()
        self.store = DraftStore(self.base/'state',[self.cwd])
        self.workspace = self.store.open_workspace(str(self.cwd))['id']

    def home(self, name, provider='codex'):
        home = self.base/name
        root = home/('.'+provider)
        home.mkdir(mode=0o700)
        root.mkdir(mode=0o700)
        if provider=='codex':
            (root/'config.toml').write_text('model="fixture"\ncli_auth_credentials_store="file"\n')
        else:
            (root/'settings.json').write_text(json.dumps({'model':'claude-sonnet-4-6',
                    'env':{'ANTHROPIC_API_KEY':'alternate-synthetic-key'}}))
        for file in root.iterdir():
            file.chmod(0o600)
        return home

    def test_startup_freezes_config_root_and_credentials(self):
        home = self.home('named')
        root = home/'.codex'
        auth = root/'auth.json'
        auth.write_text('{"OPENAI_API_KEY":"alternate-synthetic-one"}')
        auth.chmod(0o600)
        homes = NativeAgentHomes(self.store,['codex:named='+str(home)],{})
        profile = homes.require('codex','named')
        self.assertNotIn('alternate-synthetic-one',str(profile))
        binding = dict(profile, native_home=profile)
        homes.require_binding(binding)
        self.assertEqual(homes.worker_arguments([binding,binding]),['--native-agent-home','codex:named='+str(home)])
        auth.write_text('{"OPENAI_API_KEY":"alternate-synthetic-two"}')
        with self.assertRaises(Blocked):
            homes.require_binding(binding)
        different = NativeAgentHomes(self.store,['codex:named='+str(home)],{})
        with self.assertRaises(Blocked):
            different.require_binding(binding)

    def test_refuses_workspace_alias_keyring_and_shared_auth_inode(self):
        home = self.home('named')
        profile = home/'.codex'
        alias = self.base/'alias'
        alias.symlink_to(home)
        with self.assertRaises((Invalid,Blocked,SessionRefused)):
            NativeAgentHomes(self.store,['codex:alias='+str(alias)],{})
        with self.assertRaises(Invalid):
            NativeAgentHomes(self.store,['codex:inside='+str(self.cwd)],{})
        with self.assertRaises(Invalid):
            NativeAgentHomes(self.store,['codex:named='+str(home)],{('codex','named'):'fixture'})
        config = profile/'config.toml'
        config.write_text('cli_auth_credentials_store="keyring"')
        with self.assertRaises(Blocked):
            NativeAgentHomes(self.store,['codex:named='+str(home)],{})
        config.write_text('cli_auth_credentials_store="file"')
        outside = self.base/'other-auth'
        outside.write_text('{"OPENAI_API_KEY":"outside-synthetic-key"}')
        os.link(outside,profile/'auth.json')
        with self.assertRaises((Blocked,SessionRefused)):
            NativeAgentHomes(self.store,['codex:named='+str(home)],{})

    def test_refuses_cross_home_database_and_nested_history_aliases(self):
        codex = self.home('codex')
        outside = self.base/'outside'
        outside.write_text('fixture')
        database = codex/'.codex/state_5.sqlite'
        database.symlink_to(outside)
        with self.assertRaises(Blocked):
            NativeAgentHomes(self.store,['codex:isolated='+str(codex)],{})
        database.unlink()
        claude = self.home('claude','claude')
        project = claude/'.claude/projects/workspace'
        project.mkdir(parents=True)
        (project/'session.jsonl').symlink_to(outside)
        with self.assertRaises(Blocked):
            NativeAgentHomes(self.store,['claude:isolated='+str(claude)],{})
        (project/'session.jsonl').unlink()
        (claude/'.claude.json').symlink_to(outside)
        with self.assertRaises(Blocked):
            NativeAgentHomes(self.store,['claude:isolated='+str(claude)],{})

    def test_saved_agent_cannot_follow_repointed_profile_after_restart(self):
        from test_v2_agent_records import configuration

        first = self.home('first','claude')
        second = self.home('second','claude')
        initial = ProviderRegistry(self.store,native_homes=['claude:named='+str(first)])
        agents = AgentRecords(self.store, initial)
        config = configuration(self.cwd, mode='Ask', max_budget_usd=None, native_profile='named')
        saved = agents.save(self.workspace,None,None,config)
        self.assertIsNotNone(saved['native_home_pin'])
        replaced = ProviderRegistry(self.store,native_homes=['claude:named='+str(second)])
        agents.providers = replaced
        runtime = SimpleNamespace(agents=agents)
        with self.assertRaisesRegex(Invalid,'绑定已变化'):
            freeze_agent(runtime,self.workspace,dict(agent_id=saved['id'],agent_revision=saved['revision']))
        confirmed = agents.save(self.workspace,saved['id'],saved['revision'],config)
        self.assertNotEqual(confirmed['native_home_pin'],saved['native_home_pin'])
        self.assertNotEqual(confirmed['revision'],saved['revision'])

    def test_same_uuid_in_two_verified_homes_has_distinct_native_gate(self):
        provider = 'claude'
        first, second = (self.home(name,provider) for name in ('first','second'))
        registry = ProviderRegistry(self.store,native_homes=[f'{provider}:first={first}',f'{provider}:second={second}'])
        active = [registry.selected(provider,name) for name in ('first','second')]
        sid = str(uuid4())
        def scope(home):
            return dict(home,project=self.workspace,cwd=str(self.cwd),session_id=sid,execution_backend='native-local',
                        local_lock_root=str(Path(home['config_root'])/'.devflower-writer-locks'))
        with production_session_gate(scope(active[0]),None), production_session_gate(scope(active[1]),None):
            with self.assertRaises(SessionRefused):
                with production_session_gate(scope(active[0]),None):
                    self.fail('same HOME and UUID admitted a second writer')
        with patch.dict(os.environ,{'ANTHROPIC_API_KEY':'global-sentinel'}):
            self.assertEqual(ClaudeCLI(self.store,native_home=active[0]).credential_environment(),{})
            self.assertEqual(ClaudeCLI(self.store).credential_environment()['ANTHROPIC_API_KEY'],'global-sentinel')
        public = registry.listing()
        self.assertEqual([row['id'] for row in public['profiles']],['first','second'])
        self.assertNotIn(str(first),str(public))
