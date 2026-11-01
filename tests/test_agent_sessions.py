"""Session-center regressions: real filesystem/SQLite/HTTP; fake CLI envelopes
are explicitly transport fixtures, never native-model acceptance evidence.
"""
import copy
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'tools'), str(Path(__file__).resolve().parent)]
from orchestration.adapters import CLI
from orchestration.common import Blocked, canonical, digest, save, tree_hash as source_tree_hash
from orchestration.contracts import config_check
from orchestration.store import Store, lease, process_identity
from studio.agent_sessions import capabilities, conversation, nested_profiles, session_identity
from studio.graph import Invalid
from studio.server import Conflict
from studio.usable_graph import compile_plan, template, TOOLS
from studio.usable_runtime import Runtime
from studio.session_runtime import SessionRuntime
from studio.session_center import SessionJournal
from studio.usable_server import handler
import test_usable
from test_workspace_sessions import native, SID, OTHER, tree_hash


def append_messages(path, provider):
    if provider == 'claude':
        rows = [dict(type='user', sessionId=SID, message=dict(role='user', content='VISIBLE hello')),
                dict(type='assistant', sessionId=SID, message=dict(role='assistant', content=[
                    dict(type='thinking', thinking='HIDDEN_REASONING'),
                    dict(type='tool_use', input=dict(command='PRIVATE_COMMAND')),
                    dict(type='text', text='VISIBLE answer api_key=sk-fixture-' + 'x' * 32)])),
                dict(type='user', sessionId=SID, message=dict(role='user', content=[
                    dict(type='tool_result', content='PRIVATE_TOOL_RESULT')]))]
    else:
        rows = [dict(type='response_item', payload=dict(type='message', role='user', content=[dict(type='input_text', text='VISIBLE hello')])),
                dict(type='response_item', payload=dict(type='reasoning', summary='HIDDEN_REASONING')),
                dict(type='response_item', payload=dict(type='function_call', arguments='PRIVATE_COMMAND')),
                dict(type='response_item', payload=dict(type='function_call_output', output='PRIVATE_TOOL_RESULT')),
                dict(type='response_item', payload=dict(type='message', role='assistant', phase='final_answer', content=[dict(type='output_text', text='VISIBLE answer api_key=sk-fixture-' + 'x' * 32)]))]
    with path.open('a') as stream:
        stream.write(''.join(json.dumps(x) + '\n' for x in rows))


class VisibleHistoryTests(unittest.TestCase):
    def test_all_identity_components_participate(self):
        base = ['codex', SID, 'p1', '/work/a', '/work']
        original = session_identity(*base)
        self.assertEqual(original, session_identity(*base))
        for index, value in enumerate(['claude', OTHER, 'p2', '/work/b', '/different']):
            changed = base.copy();changed[index] = value
            self.assertNotEqual(original, session_identity(*changed))

    def test_capabilities_do_not_promise_unsafe_native_operations(self):
        for provider in ('codex', 'claude','copilot'):
            c = capabilities(provider)
            self.assertTrue(all(c[k] for k in ('discover', 'read', 'create', 'resume', 'hide', 'unhide')))
            self.assertFalse(c['native_archive']);self.assertFalse(c['native_delete']);self.assertFalse(c['subagents'])
        self.assertFalse(capabilities('unconfigured-provider')['supported'])

    def test_visible_text_search_and_hidden_blocks_for_both_providers(self):
        with tempfile.TemporaryDirectory() as folder:
            for provider in ('codex', 'claude'):
                path = native(Path(folder) / provider, provider, Path(folder))
                append_messages(path, provider)
                rows = conversation(provider, path)
                rendered = json.dumps(rows)
                for hidden in ('HIDDEN_REASONING', 'PRIVATE_COMMAND', 'PRIVATE_TOOL_RESULT', 'sk-fixture-'):
                    self.assertNotIn(hidden, rendered)
                self.assertIn('VISIBLE answer', rendered)
                self.assertEqual(conversation(provider, path, query='HeLLo'), [dict(role='user', text='VISIBLE hello')])
                self.assertEqual(len(conversation(provider, path, limit=1)), 1)

    def test_history_replacement_between_scan_and_read_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            path = native(Path(folder), 'claude', Path(folder));before = digest(path.read_bytes())
            append_messages(path, 'claude')
            with self.assertRaises(Invalid):
                conversation('claude', path, expected_sha256=before)

    def test_partial_and_symlink_history_are_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'broken.jsonl';path.write_text('{"type":"user"}')
            with self.assertRaises(Invalid):conversation('claude', path)
            alias = Path(folder) / 'alias';alias.symlink_to(path)
            with self.assertRaises(Invalid):conversation('claude', alias)

    def test_codex_analysis_channels_and_tool_recipients_never_become_visible(self):
        with tempfile.TemporaryDirectory() as folder:
            path = native(Path(folder), 'codex', Path(folder))
            rows = [dict(type='response_item', payload=dict(type='message', role='assistant',
                    content=[dict(type='output_text', text='PRIVATE_ANALYSIS')], **fields))
                    for fields in ({'channel': 'analysis'}, {'recipient': 'functions.exec'}, {'phase': 'commentary'})]
            with path.open('a') as f:f.write(''.join(json.dumps(row)+'\n' for row in rows))
            self.assertEqual(conversation('codex', path), [])

    def test_nested_discovery_is_bounded_and_does_not_follow_links(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            native(root / 'nested/reviewer/.claude', 'claude', root)
            (root / 'link').symlink_to(root / 'nested', target_is_directory=True)
            rows, _ = nested_profiles(root)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['config_root'], str(root / 'nested/reviewer/.claude'))
            _, coverage = nested_profiles(root, entries=1)
            self.assertEqual(coverage['status'], 'partial')


