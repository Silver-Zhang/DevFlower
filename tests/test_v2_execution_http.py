"""Real loopback HTTP with synthetic Provider/spawn; never native inference."""
import copy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from orchestration.common import Blocked
from v2.server import create_server
from v2.runtime import V2Runtime
from v2.store import DraftStore
from studio.graph import Invalid
import test_v2_execution as fixtures


class ExecutionHTTPTests(unittest.TestCase):
    setUp = fixtures.ExecutionTests.setUp
    def start_server(self, runtime=None):
        server, self.token = create_server(self.store, runtime=runtime or self.rt)
        self.server = server
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        self.url = 'http://127.0.0.1:' + str(server.server_port) + '/api/v2/'

    def request(self, path, data=None):
        request = Request(self.url + path, data=None if data is None else json.dumps(data).encode(),
            headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json'})
        try:
            with urlopen(request, timeout=10) as response: return response.status, json.load(response)
        except HTTPError as error: return error.code, json.load(error)

    def payload(self):
        return dict(workspace=self.workspace,draft_id=self.draft['id'],revision=self.draft['revision'],
                    intent_key='synthetic-http-intent',task='')

    def test_http_concurrent_launch_exactly_one_durable_run_and_spawn(self):
        self.rt.provider.cached = lambda: dict(models=[],inference_verified=False)
        self.start_server()
        lock = threading.Lock(); seen=[]
        def spawn(run):
            with self.store.db.tx(read=True) as db:
                self.assertEqual(db.execute('SELECT run_id FROM launch_intents').fetchone()[0],run)
                self.assertEqual(len(self.store.db.list('definition')),1)
            with lock: seen.append(run)
        with patch.object(self.rt,'spawn',side_effect=spawn):
            with ThreadPoolExecutor(max_workers=6) as pool: rows=list(pool.map(lambda _:self.request('launch',self.payload()),range(6)))
        self.assertEqual([r[0] for r in rows],[200]*6)
        self.assertEqual(len({r[1]['run']['id'] for r in rows}),1);self.assertEqual(len(seen),1)
        code,result=self.request('launch',dict(self.payload(),task='changed'))
        self.assertEqual(code,409);self.assertIn('不同',result['error']);self.assertEqual(len(seen),1)

    def test_http_frozen_run_ownership_and_saved_edit_separate(self):
        self.start_server()
        with patch.object(self.rt,'spawn'):
            code,result=self.request('launch',self.payload())
        self.assertEqual(code,200);run=result['run']['id'];definition=copy.deepcopy(result['definition'])
        changed=copy.deepcopy(self.doc);changed['nodes'][0]['round_prompt']='new local draft'
        code,_=self.request('drafts/save',dict(workspace=self.workspace,id=self.draft['id'],expected=self.draft['revision'],document=changed))
        self.assertEqual(code,200)
        code,result=self.request('run?workspace='+self.workspace+'&id='+run)
        self.assertEqual(code,200);self.assertEqual(result['definition'],definition)
        spaces=self.root/'spaces';(spaces/'B').mkdir();other=self.store.open_workspace(str(spaces/'B'))['id']
        self.assertEqual(self.request('run?workspace='+other+'&id='+run)[0],409)
        self.assertEqual(self.request('runs?workspace='+other),(200,[]))

    def test_http_missing_cli_keeps_draft_entry_and_explains_unavailable(self):
        with patch('v2.runtime.CodexProvider',side_effect=Blocked('未找到 Codex CLI')): rt=V2Runtime(self.store)
        self.start_server(rt)
        code,status=self.request('status');self.assertEqual(code,200)
        self.assertTrue(status['execution_supported']);self.assertFalse(status['provider_available']);self.assertFalse(status['execution_available'])
        self.assertIn('编辑和保存仍可使用',status['provider']['error'])
        empty=dict(version=1,name='still editable',nodes=[],edges=[])
        code,_=self.request('drafts/save',dict(workspace=self.workspace,id=None,expected=None,document=empty));self.assertEqual(code,200)
        self.assertEqual(self.request('launch',self.payload())[0],409)
        self.assertEqual(self.store.db.list('run'),[])

    def test_http_custom_home_and_native_secret_never_public(self):
        secret='SYNTHETIC_PRIVATE_VALUE_123';url='https://private.invalid/credential'
        with patch('v2.runtime.CodexProvider',side_effect=Blocked('CODEX_HOME '+secret+' '+url)): rt=V2Runtime(self.store)
        self.start_server(rt)
        _,status=self.request('status');public=json.dumps(status)
        self.assertNotIn(secret,public);self.assertNotIn(url,public);self.assertFalse(status['provider_available'])
        self.assertIn('CODEX_HOME',status['provider']['error'])

    def test_http_worker_dead_projection_agrees_list_detail(self):
        self.start_server()
        with patch.object(self.rt,'spawn'): _,result=self.request('launch',self.payload())
        run=result['run']['id'];self.store.db.change('run',run,dict(state='RUNNING',created_at=time.time()-60))
        _,detail=self.request('run?workspace='+self.workspace+'&id='+run);_,rows=self.request('runs?workspace='+self.workspace)
        self.assertEqual(detail['state'],'RECOVERY_REQUIRED');self.assertEqual(detail['run']['state'],rows[0]['state'])
        self.assertEqual(detail['run']['persisted_state'],'RUNNING')

    def test_http_bad_identity_and_invalid_launch_return_explicit_errors(self):
        self.start_server()
        for patch_value in [dict(workspace=[]),dict(intent_key=[]),dict(task=[]),dict(draft_id=[]),dict(revision=[])]:
            code,value=self.request('launch',dict(self.payload(),**patch_value))
            self.assertEqual(code,400,(patch_value,value));self.assertIn('error',value)
        self.assertEqual(self.store.db.list('run'),[])

    def test_v2_refuses_state_parent_as_execution_root(self):
        # V2 deliberately does not adopt old partial parent-workspace views.
        with self.assertRaises(Invalid): DraftStore(self.root/'state',[self.root])


if __name__=='__main__':unittest.main()
