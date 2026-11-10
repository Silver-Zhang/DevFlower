"""Atomic launch authorization through real loopback HTTP and isolated SQLite."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
import hashlib
from http.client import HTTPConnection, RemoteDisconnected
import json
import multiprocessing
from pathlib import Path
import signal
import sqlite3
from tempfile import TemporaryDirectory
from threading import Barrier, Event, Thread
from unittest import TestCase
from unittest.mock import patch

from v2 import runtime as launch_runtime
from v2.server import create_server
from v2.store import DraftStore


KEY = 'SYNTHETIC_PRIVATE_INTENT'
TASK = 'SYNTHETIC_PRIVATE_TASK'
PRIVATE_FAILURE = 'SYNTHETIC_PRIVATE_SQL_OR_SPAWN_FAILURE'
DEFINITION_CONFLICT = {'error': '冻结执行定义与本次启动不一致，未启动任务'}
SERVICE_FAILURE = {'error': '本机读写失败，当前草稿仍保留在编辑器；请核对目录与权限后重试'}


def compact(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def sha(value):
    return hashlib.sha256(value).hexdigest()


class SyntheticProvider:
    def __init__(self, store, executable=None):
        pass

    def binding(self, workspace, node):
        return dict(provider='codex', executable='/synthetic/never-executed',
                    home='/synthetic/private-home', config_root='/synthetic/private-config',
                    model=node['model'], effort=node['effort'], _executable_sha256='a' * 64)


def runtime_for(store):
    with patch('v2.runtime.CodexProvider', SyntheticProvider):
        return launch_runtime.V2Runtime(store)


def request(endpoint, path, payload=None):
    port, token = endpoint
    headers = {'Authorization': 'Bearer ' + token}
    body = None
    if payload is not None:
        headers['Content-Type'] = 'application/json'
        body = compact(payload)
    with closing(HTTPConnection('127.0.0.1', port, timeout=12)) as conn:
        conn.request('POST' if body is not None else 'GET', '/api/v2/' + path, body=body, headers=headers)
        response = conn.getresponse()
        return response.status, json.loads(response.read())


def killable_service(state, projects, sender):
    """Fixture process, paused after real event INSERT with the writer open."""
    store = DraftStore(state, [projects])
    runtime = runtime_for(store)
    def unexpected_spawn(_):
        raise AssertionError('unexpected synthetic spawn')

    runtime.spawn = unexpected_spawn
    server, token = create_server(store, runtime=runtime)
    sender.send(('ready', (server.server_port, token)))
    append = launch_runtime._append_run_authorized

    def pause_after_insert(db, run, reserved_at):
        append(db, run, reserved_at)
        event_count = db.execute('SELECT count(*) FROM events WHERE run=?', (run['id'],)).fetchone()[0]
        sender.send(('uncommitted', (run['id'], db.in_transaction, event_count)))
        if not Event().wait(15):
            raise sqlite3.OperationalError('fixture child was not interrupted')

    with patch.object(launch_runtime, '_append_run_authorized', side_effect=pause_after_insert):
        server.serve_forever(poll_interval=0.03)


class LaunchEventTests(TestCase):
    def setUp(self):
        temp = TemporaryDirectory(prefix='devflower-v2-launch-event-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.projects = self.root / 'projects'
        (self.projects / 'A').mkdir(parents=True)
        (self.projects / 'B').mkdir()
        self.store = DraftStore(self.root / 'state', [self.projects])
        self.database = self.store.state / 'v2.sqlite'
        self.workspace = self.store.open_workspace(str(self.projects / 'A'))['id']
        self.graph = {
            'version': 1, 'name': '合成授权工作流',
            'nodes': [{'id': 'nodeA', 'name': '合成 Agent', 'provider': 'codex', 'model': 'fixture-model',
                       'effort': 'low', 'position': {'x': 0, 'y': 0},
                       'round_prompt': 'SYNTHETIC_PRIVATE_PROMPT'}],
            'edges': [],
        }
        self.draft = self.store.save(self.workspace, None, None, self.graph)
        self.runtime = runtime_for(self.store)

    def payload(self, **changes):
        payload = dict(workspace=self.workspace, draft_id=self.draft['id'], revision=self.draft['revision'],
                       intent_key=KEY, task=TASK)
        return payload | changes

    @contextmanager
    def service(self, runtime=None):
        runtime = self.runtime if runtime is None else runtime
        server, token = create_server(runtime.store, runtime=runtime)
        thread = Thread(target=server.serve_forever, kwargs={'poll_interval': 0.03}, daemon=True)
        thread.start()
        try:
            yield server.server_port, token
        finally:
            server.shutdown()
            server.server_close()
            thread.join(8)

    @contextmanager
    def reader(self):
        with closing(sqlite3.connect(self.database.as_uri() + '?mode=ro', uri=True, timeout=1)) as db:
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            yield db

    def rows(self):
        with self.reader() as db:
            return tuple(tuple(db.execute(sql).fetchall()) for sql in (
                'SELECT kind,id,typeof(data),CAST(data AS BLOB) FROM records ORDER BY kind,id',
                'SELECT * FROM launch_intents ORDER BY workspace,intent_key',
                'SELECT seq,run,CAST(data AS BLOB) FROM events ORDER BY seq',
                'SELECT id,revision,CAST(data AS BLOB) FROM drafts ORDER BY id,revision',
            ))

    def definition(self, task=TASK):
        return launch_runtime.compile_definition(self.store, self.runtime.provider, self.workspace, self.draft, task)

    def committed_fact(self, run_id):
        # One genuinely independent reader sees all four rows in the same
        # SQLite snapshot at the synthetic spawn entry.
        with self.reader() as db:
            run_raw = db.execute("SELECT data FROM records WHERE kind='run' AND id=?", (run_id,)).fetchone()[0]
            run = json.loads(run_raw)
            definition_raw = db.execute("SELECT data FROM records WHERE kind='definition' AND id=?",
                                        (run['definition_id'],)).fetchone()[0]
            reservation = db.execute('SELECT workspace,intent_key,payload_sha,run_id,definition_id,state,created '
                                     'FROM launch_intents WHERE run_id=?', (run_id,)).fetchone()
            events = db.execute('SELECT seq,run,data FROM events WHERE run=?', (run_id,)).fetchall()
        self.assertEqual(len(events), 1)
        self.assertEqual(run['state'], 'QUEUED')
        definition = json.loads(definition_raw)
        self.assertEqual(definition['id'], run['definition_id'])
        self.assertEqual(sha(compact({key: value for key, value in definition.items() if key != 'id'})), definition['id'])
        self.assertEqual(definition_raw.encode(), compact(definition))
        scope, key, payload_sha, reserved_run, definition_id, state, created = reservation
        self.assertEqual((scope, key, reserved_run, definition_id, state),
                         (run['workspace'], run['launch_intent'], run_id, definition['id'], 'RESERVED'))
        self.assertEqual(payload_sha, sha(compact(dict(workspace=scope, draft_id=run['draft_id'],
                                                      revision=run['draft_revision'], task=run['task']))))
        seq, event_run, event_raw = events[0]
        expected = dict(
            kind='run-authorized', event_version=1,
            event_id=sha(b'devflower.v2.run-authorized.v1\0' + run_id.encode('ascii')),
            run=run_id, definition_id=definition_id, aggregate_kind='run', aggregate_id=run_id,
            cause={'kind': 'launch_intent', 'reservation_run_id': run_id},
            payload={'state': 'QUEUED', 'created_at': run['created_at']}, at=created,
        )
        self.assertEqual(event_run, run_id)
        self.assertEqual(event_raw.encode(), compact(expected))
        for private in (KEY, TASK, 'SYNTHETIC_PRIVATE_PROMPT', '/synthetic/'):
            self.assertNotIn(private, event_raw)
        return dict(expected, seq=seq)

    def seed_legacy(self, run_id, key, event_fields):
        payload = self.payload(intent_key=key, task='legacy synthetic task')
        definition = self.definition(payload['task'])
        run = dict(id=run_id, run=run_id, workspace=self.workspace, definition_id=definition['id'],
                   draft_id=self.draft['id'], draft_revision=self.draft['revision'], state='FAILED',
                   created_at=1.5, task=payload['task'], launch_intent=key)
        facts = dict(payload)
        del facts['intent_key']
        with self.store.db.tx() as db:
            db.execute("INSERT OR IGNORE INTO records VALUES('definition',?,?)",
                       (definition['id'], compact(definition).decode()))
            db.execute("INSERT INTO records VALUES('run',?,?)", (run_id, compact(run).decode()))
            db.execute('INSERT INTO launch_intents VALUES(?,?,?,?,?,?,?)',
                       (self.workspace, key, sha(compact(facts)), run_id, definition['id'], 'RESERVED', 2.0))
            if event_fields is not None:
                event = dict(event_fields, kind='run-authorized', run=run_id, definition_id=definition['id'], at=2.0)
                # Old readers accept valid legacy JSON regardless of encoding style.
                db.execute('INSERT INTO events(run,data) VALUES(?,?)',
                           (run_id, json.dumps(event, ensure_ascii=False)))
        return payload

    def test_http_winner_commits_exact_fact_before_spawn_and_reuses_intent(self):
        observed = []
        with self.service() as endpoint, patch.object(self.runtime, 'spawn', side_effect=lambda run: observed.append(self.committed_fact(run))) as spawn:
            status, body = request(endpoint, 'launch', self.payload())
            self.assertEqual(status, 200)
            run_id = body['run']['id']
            self.assertEqual(body['run']['launch_intent'], KEY)
            self.assertEqual(body['events'], observed)
            self.assertEqual(self.store.db.events(run_id), observed)
            self.assertEqual(self.store.db.events(run_id, observed[0]['seq']), [])
            self.assertEqual(request(endpoint, f'run?workspace={self.workspace}&id={run_id}')[1]['events'], observed)
            before = self.rows()
            self.assertEqual(request(endpoint, 'launch', self.payload())[1]['run']['id'], run_id)
            self.assertEqual(request(endpoint, 'launch', self.payload(task='changed')),
                             (409, {'error': '同一启动键对应不同任务，未启动第二次调用'}))
            spawn.assert_called_once_with(run_id)
            self.assertEqual(self.rows(), before)

    def racing(self, endpoint, payloads):
        compile_definition = launch_runtime.compile_definition
        after_initial_lookup = Barrier(2, timeout=8)
        arrived = []

        def compile_both(*args, **kwargs):
            definition = compile_definition(*args, **kwargs)
            arrived.append(definition['id'])
            after_initial_lookup.wait()
            return definition

        with patch.object(launch_runtime, 'compile_definition', side_effect=compile_both):
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(request, endpoint, 'launch', payload) for payload in payloads]
                results = [future.result(timeout=12) for future in futures]
        self.assertEqual(len(arrived), 2, 'both first requests crossed initial lookup before either write')
        return results

    def test_concurrent_same_payloads_cross_initial_lookup_and_commit_one_fact(self):
        facts = []
        with self.service() as endpoint, patch.object(self.runtime, 'spawn', side_effect=lambda run: facts.append(self.committed_fact(run))) as spawn:
            results = self.racing(endpoint, [self.payload(), self.payload()])
            self.assertEqual([status for status, _ in results], [200, 200])
            run_ids = {body['run']['id'] for _, body in results}
            self.assertEqual(run_ids, {facts[0]['run']})
            self.assertEqual(len(facts), 1)
            spawn.assert_called_once_with(facts[0]['run'])
            records, reservations, events, _ = self.rows()
            self.assertEqual(sum(kind == 'run' for kind, *_ in records), 1)
            self.assertEqual(sum(kind == 'definition' for kind, *_ in records), 1)
            self.assertEqual(len(reservations), 1)
            self.assertEqual(len(events), 1)

    def test_concurrent_changed_payload_loses_in_second_lookup_with_legacy_409(self):
        facts = []
        with self.service() as endpoint, patch.object(self.runtime, 'spawn', side_effect=lambda run: facts.append(self.committed_fact(run))) as spawn:
            results = self.racing(endpoint, [self.payload(), self.payload(task='changed')])
            self.assertEqual(sorted(status for status, _ in results), [200, 409])
            self.assertIn((409, {'error': '同一启动键对应不同任务'}), results)
            self.assertEqual(len(facts), 1)
            spawn.assert_called_once()
            self.assertEqual(len(self.rows()[1]), 1)
            self.assertEqual(len(self.rows()[2]), 1)

    def test_same_client_key_in_distinct_workspaces_has_independent_facts(self):
        other = self.store.open_workspace(str(self.projects / 'B'))['id']
        draft = self.store.save(other, None, None, self.graph)
        with self.service() as endpoint, patch.object(self.runtime, 'spawn', side_effect=self.committed_fact) as spawn:
            first = request(endpoint, 'launch', self.payload())
            second = request(endpoint, 'launch', self.payload(workspace=other, draft_id=draft['id'], revision=draft['revision']))
            self.assertEqual((first[0], second[0]), (200, 200))
            self.assertNotEqual(first[1]['run']['id'], second[1]['run']['id'])
            self.assertNotEqual(first[1]['events'][0]['event_id'], second[1]['events'][0]['event_id'])
            self.assertEqual(request(endpoint, f"run?workspace={other}&id={first[1]['run']['id']}")[0], 409)
            self.assertEqual(spawn.call_count, 2)
            self.assertEqual(len(self.rows()[1]), 2)

    def test_save_after_compile_triggers_in_transaction_draft_cas(self):
        compiled, continue_launch = Event(), Event()
        compile_definition = launch_runtime.compile_definition

        def wait_for_save(*args, **kwargs):
            definition = compile_definition(*args, **kwargs)
            compiled.set()
            if not continue_launch.wait(8):
                raise AssertionError('missing fixture CAS release')
            return definition

        with self.service() as endpoint, patch.object(self.runtime, 'spawn') as spawn, \
                patch.object(launch_runtime, 'compile_definition', side_effect=wait_for_save):
            with ThreadPoolExecutor(max_workers=1) as pool:
                launch = pool.submit(request, endpoint, 'launch', self.payload())
                try:
                    self.assertTrue(compiled.wait(8))
                    status, saved = request(endpoint, 'drafts/save', dict(
                        workspace=self.workspace, id=self.draft['id'], expected=self.draft['revision'],
                        document=dict(self.graph, name='保存后的合成图')))
                    self.assertEqual(status, 200)
                    before = self.rows()
                finally:
                    continue_launch.set()
                self.assertEqual(launch.result(timeout=12), (409, {'error': '草稿在启动前变化，未发起调用'}))
            spawn.assert_not_called()
            self.assertEqual(self.rows(), before)
            self.assertNotEqual(saved['revision'], self.draft['revision'])

    def test_existing_canonical_definition_is_reused_for_distinct_new_intents(self):
        definition = self.definition()
        self.store.db.put('definition', definition['id'], definition)
        facts = []
        with self.service() as endpoint, patch.object(self.runtime, 'spawn', side_effect=lambda run: facts.append(self.committed_fact(run))):
            for key in (KEY, 'another-synthetic-launch-intent'):
                self.assertEqual(request(endpoint, 'launch', self.payload(intent_key=key))[0], 200)
        records, reservations, events, _ = self.rows()
        self.assertEqual([record[3] for record in records if record[0] == 'definition'], [compact(definition)])
        self.assertEqual(len(reservations), 2)
        self.assertEqual(len(events), 2)
        self.assertEqual([fact['definition_id'] for fact in facts], [definition['id']] * 2)
        self.assertLess(facts[0]['seq'], facts[1]['seq'])

    def test_corrupt_noncanonical_nontext_and_missing_definition_fail_closed(self):
        definition = self.definition()
        key, encoded = definition['id'], compact(definition)
        malformed = (
            ('different canonical object', 'TEXT', compact(dict(definition, task=PRIVATE_FAILURE))),
            ('invalid JSON', 'TEXT', b'{' + PRIVATE_FAILURE.encode()),
            ('noncanonical JSON', 'TEXT', encoded + b'\n'),
            ('duplicate key', 'TEXT', b'{"id":"duplicate",' + encoded[1:]),
            ('invalid UTF-8 TEXT', 'TEXT', b'\xff' + encoded),
            ('canonical BLOB', 'BLOB', encoded),
            ('NULL', 'BLOB', None),
        )
        with self.service() as endpoint, patch.object(self.runtime, 'spawn') as spawn:
            for label, storage_type, raw in malformed:
                with self.subTest(label=label):
                    with self.store.db.tx() as db:
                        db.execute(f"INSERT OR REPLACE INTO records VALUES('definition',?,CAST(? AS {storage_type}))",
                                   (key, raw))
                    before = self.rows()
                    self.assertEqual(request(endpoint, 'launch', self.payload()), (409, DEFINITION_CONFLICT))
                    self.assertEqual(self.rows(), before)
            with self.store.db.tx() as db:
                db.execute("DELETE FROM records WHERE kind='definition' AND id=?", (key,))
                db.execute("CREATE TRIGGER fixture_skip_definition BEFORE INSERT ON records "
                           "WHEN NEW.kind='definition' BEGIN SELECT RAISE(IGNORE); END")
            before = self.rows()
            self.assertEqual(request(endpoint, 'launch', self.payload()), (409, DEFINITION_CONFLICT))
            self.assertEqual(self.rows(), before)
            spawn.assert_not_called()

    def test_compiled_definition_with_wrong_content_digest_rolls_back(self):
        definition = dict(self.definition(), id='f' * 64)
        before = self.rows()
        with self.service() as endpoint, patch.object(self.runtime, 'spawn') as spawn, \
                patch.object(launch_runtime, 'compile_definition', return_value=definition):
            self.assertEqual(request(endpoint, 'launch', self.payload()), (409, DEFINITION_CONFLICT))
            self.assertEqual(self.rows(), before)
            spawn.assert_not_called()

    def test_faults_before_and_after_event_insert_are_invisible_and_roll_back(self):
        self.seed_legacy('1' * 32, 'legacy-synthetic-intent-with-event', {'intent_key': 'legacy-field'})
        baseline = self.rows()
        original_append = launch_runtime._append_run_authorized
        with self.service() as endpoint, patch.object(self.runtime, 'spawn') as spawn:
            for after_insert in (False, True):
                with self.subTest(after_insert=after_insert):
                    staged, release = Event(), Event()
                    recorded = []

                    def inject(db, run, reserved_at):
                        if after_insert:
                            original_append(db, run, reserved_at)
                        run_id = run['id']
                        recorded.append((
                            db.in_transaction,
                            db.execute("SELECT count(*) FROM records WHERE kind='definition' AND id=?",
                                       (run['definition_id'],)).fetchone()[0],
                            db.execute("SELECT count(*) FROM records WHERE kind='run' AND id=?", (run_id,)).fetchone()[0],
                            db.execute('SELECT count(*) FROM launch_intents WHERE run_id=?', (run_id,)).fetchone()[0],
                            db.execute('SELECT count(*) FROM events WHERE run=?', (run_id,)).fetchone()[0],
                        ))
                        staged.set()
                        if not release.wait(8):
                            raise AssertionError('missing fixture rollback release')
                        raise sqlite3.OperationalError(PRIVATE_FAILURE)

                    with patch.object(launch_runtime, '_append_run_authorized', side_effect=inject):
                        with ThreadPoolExecutor(max_workers=1) as pool:
                            result = pool.submit(request, endpoint, 'launch', self.payload())
                            try:
                                self.assertTrue(staged.wait(8))
                                self.assertEqual(recorded, [(True, 1, 1, 1, int(after_insert))])
                                self.assertEqual(self.rows(), baseline, 'independent reader observes no partial write')
                            finally:
                                release.set()
                            self.assertEqual(result.result(timeout=12), (503, SERVICE_FAILURE))
                    self.assertEqual(self.rows(), baseline)
            spawn.assert_not_called()

    def test_actual_child_kill_after_insert_before_commit_leaves_no_partial_run(self):
        self.seed_legacy('1' * 32, 'legacy-synthetic-intent-preserved', {})
        baseline = self.rows()
        context = multiprocessing.get_context('fork')
        receiver, sender = context.Pipe(duplex=False)
        child = context.Process(target=killable_service, args=(self.store.state, self.projects, sender))
        child.start()
        sender.close()
        try:
            self.assertTrue(receiver.poll(8))
            status, endpoint = receiver.recv()
            self.assertEqual(status, 'ready')
            with ThreadPoolExecutor(max_workers=1) as pool:
                result = pool.submit(request, endpoint, 'launch', self.payload())
                try:
                    self.assertTrue(receiver.poll(8))
                    label, (run_id, transaction, count) = receiver.recv()
                    self.assertEqual(label, 'uncommitted')
                    self.assertTrue(transaction)
                    self.assertEqual(count, 1)
                    self.assertEqual(self.rows(), baseline)
                finally:
                    child.kill()
                    child.join(timeout=8)
                self.assertEqual(child.exitcode, -signal.SIGKILL)
                with self.assertRaises((RemoteDisconnected, ConnectionResetError)):
                    result.result(timeout=12)
        finally:
            if child.is_alive():
                child.kill()
                child.join(timeout=8)
            receiver.close()
        self.assertEqual(self.rows(), baseline)
        reopened = DraftStore(self.store.state, [self.projects])
        fresh_runtime = runtime_for(reopened)
        with self.service(fresh_runtime) as endpoint, patch.object(fresh_runtime, 'spawn', side_effect=self.committed_fact) as spawn:
            status, body = request(endpoint, 'launch', self.payload())
            self.assertEqual(status, 200)
            self.assertNotEqual(body['run']['id'], run_id)
            spawn.assert_called_once_with(body['run']['id'])

    def test_committed_then_spawn_boundary_failure_reopens_without_replay(self):
        observed = []

        def fail_spawn(run_id):
            observed.append(self.committed_fact(run_id))
            raise OSError(PRIVATE_FAILURE)

        with self.service() as endpoint, patch.object(self.runtime, 'spawn', side_effect=fail_spawn) as spawn:
            self.assertEqual(request(endpoint, 'launch', self.payload()), (503, SERVICE_FAILURE))
            spawn.assert_called_once_with(observed[0]['run'])
        before = self.rows()
        reopened = DraftStore(self.store.state, [self.projects])
        fresh_runtime = runtime_for(reopened)
        fresh_runtime.provider = None
        with self.service(fresh_runtime) as endpoint, patch.object(fresh_runtime, 'spawn') as spawn:
            status, body = request(endpoint, 'launch', self.payload())
            self.assertEqual(status, 200)
            self.assertEqual(body['run']['id'], observed[0]['run'])
            self.assertEqual(body['events'], observed)
            spawn.assert_not_called()
        self.assertEqual(self.rows(), before)

    def test_legacy_reservations_with_or_without_legacy_event_remain_read_only(self):
        seeded = [
            ('1' * 32, self.seed_legacy('1' * 32, 'legacy-intent-with-raw-field', {'intent_key': 'old-private-field'})),
            ('2' * 32, self.seed_legacy('2' * 32, 'legacy-intent-without-raw-field', {})),
            ('3' * 32, self.seed_legacy('3' * 32, 'legacy-intent-without-event', None)),
        ]
        before = self.rows()
        reopened = DraftStore(self.store.state, [self.projects])
        fresh_runtime = runtime_for(reopened)
        fresh_runtime.provider = None
        with self.service(fresh_runtime) as endpoint, patch.object(fresh_runtime, 'spawn') as spawn:
            for run_id, payload in seeded:
                status, body = request(endpoint, 'launch', payload)
                self.assertEqual(status, 200)
                self.assertEqual(body['run']['id'], run_id)
                self.assertEqual(body['events'], reopened.db.events(run_id))
                self.assertFalse(any('event_version' in event for event in body['events']))
            first = request(endpoint, f"run?workspace={self.workspace}&id={seeded[0][0]}")[1]
            second = request(endpoint, f"run?workspace={self.workspace}&id={seeded[1][0]}")[1]
            third = request(endpoint, f"run?workspace={self.workspace}&id={seeded[2][0]}")[1]
            self.assertEqual(first['events'][0]['intent_key'], 'old-private-field')
            self.assertNotIn('intent_key', second['events'][0])
            self.assertEqual(third['events'], [])
            spawn.assert_not_called()
        self.assertEqual(self.rows(), before)