class CenterTests(unittest.TestCase):
    setUp = test_usable.RuntimeTests.setUp
    request = test_usable.RuntimeTests.request

    def native_profile(self, provider='claude', managed=False):
        home = self.runtime.native / 'dedicated' if managed else self.root / 'external-home'
        root = home / ('.' + provider)
        path = native(root, provider, self.source);append_messages(path, provider)
        if managed:
            p = self.runtime.operator['profiles']['default-' + provider]
            b = dict(p, home=str(home), role='reviewer', project=self.project['id'], session_id=SID,
                     isolation='bwrap', external_sandbox=provider == 'codex')
            self.runtime.db.put('chat-profile', 'test-profile', dict(workspace=self.project['id'], binding=b))
        else:
            operator = copy.deepcopy(self.runtime.operator)
            operator['session_profiles'] = [dict(provider=provider, home=str(home), config_root=str(root))]
            save(self.operator_file, operator)
            self.runtime = Runtime(self.operator_file, digest(self.operator_file.read_bytes()))
        rows = self.runtime.sessions(self.project['id'])['discovery']['sessions']
        return path, next(r for r in rows if r['session_id'] == SID)

    def chat_request(self, row=None, provider='claude'):
        return dict(workspace=self.project['id'], handle=row['handle'] if row else 'new',
                    profile='default-' + provider, permission='read', prompt='Explain this project')

    def http(self, path, data=None, auth=True, headers=None):
        if not hasattr(self, '_server'):
            self._server = ThreadingHTTPServer(('127.0.0.1', 0), handler(self.runtime, 'fixture-token'))
            thread = threading.Thread(target=self._server.serve_forever, daemon=True);thread.start()
            self.addCleanup(thread.join);self.addCleanup(self._server.server_close);self.addCleanup(self._server.shutdown)
        h = {'Authorization': 'Bearer fixture-token'} if auth else {}
        if data is not None:h['Content-Type'] = 'application/json'
        h.update(headers or {})
        req = Request(f'http://127.0.0.1:{self._server.server_port}' + path,
                      data=canonical(data) if data is not None else None, headers=h)
        with urlopen(req) as response:return json.load(response)

    def test_readonly_nonrepo_workspace_can_read_but_cannot_execute(self):
        folder = self.root / 'notes';folder.mkdir()
        path = native(folder / 'nested/.claude', 'claude', folder);append_messages(path, 'claude')
        row = self.runtime.open_workspace(str(folder))
        self.assertFalse(row['execution_allowed']);self.assertEqual(row['environment'], 'local')
        sessions = self.runtime.sessions(row['id'])['discovery']['sessions']
        self.assertEqual(len(sessions), 1)
        history = self.runtime.session_center.history(row['id'], sessions[0]['handle'], query='hello')
        self.assertEqual(history['messages'], [dict(role='user', text='VISIBLE hello')])
        request = self.chat_request();request['workspace'] = row['id']
        with self.assertRaises(Blocked):self.runtime.session_center.prepare(request)

    def test_readonly_outside_allowed_roots_never_grants_execution(self):
        folder = self.root / 'outside';folder.mkdir()
        row = self.runtime.open_workspace(str(folder))
        self.assertFalse(row['execution_allowed'])
        with self.assertRaises(Blocked):self.runtime.project(row['id'])

    def test_http_history_requires_handle_auth_and_workspace(self):
        path, row = self.native_profile()
        before = tree_hash(path.parents[2])
        value = self.http('/api/session/history?' + urlencode(dict(workspace=self.project['id'], handle=row['handle'], query='hello')))
        self.assertEqual(value['messages'], [dict(role='user', text='VISIBLE hello')])
        for query in (dict(workspace=self.project['id'], path=str(path)), dict(workspace=self.project['id'], handle=str(path))):
            with self.assertRaises(HTTPError):self.http('/api/session/history?' + urlencode(query))
        with self.assertRaises(HTTPError) as error:self.http('/api/sessions?workspace=' + self.project['id'], auth=False)
        self.assertEqual(error.exception.code, 403)
        folder = self.root / 'other';folder.mkdir();other = self.runtime.open_workspace(str(folder))
        with self.assertRaises(Blocked):self.runtime.session_center.history(other['id'], row['handle'])
        self.assertEqual(before, tree_hash(path.parents[2]))

    def test_external_and_default_history_never_become_writers(self):
        _, row = self.native_profile()
        self.assertFalse(row['resume'])
        with self.assertRaisesRegex(Blocked, 'read only'):
            self.runtime.session_center.prepare(self.chat_request(row))

    def test_independent_controller_reviewer_history_preserves_original_candidate_cwd(self):
        store = Store(self.runtime.control);self.addCleanup(store.db.close)
        home = self.runtime.native / 'independent-reviewer'
        p = self.runtime.operator['profiles']['default-claude']
        binding = dict(p, project=self.project['id'], role='reviewer', home=str(home), session_id=None,
                       runtime_profile='default-claude', isolation='bwrap')
        run = store.create(dict(source={'root': str(self.source)}, sessions={'reviewer': SID},
                               config={'bindings': {'reviewer': binding}}))
        store.transition(run, 'READY')
        cwd = self.runtime.control / run / 'round-1/candidate';cwd.mkdir(parents=True)
        (cwd / 'file.py').write_text('# independent fixed candidate\n')
        path = native(home / '.claude', 'claude', cwd);append_messages(path, 'claude')
        before = tree_hash(cwd)
        center = self.runtime.session_center
        rows = center.discover(self.project['id'])['discovery']['sessions']
        reviewer = next(row for row in rows if row['session_id']==SID and row['provider']=='claude')
        self.assertEqual(reviewer['association'], 'registered_origin')
        self.assertIn('VISIBLE answer', json.dumps(center.history(self.project['id'], reviewer['handle'])))
        preview = center.prepare(self.chat_request(reviewer))['data']
        self.assertEqual(preview['cwd'], str(cwd));self.assertEqual(preview['binding']['session_id'], SID)
        with self.assertRaisesRegex(Blocked, 'only be continued with read'):
            center.prepare(dict(self.chat_request(reviewer), permission='workspace-write'))
        with path.open('a') as f:
            f.write(json.dumps(dict(type='user',sessionId=SID,cwd=str(cwd.parent/'candidate-2'),
                message=dict(role='user',content='Visible subsequent fixed candidate')))+'\n')
        latest = next(row for row in center.discover(self.project['id'])['discovery']['sessions'] if row['session_id']==SID)
        self.assertTrue(latest['capabilities']['read']);self.assertFalse(latest['resume'])
        self.assertIn('subsequent fixed candidate', json.dumps(center.history(self.project['id'], latest['handle'])))
        self.assertEqual(before, tree_hash(cwd))

    def test_transcript_cannot_disclose_messages_from_a_different_workspace_cwd(self):
        for provider in ('codex','claude'):
            with self.subTest(provider=provider):
                path, row = self.native_profile(provider, managed=True)
                outside = self.root / 'other-project'
                context = (dict(type='turn_context',payload=dict(cwd=str(outside))) if provider=='codex' else
                           dict(type='user',sessionId=SID,cwd=str(outside)))
                with path.open('a') as f:
                    f.write(json.dumps(context)+'\n')
                center = self.runtime.session_center
                current = next(x for x in center.discover(self.project['id'])['discovery']['sessions']
                               if x['handle']==row['handle'])
                self.assertFalse(current['capabilities']['read']);self.assertFalse(current['resume'])
                with self.assertRaisesRegex(Blocked, 'outside the approved'):
                    center.history(self.project['id'], row['handle'])

    def test_registered_mapping_mismatch_is_metadata_only(self):
        home = self.root / 'mapped-home';root = home / '.claude'
        path = native(root,'claude',self.root/'outside-mapping');append_messages(path,'claude')
        operator = copy.deepcopy(self.runtime.operator)
        operator['session_profiles'] = [dict(provider='claude',home=str(home),config_root=str(root),
            workspace=str(self.source),original_project_root=str(self.root/'approved-old-candidate'))]
        save(self.operator_file,operator);self.runtime=Runtime(self.operator_file,digest(self.operator_file.read_bytes()))
        center=self.runtime.session_center;row=center.discover(self.project['id'])['discovery']['sessions'][0]
        self.assertFalse(row['capabilities']['read']);self.assertEqual(row['association'],'mapping_mismatch')
        with self.assertRaisesRegex(Blocked,'outside the approved'):
            center.history(self.project['id'],row['handle'])
        self.assertTrue(center.visibility(self.project['id'],row['handle'],True)['hidden'])

    def test_hide_unhide_persist_without_native_writes_or_source_deletion(self):
        path, row = self.native_profile();before = tree_hash(path.parents[2])
        center = self.runtime.session_center
        center.visibility(self.project['id'], row['handle'], True)
        self.assertEqual(center.discover(self.project['id'])['discovery']['sessions'], [])
        reopened = Runtime(self.operator_file, digest(self.operator_file.read_bytes())).session_center
        hidden = reopened.discover(self.project['id'], True)['discovery']['sessions']
        self.assertTrue(hidden[0]['hidden'])
        reopened.visibility(self.project['id'], row['handle'], False)
        self.assertEqual(len(reopened.discover(self.project['id'])['discovery']['sessions']), 1)
        self.assertEqual(before, tree_hash(path.parents[2]));self.assertTrue(self.source.is_dir())

    def test_http_native_delete_archive_and_arbitrary_argv_are_denied(self):
        path, row = self.native_profile();before = tree_hash(path.parents[2])
        for route in ('delete', 'archive', 'unarchive'):
            with self.assertRaises(HTTPError) as error:
                self.http('/api/session/' + route, dict(workspace=self.project['id'], handle=row['handle'], confirmed=True))
            self.assertEqual(error.exception.code, 400)
        with self.assertRaises(HTTPError):
            self.http('/api/session/prepare', dict(self.chat_request(), argv=['rm', '-rf', '/']))
        self.assertEqual(before, tree_hash(path.parents[2]));self.assertTrue(self.source.is_dir())

    def test_read_profile_registration_does_not_authorize_external_roots(self):
        center = self.runtime.session_center
        with self.assertRaises(Blocked):center.register(self.project['id'], 'claude', str(self.root))
        root = self.source / 'isolated/.claude';native(root, 'claude', self.source)
        value = center.register(self.project['id'], 'claude', str(root))
        self.assertFalse(value['execution_authorized'])
        self.assertFalse(center.discover(self.project['id'])['discovery']['sessions'][0]['resume'])

    def test_http_registered_roots_keep_same_uuid_histories_and_visibility_independent(self):
        approved = self.root / 'approved-profile-stores'
        paths, roots = {}, {}
        for provider in ('codex', 'claude'):
            for name in ('alpha', 'beta'):
                root = approved / provider / name / ('.' + provider)
                path = native(root, provider, self.source)
                marker = provider + '-' + name + '-visible-history'
                message = (dict(type='response_item', payload=dict(type='message', role='user',
                    content=[dict(type='input_text', text=marker)])) if provider=='codex' else
                    dict(type='user', sessionId=SID, message=dict(role='user', content=marker)))
                with path.open('a') as f:f.write(json.dumps(message)+'\n')
                roots[provider, name], paths[path] = root, path.read_bytes()
        operator = copy.deepcopy(self.runtime.operator)
        operator['session_read_roots'] = [str(approved)]
        save(self.operator_file, operator)
        self.runtime = Runtime(self.operator_file, digest(self.operator_file.read_bytes()))
        source_before = tree_hash(self.source)
        for provider in ('codex', 'claude'):
            with self.subTest(provider=provider):
                registrations = [self.http('/api/session/register', dict(workspace=self.project['id'],
                    provider=provider, config_root=str(roots[provider, name]))) for name in ('alpha','beta')]
                self.assertNotEqual(registrations[0]['id'], registrations[1]['id'])
                repeated = self.http('/api/session/register', dict(workspace=self.project['id'],
                    provider=provider, config_root=str(roots[provider,'alpha'])))
                self.assertEqual(repeated['id'], registrations[0]['id'])
                self.assertTrue(all(not x['execution_authorized'] for x in registrations))
                rows = self.http('/api/sessions?workspace='+self.project['id'])['discovery']['sessions']
                rows = [x for x in rows if x['provider']==provider]
                self.assertEqual(len(rows), 2)
                self.assertEqual({x['session_id'] for x in rows}, {SID})
                self.assertEqual(len({x['handle'] for x in rows}), 2)
                self.assertEqual(len({x['profile_id'] for x in rows}), 2)
                by_root = {x['storage_root']:x for x in rows}
                alpha, beta = [by_root[str(roots[provider,name])] for name in ('alpha','beta')]
                for name, row in [('alpha',alpha),('beta',beta)]:
                    query = dict(workspace=self.project['id'], handle=row['handle'],query=provider+'-'+name+'-visible-history')
                    messages = self.http('/api/session/history?'+urlencode(query))['messages']
                    self.assertEqual(messages,[dict(role='user',text=provider+'-'+name+'-visible-history')])
                    query['query'] = provider+'-'+('beta' if name=='alpha' else 'alpha')
                    self.assertEqual(self.http('/api/session/history?'+urlencode(query))['messages'],[])
                    self.assertFalse(row['resume'])
                    with self.assertRaises(HTTPError):
                        self.http('/api/session/prepare',self.chat_request(row,provider))
                self.http('/api/session/visibility',dict(workspace=self.project['id'],handle=alpha['handle'],hidden=True))
                visible = self.http('/api/sessions?workspace='+self.project['id'])['discovery']['sessions']
                self.assertIn(beta['handle'],{x['handle'] for x in visible})
                self.assertNotIn(alpha['handle'],{x['handle'] for x in visible})
                reopened = Runtime(self.operator_file,digest(self.operator_file.read_bytes())).session_center
                persisted = reopened.discover(self.project['id'],True)['discovery']['sessions']
                self.assertTrue(next(x for x in persisted if x['handle']==alpha['handle'])['hidden'])
                self.assertFalse(next(x for x in persisted if x['handle']==beta['handle'])['hidden'])
                reopened.visibility(self.project['id'],alpha['handle'],False)
        self.assertEqual(len(self.runtime.db.listing('session-registration')),4)
        self.assertEqual(source_before,tree_hash(self.source))
        self.assertTrue(all(path.read_bytes()==before for path,before in paths.items()))

    def test_resume_preview_preserves_exact_profile_cwd_uuid_and_tools(self):
        path, row = self.native_profile('codex', managed=True)
        value = self.runtime.session_center.prepare(self.chat_request(row, 'codex'))
        b = value['data']['binding']
        self.assertEqual(b['session_id'], SID);self.assertEqual(b['home'], str(self.runtime.native / 'dedicated'))
        self.assertEqual(value['data']['cwd'], str(self.source));self.assertEqual(b['tools'], ['native'])
        self.assertEqual(b['role'], 'reviewer');self.assertEqual(b['isolation'], 'bwrap')
        self.assertEqual(b['model'], self.runtime.operator['profiles']['default-codex']['model'])
        self.assertNotIn('--last', json.dumps(value));self.assertEqual(value['data']['native_sha256'], digest(path.read_bytes()))

    def test_live_home_lease_disables_resume_and_prevents_takeover(self):
        _, row = self.native_profile(managed=True)
        home = self.runtime.native / 'dedicated'
        with lease(home.parent / (digest(str(home.resolve()).encode()) + '.session.lock')):
            listing = self.runtime.sessions(self.project['id'])['discovery']['sessions']
            self.assertFalse(listing[0]['resume'])
            with self.assertRaisesRegex(Blocked, 'occupied'):
                self.runtime.session_center.prepare(self.chat_request(row))

    def test_unknown_uuid_in_managed_home_remains_readonly(self):
        path, _ = self.native_profile(managed=True)
        native(path.parents[2], 'claude', self.source, OTHER)
        listing = self.runtime.sessions(self.project['id'])['discovery']['sessions']
        other = next(row for row in listing if row['session_id'] == OTHER)
        self.assertFalse(other['resume'])
        with self.assertRaisesRegex(Blocked, 'UUID'):
            self.runtime.session_center.prepare(self.chat_request(other))

    def test_duplicate_uuid_in_one_profile_cannot_select_a_history_or_writer(self):
        path, row = self.native_profile('codex', managed=True)
        duplicate = path.parent.parent / '21' / path.name
        duplicate.parent.mkdir();duplicate.write_bytes(path.read_bytes())
        center = self.runtime.session_center
        current = next(x for x in center.discover(self.project['id'])['discovery']['sessions'] if x['handle']==row['handle'])
        self.assertFalse(current['resume']);self.assertFalse(current['capabilities']['read'])
        with self.assertRaisesRegex(Conflict,'ambiguous'):
            center.history(self.project['id'], row['handle'])

    def test_search_runs_after_known_credential_redaction(self):
        path, row = self.native_profile()
        secret = 'opaque-fixture-credential-value'
        (path.parents[2] / 'settings.json').write_text(json.dumps({'apiKey': secret}))
        with path.open('a') as f:
            f.write(json.dumps(dict(type='assistant', sessionId=SID,
                message=dict(role='assistant', content='Visible answer containing '+secret)))+'\n')
        center = self.runtime.session_center
        self.assertEqual(center.history(self.project['id'], row['handle'], query=secret)['messages'], [])
        rendered = json.dumps(center.history(self.project['id'], row['handle'], query='containing'))
        self.assertIn('[REDACTED]', rendered);self.assertNotIn(secret, rendered)

    def test_history_redacts_unmounted_authentication_overlays_and_refuses_project_auth_links(self):
        path, row = self.native_profile(managed=True)
        secret = 'approved-overlay-credential-fixture'
        source = Path(self.runtime.operator['profiles']['default-claude']['readonly_files'][0][0])
        source.write_text(json.dumps({'apiKey':secret}))
        # The fixture's pinned dependency set is empty; no real auth is modified.
        with path.open('a') as f:
            f.write(json.dumps(dict(type='assistant',sessionId=SID,
                message=dict(role='assistant',content='Visible bare value '+secret)))+'\n')
        (path.parents[2]/'settings.json').symlink_to(source)
        from studio.session_center import credential_bytes
        with self.assertRaises(ValueError):credential_bytes(path.parents[2]/'settings.json')
        center=self.runtime.session_center
        rendered=json.dumps(center.history(self.project['id'],row['handle']))
        self.assertIn('[REDACTED]',rendered);self.assertNotIn(secret,rendered)
        self.assertEqual(center.history(self.project['id'],row['handle'],query=secret)['messages'],[])

    def test_profile_overflow_reports_partial_instead_of_failing_discovery(self):
        center = self.runtime.session_center
        profiles = [dict(provider='claude', config_root=str(self.source / ('p'+str(i)) / '.claude'),
                         home=None, origin='registered') for i in range(40)]
        with patch.object(center, '_profiles', return_value=(profiles, {}, [])):
            result = center.discover(self.project['id'])
        report = result['discovery']['profile_coverage']
        self.assertEqual(report['status'], 'partial');self.assertEqual(report['scanned'], 32)
        self.assertEqual(report['registered'], 40)

    def test_old_pending_home_is_not_hidden_by_other_workspaces_recent_history(self):
        center = self.runtime.session_center
        self.runtime.db.put('session-operation', 'old', dict(workspace=self.project['id'],
            binding=dict(home='old-home'), state='RUNNING'))
        for i in range(101):
            self.runtime.db.put('session-operation', str(i), dict(workspace='other',
                binding=dict(home='unrelated-home'), state='COMPLETED'))
        self.assertTrue(center._busy('old-home'))

    def test_native_request_journal_binds_only_exact_observed_uuid(self):
        center = self.runtime.session_center
        for provider in ('codex', 'claude'):
            value = center.prepare(self.chat_request(provider=provider))['data']
            key = provider+'-journal'
            self.runtime.db.put('session-operation', key, dict(value, id=key, state='QUEUED'))
            journal = SessionJournal(self.runtime.db, key)
            op = journal.intent(key, 'AGENT_REQUEST', dict(requested_session=SID if provider=='claude' else None))
            process = journal.intent(key, 'PROCESS', {})
            journal.finish(process, 'RUNNING', process=None)
            if provider == 'codex':journal.finish(op, 'AGENT_SESSION', actual_session=SID)
            binding = self.runtime.db.get('chat-profile', digest(value['binding']['home'].encode()))['binding']
            self.assertEqual(binding['session_id'], SID)

    def test_new_workflow_cannot_reuse_session_in_a_different_snapshot_cwd(self):
        request = self.request()
        next(n for n in request['workflow']['nodes'] if n['type']=='developer')['config']['session'] = SID
        with self.assertRaisesRegex(Blocked, 'new isolated cwd'):
            self.runtime.prepare(request)

    def test_source_and_native_changes_invalidate_preview_before_spawn(self):
        path, row = self.native_profile(managed=True);center = self.runtime.session_center
        preview = center.prepare(self.chat_request(row));append_messages(path, 'claude')
        with self.assertRaisesRegex(Conflict, 'Native session changed'), patch.object(center, '_spawn') as spawn:
            center.launch(preview['id'], preview['sha256'], 'Approved exact turn')
        spawn.assert_not_called()
        preview = center.prepare(self.chat_request(row));(self.source / 'README.md').write_text('changed')
        with self.assertRaisesRegex(Conflict, 'Workspace changed'), patch.object(center, '_spawn') as spawn:
            center.launch(preview['id'], preview['sha256'], 'Approved exact turn')
        spawn.assert_not_called()

    def test_expired_or_missing_confirmation_cannot_spawn(self):
        center = self.runtime.session_center;preview = center.prepare(self.chat_request())
        with self.assertRaises(Invalid):center.launch(preview['id'], preview['sha256'], '')
        with self.runtime.db.db() as db, db:db.execute('UPDATE permits SET at=0 WHERE id=?', (preview['id'],))
        with self.assertRaises(Conflict), patch.object(center, '_spawn') as spawn:
            center.launch(preview['id'], preview['sha256'], 'Approved')
        spawn.assert_not_called()

    def test_managed_turn_cli_failure_is_recorded_and_never_success(self):
        center = self.runtime.session_center;preview = center.prepare(self.chat_request())
        value = preview['data'];op = dict(value, id='a'*32, state='QUEUED', cancel_requested=False, created_at=time.time())
        self.runtime.db.put('session-operation', op['id'], op)
        with patch('studio.session_center.CLI.start', side_effect=Blocked('CLI authentication failed')):
            center.execute(op['id'])
        status = center.operation(op['id'], self.project['id'])
        self.assertEqual(status['state'], 'FAILED');self.assertIn('authentication', status['reason']);self.assertIsNone(status['receipt'])

    def test_error_envelope_with_zero_exit_is_failed_authentication_in_operation_http(self):
        center = self.runtime.session_center
        value = center.prepare(self.chat_request())['data']
        key = 'c'*32
        self.runtime.db.put('session-operation',key,dict(value,id=key,state='QUEUED',
            cancel_requested=False,created_at=time.time()))
        raw = canonical(dict(type='result',subtype='error_during_execution',is_error=True,
            session_id=SID,result='API Error: authentication_error: invalid authentication token'))
        process = dict(exit=0,timeout=False,overflow=False,cancelled=False)
        with patch('orchestration.adapters.execute',return_value=(process,raw,b'')):
            center.execute(key)
        status = self.http('/api/session/operation?'+urlencode(dict(workspace=self.project['id'],id=key)))
        self.assertEqual(status['state'],'FAILED')
        self.assertEqual(status['error_category'],'authentication')
        self.assertIsNone(status['receipt']);self.assertIsNone(status['reply'])

    def test_worker_loss_is_recovery_required_and_cancel_owns_exact_process(self):
        center = self.runtime.session_center;value = center.prepare(self.chat_request())['data']
        proc = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(30)'], start_new_session=True)
        self.addCleanup(lambda: proc.kill() if proc.poll() is None else None)
        op = dict(value, id='b'*32, state='RUNNING', cancel_requested=False, created_at=time.time()-10, worker=None)
        self.runtime.db.put('session-operation', op['id'], op)
        self.runtime.db.put('session-step', 'process', dict(id='process', operation=op['id'], kind='PROCESS', status='RUNNING', data=dict(process=process_identity(proc.pid))))
        self.assertEqual(center.operation(op['id'], self.project['id'])['state'], 'RECOVERY_REQUIRED')
        result = center.cancel(self.project['id'], op['id']);proc.wait(timeout=5)
        self.assertEqual(result['state'], 'CANCELLED');self.assertFalse(result['alive'])
        self.assertNotEqual(proc.returncode, 0)

    def test_v2_provider_swaps_bind_actual_adapter_and_keep_mandatory_gates(self):
        request = self.request()
        for n in request['workflow']['nodes']:
            if n['type'] in ('developer', 'reviewer'):
                provider = 'claude' if n['type'] == 'developer' else 'codex'
                n['config'].update(provider=provider, profile='default-' + provider, tools=TOOLS[provider][:])
        result = self.runtime.prepare(request)['data']
        self.assertEqual(result['config']['policy']['agents']['reviewer']['adapter'], 'codex-cli')
        self.assertEqual(result['config']['bindings']['implementer']['provider'], 'claude')
        self.assertTrue({'freeze', 'verify', 'independent-review', 'delivery'} <= set(result['workflow']['runtime_gates']))
        config_check(result['config'])
        config = copy.deepcopy(result['config']);config['bindings']['reviewer']['isolation'] = 'landlock'
        with self.assertRaises(Blocked):config_check(config)
        legacy = copy.deepcopy(request['workflow']);legacy['version'] = 1
        with self.assertRaises(Invalid):compile_plan(legacy)

    def development_runtime(self, session_read_roots=()):
        operator = dict(version=1, kind='development-sessions', storage=str(self.root / 'session-studio'),
            native=str(self.root / 'session-native'), allowed_roots=[str(self.source)],
            profiles=self.runtime.operator['profiles'], dependencies={})
        if session_read_roots:
            operator['session_read_roots'] = list(session_read_roots)
        for p in operator['profiles'].values():
            for path in [p['executable'], *[src for src, _ in p['readonly_files']]]:
                operator['dependencies'][path] = digest(Path(path).read_bytes())
        config = self.root / 'session-operator.json';save(config, operator)
        return SessionRuntime(config, digest(config.read_bytes()), source_tree_hash(Path(__file__).resolve().parents[1]))

    def test_development_host_has_no_stable_identity_or_controller_authorization(self):
        self.runtime = self.development_runtime()
        self.assertFalse(hasattr(self.runtime, 'trust'));self.assertFalse(hasattr(self.runtime, 'control'))
        row = self.runtime.open_workspace(str(self.source))
        status = self.http('/api/status')
        self.assertEqual(status['execution_context'], 'development-sessions')
        self.assertIsNone(status['stable']);self.assertEqual(status['runs'], [])
        self.assertTrue(row['execution_allowed'])
        for route in ('prepare', 'authorize-run', 'resume', 'approve-result', 'cancel'):
            with self.assertRaises(HTTPError) as error:self.http('/api/' + route, {})
            self.assertEqual(error.exception.code, 400)
        self.assertFalse((self.runtime.storage / 'state.sqlite').exists())
        self.assertFalse((self.runtime.storage / 'approval.json').exists())

    def test_development_dependency_drift_is_blocked_without_new_approval_records(self):
        runtime = self.development_runtime()
        credential = Path(runtime.operator['profiles']['default-claude']['readonly_files'][0][0])
        credential.write_text('{"changed":"fixture"}')
        with self.assertRaisesRegex(Blocked, 'configuration changed'):runtime.check_operator()

    def test_development_detached_worker_preserves_candidate_pin(self):
        runtime = self.development_runtime()
        with patch('studio.session_center.subprocess.Popen') as spawn:
            runtime.session_center._spawn('f' * 32)
        argv = spawn.call_args.args[0]
        self.assertIn('--candidate-sha256', argv)
        self.assertEqual(argv[argv.index('--candidate-sha256') + 1], runtime.candidate_sha256)
        self.assertNotIn('--stable', argv)


