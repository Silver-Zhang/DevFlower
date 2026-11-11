"""Native title recovery, frozen profile scope and public execution facts."""
from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch
from uuid import uuid4

from orchestration.common import Blocked, canonical, digest
from v2.execution_facts import failure_fact
from v2.native_agent_profiles import approved_paths, read_profile, require_frozen_profile
from v2.native_session_links import claim_key, require_creation_profile
from v2.native_session_title import NativeSessionTitles
from v2.native_writer_state import require_native_writer_idle
from v2.session_identity import SessionRefused, StorageRootIdentity
from v2.session_lease import production_session_gate
from v2.store import DraftStore


class NativeControlTests(TestCase):
    def setUp(self):
        self.base = Path(self.enterContext(TemporaryDirectory()))
        self.cwd = self.base/'workspace'
        self.cwd.mkdir()
        self.home = self.base/'home'
        self.root = self.home/'.codex'
        self.root.mkdir(parents=True,mode=0o700)
        self.store = DraftStore(self.base/'state',[self.cwd])
        self.workspace = self.store.open_workspace(str(self.cwd))['id']
        self.sid = str(uuid4())
        self.binding = dict(provider='codex',project=self.workspace,cwd=str(self.cwd),home=str(self.home),
            config_root=str(self.root),session_id=self.sid,execution_backend='native-local',
            local_lock_root=str(self.root/'.devflower-writer-locks'),
            root_identity=StorageRootIdentity.capture(self.root).fields())

    def test_named_profiles_freeze_file_and_refuse_unsafe_options(self):
        claude_root = self.home/'.claude'
        claude_root.mkdir()
        claude = claude_root/'quality.json'
        codex = self.root/'quality.config.toml'
        paths = approved_paths(['codex:quality','claude:quality='+str(claude)],self.home)
        self.assertEqual(paths[('codex','quality')],str(codex))
        for file,content in ((codex,'model="fixture-a"\nmodel_reasoning_effort="low"'),
                             (claude,'{"model":"fixture-b","effortLevel":"high"}')):
            file.write_text(content)
            file.chmod(0o600)
        for provider,file,model,effort in (('codex',codex,'fixture-a','low'),('claude',claude,'fixture-b','high')):
            with self.subTest(provider=provider):
                root = file.parent
                profile = read_profile(provider,'quality',str(file),str(root))
                self.assertEqual((profile['model'],profile['effort']),(model,effort))
                binding = {'provider':provider,'config_root':str(root),'native_profile':profile}
                require_frozen_profile(binding)
                file.write_text('model="fixture-c"' if provider=='codex' else '{"model":"fixture-c"}')
                with self.assertRaises(Blocked):
                    require_frozen_profile(binding)
        claude.write_text('{"env":{"ANTHROPIC_BASE_URL":"https://forbidden.invalid"}}')
        with self.assertRaises(Blocked):
            read_profile('claude','quality',str(claude),str(claude_root))

    def test_named_resume_identity_does_not_split_native_root_lease(self):
        named = {'id':'quality','sha256':'a'*64}
        with production_session_gate(self.binding,None):
            with self.assertRaises(SessionRefused) as error:
                with production_session_gate(dict(self.binding,native_profile=named),None):
                    self.fail('same native UUID had two simultaneous writers')
            self.assertEqual(error.exception.code,'LEASE_HELD')
        root = self.binding['config_root']
        self.store.db.put('native-session-claim',claim_key(root,self.sid),{'session_id':self.sid})
        with patch('v2.native_session_links.verified_workflow',return_value={'native_profile':named}):
            require_creation_profile(self.store,root,self.sid,'codex',named)
            for mismatch in (None,{'id':'quality','sha256':'b'*64}):
                with self.assertRaises(SessionRefused):
                    require_creation_profile(self.store,root,self.sid,'codex',mismatch)
        self.store.db.put('native-session-title','unresolved',dict(binding=self.binding,target=self.sid,
                          state='RECOVERY_REQUIRED',effect_uncertain=True))
        reopened = DraftStore(self.store.state,[self.cwd])
        with production_session_gate(self.binding,None), self.assertRaises(SessionRefused) as error:
            require_native_writer_idle(reopened,self.binding,self.sid)
        self.assertEqual(error.exception.code,'RECOVERY_REQUIRED')

    def test_lost_native_title_response_reconciles_without_another_mutation(self):
        handle = 'v1.'+'1'*64
        relative = Path('year/month/day/thread.jsonl')
        record = SimpleNamespace(ref=SimpleNamespace(relative=relative))
        locator = object()
        reader = Mock()
        reader.workspace.return_value = self.cwd,None,None
        reader.listing.return_value = {'revision':'2'*32}
        reader.verified_record.side_effect = lambda *_:nullcontext((None,locator))
        operations = SimpleNamespace(store=self.store,reader=reader,require_scope=Mock())
        titles = NativeSessionTitles(operations)
        native = {'title':None,'writes':0}
        def request(method,params):
            self.assertEqual(method,'thread/name/set')
            native['title'] = params['name']
            native['writes'] += 1
            raise OSError('simulated missing response')
        rpc = SimpleNamespace(request=request)
        data = dict(workspace=self.workspace,handle=handle,revision='2'*32,intent_key='native-title-fixture',
                    expected_title_revision=digest(canonical(None)),title='原生标题')
        fence = Mock()
        with patch.object(titles,'locate',return_value=(self.binding,locator,record)), \
             patch.object(titles,'client',side_effect=lambda _:nullcontext(rpc)), \
             patch.object(titles,'checked_thread',side_effect=lambda *_:native['title']), \
             patch('v2.native_session_title.production_session_gate',side_effect=lambda *_:nullcontext()), \
             patch('v2.native_session_title.codex_presentation_guard',side_effect=lambda *_:nullcontext(fence)), \
             patch('v2.native_session_title.verify_bytes'):
            first = titles.rename(data)
            self.assertEqual(first['state'],'RECOVERY_REQUIRED')
            self.assertEqual(titles.rename(data)['state'],'RECOVERY_REQUIRED')
            result = titles.operation(self.workspace,data['intent_key'],verify=True)
            self.assertEqual((result['state'],result['native_title']),('COMPLETED',data['title']))
            self.assertEqual(native['writes'],1)
            self.assertEqual(fence.call_count,2)

    def test_diagnostics_do_not_infer_no_send_from_a_local_timeout(self):
        failure = Blocked('authorization: fixture-sensitive-literal')
        before = failure_fact('codex',failure)
        self.assertEqual((before['code'],before['effect']),('AUTHENTICATION','not-sent'))
        self.assertNotIn('fixture-sensitive-literal',str(before))
        process = {'timeout':True,'duration':1.5,'exit':9,'argv':['fixture-sensitive-literal']}
        during = failure_fact('claude',failure,process=process,uncertain=True,sent=True)
        self.assertEqual((during['code'],during['effect']),('LOCAL_TIMEOUT','unknown'))
        self.assertEqual(during['process'],{'exit':9,'duration':1.5,'timeout':True})
        budget = failure_fact('claude',failure,uncertain=True,native_limit='error_max_budget_usd')
        self.assertEqual((budget['code'],budget['effect']),('NATIVE_USD_LIMIT','unknown'))

    def test_run_target_reuses_exact_existing_terminal_without_start(self):
        from v2.codex_terminals import CodexTerminals
        terminal = CodexTerminals.__new__(CodexTerminals)
        terminal.store = self.store
        terminal.operations = Mock()
        run,activation,chat,exact = 'a'*32,'b'*64,'c'*64,'d'*64
        self.store.db.put('activation',activation,dict(run=run,session_binding_id=chat))
        self.store.db.put('node-session',chat,dict(workspace=self.workspace,binding=self.binding,
                                                 native_session_id=self.sid))
        self.store.db.put(terminal.record_kind,exact,dict(binding=self.binding,workspace=self.workspace,
                          session_id=self.sid,state='RUNNING'))
        for index in range(31):
            self.store.db.put(terminal.record_kind,str(index),dict(binding=self.binding,workspace=self.workspace,
                              session_id=self.sid,state='COMPLETED'))
        terminal.snapshot = Mock(return_value=dict(id=exact,handle='exact-handle',state='RUNNING'))
        use = (dict(id=run,workspace=self.workspace),None,None,dict(id=activation),None,self.binding,str(self.cwd))
        with patch('v2.codex_terminals.workflow_use',return_value=use):
            value = terminal.run_target(self.workspace,run,activation)
            self.assertEqual((value['id'],value['handle'],value['activation']),(exact,'exact-handle',activation))
            self.assertFalse(value['start_available'])
            with self.assertRaises(SessionRefused):
                terminal.run_target(self.workspace,'1'*32,activation)
        terminal.operations.reader.listing.assert_not_called()

    def test_run_target_explains_independent_home_without_default_lookup(self):
        from v2.codex_terminals import CodexTerminals
        terminal = CodexTerminals.__new__(CodexTerminals)
        terminal.store = self.store
        terminal.operations = Mock()
        run, activation, chat = 'a'*32, 'b'*64, 'c'*64
        binding = dict(self.binding,native_home={'id':'independent'})
        self.store.db.put('activation',activation,dict(run=run,session_binding_id=chat))
        self.store.db.put('node-session',chat,dict(workspace=self.workspace,binding=binding,
                                                 native_session_id=self.sid))
        use = (dict(id=run,workspace=self.workspace),None,None,dict(id=activation),None,binding,str(self.cwd))
        with patch('v2.codex_terminals.workflow_use',return_value=use):
            result = terminal.run_target(self.workspace,run,activation)
        self.assertFalse(result['available'])
        self.assertIn('独立原生 HOME',result['reason'])
        terminal.operations.reader.listing.assert_not_called()
        terminal.operations.resolve.assert_not_called()