class NativeAdapterBoundaryTests(unittest.TestCase):
    def test_native_auth_failure_variants_are_classified_without_rejecting_successful_chat(self):
        class Journal:
            def __init__(self):self.events=[]
            def intent(self,*args):return 'request'
            def finish(self,key,status,**data):self.events.append((status,data))
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);artifacts=root/'artifacts';artifacts.mkdir()
            binding=dict(executable='/usr/bin/true',home=str(root/'home'),role='reviewer',
                project='fixture',model='test-model',effort='high',session_id=SID,read_paths=[],
                readonly_files=[],isolation='bwrap',tools=['Read','Glob','Grep'])
            failures=[(0,canonical(dict(type='result',is_error=True,subtype='error_during_execution',
                            result='API Error: 403 invalid credentials')),b''),
                      (1,b'',b'API key is invalid'),
                      (1,b'',b'authentication_error: invalid x-api-key')]
            for code,out,err in failures:
                for timed_out in (False,True):
                    with self.subTest(exit=code,wire=out or err,timeout=timed_out):
                        journal=Journal();process=dict(exit=code,timeout=timed_out,overflow=False,cancelled=False)
                        category='timeout' if timed_out else 'authentication'
                        with patch('orchestration.adapters.execute',return_value=(process,out,err)):
                            with self.assertRaisesRegex(Blocked,'CLI '+category+';'):
                                CLI('claude',binding,dict(step_timeout_seconds=30,max_log_bytes=100000)).start(
                                    'fixture',root,artifacts,store=journal,run='fixture')
                        self.assertEqual(journal.events[-1][1]['error_category'],category)
                        self.assertEqual(journal.events[-1][1]['process']['timeout'],timed_out)
                        self.assertFalse(any(state=='AGENT_COMPLETED' for state,_ in journal.events))
            journal=Journal()
            with patch('orchestration.adapters.execute',return_value=(dict(exit=-15,timeout=False,
                       overflow=False,cancelled=True),b'',b'API Error: 401')):
                with self.assertRaisesRegex(Blocked,'cancelled'):
                    CLI('claude',binding,dict(step_timeout_seconds=30,max_log_bytes=100000)).start(
                        'fixture',root,artifacts,store=journal,run='fixture')
            self.assertEqual(journal.events[-1][1]['error_category'],'cancelled')
            success=canonical(dict(type='result',is_error=False,subtype='success',session_id=SID,
                structured_output={'reply':'A 401 authentication_error is an example, not this request status.'}))
            with patch('orchestration.adapters.execute',return_value=(dict(exit=0,timeout=False,
                       overflow=False,cancelled=False),success,b'')):
                reply=CLI('claude',binding,dict(step_timeout_seconds=30,max_log_bytes=100000)).start('fixture',root,artifacts)
            self.assertIn('401',reply['report']['reply'])

    def test_supervised_stop_precedes_authentication_in_native_output(self):
        for provider in ('codex','claude'):
            for flags,category in ((dict(cancelled=True,timeout=True,overflow=True),'cancelled'),
                                   (dict(timeout=True,overflow=False),'timeout'),
                                   (dict(timeout=True,overflow=True),'timeout'),
                                   (dict(overflow=True),'output-budget')):
                with self.subTest(provider=provider,flags=flags):
                    # Representative timeout=-15 with source/test text mentioning 401.
                    source=canonical(dict(type='item.completed',item=dict(type='command_execution',
                        aggregated_output='AUTH_FAILURE = 401; test genuine 403 authentication')))+b'\n{"type":'
                    error=(canonical(dict(type='error',message='HTTP 401 unauthorized')) if provider=='codex' else
                           canonical(dict(type='result',is_error=True,result='HTTP 403 unauthorized')))
                    process=dict(exit=-15,timeout=False,overflow=False,cancelled=False);process.update(flags)
                    for raw,err in ((source,b''),(error,b'HTTP 401 unauthorized')):
                        self.assert_failed_category(provider,process,raw,err,category)

    def assert_failed_category(self,provider,process,out,err,category):
        class Journal:
            def __init__(self):self.events=[]
            def intent(self,*args):return 'request'
            def finish(self,key,status,**data):self.events.append((status,data))
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);artifacts=root/'artifacts';artifacts.mkdir()
            binding=dict(executable='/usr/bin/true',home=str(root/'home'),role='reviewer',
                project='fixture',model='test-model',effort='high',session_id=SID,read_paths=[],
                readonly_files=[],isolation='bwrap',external_sandbox=provider=='codex',tools=['Read','Glob','Grep'])
            journal=Journal()
            with patch('orchestration.adapters.execute',return_value=(process,out,err)):
                with self.assertRaisesRegex(Blocked,'CLI '+category+';' if category!='protocol' else 'Codex|Claude|structured'):
                    CLI(provider,binding,dict(step_timeout_seconds=30,max_log_bytes=100000)).start(
                        'fixture',root,artifacts,store=journal,run='fixture')
            self.assertEqual(journal.events[-1][1]['error_category'],category)
            self.assertEqual(journal.events[-1][1]['process'],process)
            self.assertFalse(any(state=='AGENT_COMPLETED' for state,_ in journal.events))

    def test_source_tool_and_success_text_cannot_classify_authentication(self):
        for provider in ('codex','claude'):
            if provider=='codex':
                raw=b'\n'.join(canonical(event) for event in [
                    dict(type='thread.started',thread_id=SID),
                    dict(type='item.completed',item=dict(type='command_execution',
                        aggregated_output='source: {"type":"error","message":"401 unauthorized"}')),
                    dict(type='item.completed',item=dict(type='agent_message',text='401 is a test example')),
                    dict(type='turn.failed',error=dict(message='provider temporarily unavailable'))])
            else:
                raw=canonical(dict(type='result',is_error=False,subtype='success',session_id=SID,
                    result='Example: 401 unauthorized',structured_output=['invalid report; test 403']))
            for code,category in ((1,'process-failure'),(0,'protocol')):
                with self.subTest(provider=provider,exit=code):
                    self.assert_failed_category(provider,dict(exit=code,timeout=False,overflow=False,cancelled=False),raw,b'',category)

    def test_native_error_envelopes_and_stderr_preserve_401_and_403(self):
        for provider in ('codex','claude'):
            for code in (0,1):
                for status in (401,403):
                    if provider=='codex':
                        wires=[canonical(dict(type='error',message=f'HTTP {status}')),
                               b'partial non-JSON line\n'+canonical(dict(type='turn.failed',error=dict(message=f'HTTP {status}')))]
                    else:
                        wires=[canonical(dict(type='result',is_error=True,subtype='error_during_execution',result=f'HTTP {status}')),
                               canonical(dict(type='result',is_error=True,errors=[f'HTTP {status}']))]
                    for raw in wires:
                        with self.subTest(provider=provider,exit=code,status=status,raw=raw):
                            self.assert_failed_category(provider,dict(exit=code,timeout=False,overflow=False,cancelled=False),raw,b'','authentication')
            self.assert_failed_category(provider,dict(exit=1,timeout=False,overflow=False,cancelled=False),b'',b'HTTP 401 unauthorized','authentication')

    def test_live_native_uuid_is_journaled_before_turn_completion(self):
        class Journal:
            def __init__(self):self.events=[]
            def intent(self, *args):return 'request'
            def finish(self, key, status, **data):self.events.append((status, data))
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);artifacts=root/'artifacts';artifacts.mkdir()
            binding=dict(executable='/usr/bin/true',home=str(root/'home'),role='reviewer',project='fixture',
                         model='test-model',effort='high',session_id=SID,read_paths=[],readonly_files=[],
                         isolation='bwrap',external_sandbox=True)
            start=canonical(dict(type='thread.started',thread_id=SID))+b'\n'
            raw=start+canonical(dict(type='item.completed',item=dict(type='agent_message',text='{"reply":"fixture"}')))+b'\n'+canonical(dict(type='turn.completed',usage={}))
            journal=Journal()
            def execute(*args, **kwargs):
                kwargs['on_stdout'](start[:10]);kwargs['on_stdout'](start[10:])
                self.assertEqual(journal.events[0],('AGENT_SESSION',dict(actual_session=SID,resolved_model='unknown')))
                self.assertFalse(any(status=='AGENT_COMPLETED' for status,_ in journal.events))
                return dict(exit=0,timeout=False,overflow=False,cancelled=False),raw,b''
            with patch('orchestration.adapters.execute',side_effect=execute):
                CLI('codex',binding,dict(step_timeout_seconds=30,max_log_bytes=100000)).start('fixture',root,artifacts,store=journal,run='fixture')
            self.assertEqual(journal.events[-1][0],'AGENT_COMPLETED')

    def test_actual_adapter_keeps_role_permissions_and_exact_resume_for_both_providers(self):
        """Fake wire envelopes exercise real adapter parsing/argv, not native acceptance."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder);cwd = root / 'source';cwd.mkdir()
            for provider in ('codex', 'claude'):
                for role in ('implementer', 'reviewer'):
                    artifacts = root / (provider + role);artifacts.mkdir()
                    home = root / ('home-' + provider + role);home.mkdir()
                    binding = dict(provider=provider, executable='/usr/bin/true', home=str(home), project='fixture',
                        role=role, model='test-model', effort='high', session_id=SID, isolation='bwrap',
                        external_sandbox=provider == 'codex', read_paths=[], readonly_files=[],
                        tools=['native'] if provider == 'codex' else ['Read', 'Glob', 'Grep', 'Bash'])
                    report = dict(reply='visible fixture reply; examples: 401/403 authentication_error')
                    if provider == 'codex':
                        events = [dict(type='thread.started', thread_id=SID),
                            dict(type='item.completed', item=dict(type='agent_message', text=json.dumps(report))),
                            dict(type='turn.completed', usage={})]
                        raw = '\n'.join(json.dumps(e) for e in events).encode()
                    else:
                        raw = canonical(dict(type='result', subtype='success', is_error=False, session_id=SID,
                                             structured_output=report, modelUsage={'test-model': {}}, usage={}))
                    process = dict(exit=0, timeout=False, overflow=False, cancelled=False, process=None)
                    with patch('orchestration.adapters.execute', return_value=(process, raw, b'')) as execute:
                        receipt = CLI(provider, binding, dict(step_timeout_seconds=30, max_log_bytes=100000)).start('fixture', cwd, artifacts)
                    argv = execute.call_args.args[0];options = execute.call_args.kwargs
                    self.assertIn(SID, argv);self.assertNotIn('--last', argv)
                    self.assertEqual(options['backend'], 'bwrap')
                    self.assertEqual(options['write'][-1:] == [str(cwd)], role == 'implementer')
                    self.assertEqual(receipt['session_id'], SID);self.assertEqual(receipt['cwd'], str(cwd))
                    self.assertEqual(receipt['home'], str(home))
                    self.assertEqual(receipt['report'],report)

    def test_auth_failure_and_wrong_uuid_never_produce_a_success_receipt(self):
        adapter = CLI('codex', {}, {})
        result = dict(exit=1, timeout=False, overflow=False, cancelled=False)
        with self.assertRaises(Blocked):adapter.collect(result, b'authentication failed')
        result['exit'] = 0
        with self.assertRaises(Blocked):adapter.collect(result, b'{"type":"turn.completed"}')


if __name__ == '__main__':unittest.main()
