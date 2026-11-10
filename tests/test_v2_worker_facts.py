"""Actual HTTP, SQLite, pipe and lease with owned synthetic worker processes."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, closing, contextmanager
import hashlib
from http.client import RemoteDisconnected
import json
import multiprocessing
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Event, Thread
import time
from unittest import TestCase
from unittest.mock import patch

from orchestration.common import Blocked
from orchestration.store import lease
from test_v2_launch_event import SERVICE_FAILURE, compact, request, runtime_for
from v2 import runtime as runtime_module, worker_facts
from v2.server import create_server
from v2.store import DraftStore

FIXTURE = Path(__file__).resolve()
GO = b'GO\n'
INCONSISTENT = {'error': '工作进程启动证据不一致，未继续进入执行'}
PRIVATE = 'SYNTHETIC_PRIVATE_WORKER_FAILURE'
FIELDS = ('host', 'boot_id', 'pid', 'start', 'pgid', 'sid')
INTERNAL = {'worker_lifecycle_version', 'worker_authorized_seq', 'worker_bound_seq',
            'worker_entered_seq', 'worker_entered_at'}


def event_id(kind, run):
    return hashlib.sha256(f'devflower.v2.{kind}.v1\0{run}'.encode('ascii')).hexdigest()


def mark(root, run, tag):
    (root / f'{run}.{tag}').touch()


def isolated_worker(mode):
    """Use the product main and its real GO/lease gate, then stop before Provider."""
    from v2 import worker

    args = sys.argv[1:]
    root = Path(args[args.index('--state-dir') + 1]).parent
    run = args[args.index('--run') + 1]
    open_store = worker.DraftStore
    save = worker_facts._save_run
    enter = worker_facts.enter_worker

    def observed_open(*args, **kwargs):
        mark(root, run, 'store')
        return open_store(*args, **kwargs)

    def pause(tag):
        mark(root, run, tag)
        if not Event().wait(25):
            raise AssertionError('fixture worker pause was not interrupted')

    def save_run(db, row):
        save(db, row)
        if mode == 'before-enter-commit':
            pause('before-enter-commit')

    def entered(*args):
        enter(*args)
        if mode == 'after-enter-commit':
            pause('after-enter-commit')

    class SyntheticRuntime:
        def __init__(self, store, *args, **kwargs):
            # A distinct SQLite connection sees Entry already committed; a
            # separate fd cannot acquire the exclusive worker lease.
            row = store.db.get('run', run)
            if row.get('worker_lifecycle_version') == 1:
                entered = [event for event in store.db.events(run) if event['kind'] == 'worker-entered']
                assert len(entered) == 1 and entered[0]['seq'] == row['worker_entered_seq']
            try:
                with lease(store.state / (run + '.worker.lock')):
                    raise AssertionError('worker constructor ran without the lease')
            except Blocked:
                pass
            mark(root, run, 'constructor')
            self.native_turn = None

    class SyntheticScheduler:
        def __init__(self, *args, **kwargs):
            mark(root, run, 'scheduler')

        def run_graph(self):
            mark(root, run, 'dispatch')

    try:
        with patch.object(worker, 'DraftStore', observed_open), \
                patch.object(worker, 'V2Runtime', SyntheticRuntime), \
                patch.object(worker, 'Scheduler', SyntheticScheduler), \
                patch.object(worker_facts, '_save_run', save_run), \
                patch.object(worker_facts, 'enter_worker', entered):
            return worker.main()
    finally:
        mark(root, run, 'exited')


def replace_worker(popen, argv, mode, kwargs):
    # Only replace the child script, leaving the actual parent pipe and Popen
    # arguments in force. The child script invokes the production worker entry.
    assert Path(argv[2]).name == 'worker.py'
    args = [*argv[:2], str(FIXTURE), *argv[3:]]
    return popen(args, **kwargs, env=dict(os.environ, DEVFLOWER_WORKER_FIXTURE_MODE=mode))


def stopped_parent(state, projects, sender):
    store = DraftStore(state, [projects])
    runtime = runtime_for(store)
    server, token = create_server(store, runtime=runtime)
    sender.send(('ready', (server.server_port, token)))
    popen = subprocess.Popen
    write = os.write

    def spawn(argv, **kwargs):
        child = replace_worker(popen, argv, 'normal', kwargs)
        sender.send(('child', child.pid))
        return child

    def before_go(fd, data):
        if data == GO:
            sender.send(('bound', store.db.list('run')[0]['id']))
            if not Event().wait(25):
                raise OSError('fixture parent was not interrupted')
        return write(fd, data)

    with patch.object(runtime_module.subprocess, 'Popen', side_effect=spawn), \
            patch.object(runtime_module.os, 'write', side_effect=before_go):
        server.serve_forever(poll_interval=.03)


class WorkerFactsTests(TestCase):
    def setUp(self):
        temp = TemporaryDirectory(prefix='devflower-v2-worker-facts-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.projects = self.root / 'projects'
        (self.projects / 'A').mkdir(parents=True)
        (self.projects / 'B').mkdir()
        self.store = DraftStore(self.root / 'state', [self.projects])
        self.database = self.store.state / 'v2.sqlite'
        self.workspace = self.store.open_workspace(str(self.projects / 'A'))['id']
        graph = dict(version=1, name='合成 worker', nodes=[dict(
            id='AgentA', name='Synthetic', provider='codex', model='fixture', effort='low',
            position=dict(x=0, y=0), round_prompt='SYNTHETIC_PRIVATE_PROMPT')], edges=[])
        self.draft = self.store.save(self.workspace, None, None, graph)
        self.runtime = runtime_for(self.store)
        self.children = []
        self.go = []
        self.sequence = 0
        self.addCleanup(self.reap_children)

    def reap_children(self):
        for child in self.children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)

    def payload(self):
        self.sequence += 1
        return dict(workspace=self.workspace, draft_id=self.draft['id'], revision=self.draft['revision'],
                    task='SYNTHETIC_PRIVATE_TASK', intent_key=f'SYNTHETIC_PRIVATE_INTENT_{self.sequence}')

    @contextmanager
    def service(self, runtime=None):
        runtime = self.runtime if runtime is None else runtime
        server, token = create_server(runtime.store, runtime=runtime)
        thread = Thread(target=server.serve_forever, kwargs={'poll_interval': .03}, daemon=True)
        thread.start()
        try:
            yield server.server_port, token
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)
            self.assertFalse(thread.is_alive())

    @contextmanager
    def process_fixture(self, *, mode='normal', on_go=None, before_spawn=None):
        popen = subprocess.Popen
        write = os.write

        def spawn(argv, **kwargs):
            if before_spawn is not None:
                before_spawn()
            child = replace_worker(popen, argv, mode, kwargs)
            self.children.append(child)
            return child

        def observe_go(fd, data):
            if data == GO:
                self.go.append(data)
                if on_go is not None:
                    return on_go(write, fd, data)
            return write(fd, data)

        with patch.object(runtime_module.subprocess, 'Popen', side_effect=spawn), \
                patch.object(runtime_module.os, 'write', side_effect=observe_go):
            yield

    def latest(self):
        return self.store.db.list('run')[0]['id']

    def committed(self, run):
        # Open a separate connection, including when called inside a writer hook.
        with closing(sqlite3.connect(self.database.as_uri() + '?mode=ro', uri=True, timeout=1)) as db:
            db.execute('BEGIN')
            raw = db.execute("SELECT data FROM records WHERE kind='run' AND id=?", (run,)).fetchone()
            events = db.execute('SELECT seq,run,data FROM events WHERE run=? ORDER BY seq', (run,)).fetchall()
        self.assertIsNotNone(raw)
        return json.loads(raw[0]), [(seq, outer, json.loads(data)) for seq, outer, data in events]

    def kinds(self, events):
        return [event['kind'] for _, _, event in events]

    def assert_fact(self, run, events, kind, cause, payload, at):
        seq, outer, event = next(entry for entry in events if entry[2]['kind'] == kind)
        key = run['id']
        self.assertEqual(outer, key)
        self.assertEqual(event, dict(kind=kind, event_version=1, event_id=event_id(kind, key), run=key,
                                    definition_id=run['definition_id'], aggregate_kind='run', aggregate_id=key,
                                    cause=cause, payload=payload, at=at))
        return seq

    def assert_bound(self, row, events):
        at = row['worker_published_at']
        run = row['id']
        seq = self.assert_fact(row, events, 'worker-bound',
                               dict(kind='run-authorized', event_id=event_id('run-authorized', run)),
                               dict(launch_state='STARTED', worker_published_at=at), at)
        self.assertEqual(row['worker_bound_seq'], seq)
        self.assertEqual(row['launch_state'], 'STARTED')
        self.assertTrue(set(FIELDS) <= set(row['worker']))

    def assert_entered(self, row, events):
        at = row['worker_entered_at']
        seq = self.assert_fact(row, events, 'worker-entered',
                               dict(kind='worker-bound', event_id=event_id('worker-bound', row['id'])),
                               dict(worker_entered_at=at), at)
        self.assertEqual(row['worker_entered_seq'], seq)

    def assert_failed(self, row, events, stage):
        state = 'FAILED' if stage == 'before_spawn' else 'RECOVERY_REQUIRED'
        upstream = 'worker-bound' if stage == 'go' else 'run-authorized'
        at = row['finished_at']
        self.assertEqual(row['state'], state)
        self.assertEqual(row['reason'], '工作进程启动未确认，未自动重试')
        self.assert_fact(row, events, 'local-worker-launch-failed',
                         dict(kind=upstream, event_id=event_id(upstream, row['id'])),
                         dict(stage=stage, has_child=stage != 'before_spawn', state=state,
                              reason_code='WORKER_LAUNCH_UNCONFIRMED', finished_at=at), at)

    def assert_quiet(self, run):
        self.assertFalse((self.root / f'{run}.constructor').exists())
        self.assertFalse((self.root / f'{run}.dispatch').exists())
        self.assertEqual(self.store.db.list('activation'), [])
        self.assertEqual(self.store.db.list('native-step'), [])

    def wait_mark(self, run, tag, timeout=8):
        end = time.monotonic() + timeout
        path = self.root / f'{run}.{tag}'
        while not path.exists() and time.monotonic() < end:
            time.sleep(.01)
        self.assertTrue(path.exists(), f'fixture marker {tag} did not arrive')

    def update(self, run, **changes):
        with self.store.db.tx() as db:
            row = json.loads(db.execute("SELECT data FROM records WHERE kind='run' AND id=?", (run,)).fetchone()[0])
            row.update(changes)
            db.execute("UPDATE records SET data=? WHERE kind='run' AND id=?", (compact(row).decode(), run))

    def test_actual_http_bound_before_go_entered_before_constructor_and_private_fields(self):
        before_go = []

        def observe(write, fd, data):
            before_go.append(self.committed(self.latest()))
            return write(fd, data)

        payload = self.payload()
        with self.service() as endpoint, self.process_fixture(on_go=observe):
            status, response = request(endpoint, 'launch', payload)
            self.assertEqual(status, 200)
            run = response['run']['id']
            self.assertEqual(self.children[0].wait(timeout=8), 0)
            self.wait_mark(run, 'dispatch')
            detail = request(endpoint, f'run?workspace={self.workspace}&id={run}')[1]
            other = self.store.open_workspace(str(self.projects / 'B'))['id']
            self.assertEqual(request(endpoint, f'run?workspace={other}&id={run}')[0], 409)
            before = self.committed(run)
            self.assertEqual(request(endpoint, 'launch', payload)[1]['run']['id'], run)
            self.assertEqual(self.committed(run), before)
        self.assertEqual(self.go, [GO])
        self.assertEqual(len(self.children), 1)
        [(published, facts)] = before_go
        self.assertEqual(self.kinds(facts), ['run-authorized', 'worker-bound'])
        self.assertEqual(published['worker_lifecycle_version'], 1)
        self.assertEqual(published['worker_authorized_seq'], facts[0][0])
        self.assert_bound(published, facts)
        self.assertNotIn('worker_entered_seq', published)
        row, events = before
        self.assertEqual(self.kinds(events), ['run-authorized', 'worker-bound', 'worker-entered'])
        self.assert_entered(row, events)
        self.assertEqual(row['state'], 'QUEUED')  # synthetic scheduler never changes it
        self.assertEqual(row['actual_calls'], 0)
        for public in (response['run'], detail['run']):
            self.assertFalse(INTERNAL & public.keys())
            self.assertEqual(public['worker'], row['worker'])
        self.assertEqual(detail['events'], [dict(event, seq=seq) for seq, _, event in events])
        for event in detail['events']:
            data = compact(event).decode()
            for private in ('SYNTHETIC_PRIVATE_', '/synthetic/', 'boot_id', 'pid', 'pgid', 'sid'):
                self.assertNotIn(private, data)

    def test_popen_failure_and_identity_unavailable_are_distinct_facts(self):
        def fail_popen():
            raise OSError(PRIVATE)

        with self.service() as endpoint, self.process_fixture(before_spawn=fail_popen):
            self.assertEqual(request(endpoint, 'launch', self.payload()), (503, SERVICE_FAILURE))
        row, events = self.committed(self.latest())
        self.assertEqual(self.children, [])
        self.assertEqual(self.kinds(events), ['run-authorized', 'local-worker-launch-failed'])
        self.assert_failed(row, events, 'before_spawn')

        with self.service() as endpoint, self.process_fixture(), \
                patch.object(runtime_module, 'process_identity', side_effect=[None, None]):
            self.assertEqual(request(endpoint, 'launch', self.payload()), (409, INCONSISTENT))
        row, events = self.committed(self.latest())
        self.assertEqual(self.kinds(events), ['run-authorized', 'local-worker-launch-failed'])
        self.assert_failed(row, events, 'identity')
        self.assertEqual(self.children[0].returncode, -signal.SIGKILL)
        self.assertEqual(self.go, [])
        self.assert_quiet(row['id'])

    def test_bound_insertion_and_projection_failures_roll_back_without_go(self):
        insert = worker_facts._insert_event
        save = worker_facts._save_run

        for phase in ('before-insert', 'after-insert', 'before-commit'):
            with self.subTest(phase=phase):
                inspected = []

                def verify(db, run):
                    pending = db.execute("SELECT count(*) FROM events WHERE run=?", (run,)).fetchone()[0]
                    updated = json.loads(db.execute("SELECT data FROM records WHERE kind='run' AND id=?",
                                                    (run,)).fetchone()[0])
                    row, committed = self.committed(run)
                    inspected.append((db.in_transaction, pending, 'worker_bound_seq' in updated,
                                      self.kinds(committed), 'worker' in row))
                    raise sqlite3.OperationalError(PRIVATE)

                def inject_insert(db, event):
                    if event['kind'] == 'worker-bound' and phase != 'before-commit':
                        if phase == 'after-insert':
                            insert(db, event)
                        verify(db, event['run'])
                    return insert(db, event)

                def inject_save(db, row):
                    save(db, row)
                    if phase == 'before-commit' and 'worker_bound_seq' in row:
                        verify(db, row['id'])

                with self.service() as endpoint, self.process_fixture(), \
                        patch.object(worker_facts, '_insert_event', side_effect=inject_insert), \
                        patch.object(worker_facts, '_save_run', side_effect=inject_save):
                    self.assertEqual(request(endpoint, 'launch', self.payload()), (503, SERVICE_FAILURE))
                self.assertEqual(inspected, [(True, 1 if phase == 'before-insert' else 2,
                                              phase == 'before-commit', ['run-authorized'], False)])
                row, events = self.committed(self.latest())
                self.assertEqual(self.kinds(events), ['run-authorized', 'local-worker-launch-failed'])
                self.assert_failed(row, events, 'publication')
                self.assertNotIn('worker', row)
                self.assertEqual(self.go, [])
                self.assertIsNotNone(self.children[-1].returncode)
                self.assert_quiet(row['id'])

    def test_go_error_preserves_bound_and_concurrent_cancel_without_signaling_unrelated(self):
        unrelated = subprocess.Popen([sys.executable, '-B', '-c', 'import time; time.sleep(20)'],
                                     start_new_session=True)
        self.children.append(unrelated)
        canceled = []

        def fail_go(write, fd, data):
            run = self.latest()
            self.assert_bound(*self.committed(run))
            canceled.append(request(endpoint, 'cancel', dict(workspace=self.workspace, run_id=run)))
            raise BrokenPipeError(PRIVATE)

        with self.service() as endpoint, self.process_fixture(on_go=fail_go):
            self.assertEqual(request(endpoint, 'launch', self.payload()), (503, SERVICE_FAILURE))
        self.assertEqual(canceled[0][0], 200)
        row, events = self.committed(self.latest())
        self.assertTrue(row['cancel_requested'])
        self.assertEqual(self.kinds(events),
                         ['run-authorized', 'worker-bound', 'cancel-requested', 'local-worker-launch-failed'])
        self.assert_failed(row, events, 'go')
        self.assertEqual(self.go, [GO])
        self.assertIsNone(unrelated.poll())
        self.assertIsNotNone(self.children[-1].returncode)
        self.assert_quiet(row['id'])

    def test_failed_cleanup_or_failure_transaction_does_not_invent_fact(self):
        save = worker_facts._save_run

        def fail_record(db, row):
            save(db, row)
            if row['state'] == 'RECOVERY_REQUIRED':
                raise sqlite3.OperationalError(PRIVATE)

        for phase in ('cleanup', 'write'):
            with self.subTest(phase=phase):
                with self.service() as endpoint, self.process_fixture(), \
                        patch.object(worker_facts, 'publish_worker', side_effect=OSError(PRIVATE)), \
                        patch.object(worker_facts, '_save_run', side_effect=fail_record):
                    if phase == 'cleanup':
                        with patch('studio.spawn_ownership.reap_unpublished', side_effect=OSError(PRIVATE)):
                            self.assertEqual(request(endpoint, 'launch', self.payload()), (503, SERVICE_FAILURE))
                    else:
                        self.assertEqual(request(endpoint, 'launch', self.payload()), (503, SERVICE_FAILURE))
                row, events = self.committed(self.latest())
                self.assertEqual(self.kinds(events), ['run-authorized'])
                self.assertEqual(row['state'], 'QUEUED')
                self.assertEqual(row['launch_state'], 'SPAWNING')
                self.assertNotIn('finished_at', row)
                self.assertEqual(self.go, [])
                self.assert_quiet(row['id'])

    def test_hard_parent_death_after_bound_never_replays_after_http_restart(self):
        ctx = multiprocessing.get_context('fork')
        receiver, sender = ctx.Pipe(duplex=False)
        parent = ctx.Process(target=stopped_parent, args=(self.store.state, self.projects, sender))
        parent.start()
        sender.close()

        def receive(tag):
            self.assertTrue(receiver.poll(8))
            actual, value = receiver.recv()
            self.assertEqual(actual, tag)
            return value

        def stop():
            if parent.is_alive():
                parent.kill()
            parent.join(8)
            receiver.close()
            self.assertFalse(parent.is_alive())

        self.addCleanup(stop)
        endpoint = receive('ready')
        payload = self.payload()
        with ThreadPoolExecutor(max_workers=1) as pool:
            reply = pool.submit(request, endpoint, 'launch', payload)
            child_pid = receive('child')
            run = receive('bound')
            before = self.committed(run)
            self.assert_bound(*before)
            self.assertEqual(before[0]['worker']['pid'], child_pid)
            self.assertEqual(self.kinds(before[1]), ['run-authorized', 'worker-bound'])
            parent.kill()
            parent.join(8)
            self.assertEqual(parent.exitcode, -signal.SIGKILL)
            with self.assertRaises(RemoteDisconnected):
                reply.result(timeout=8)
        self.wait_mark(run, 'exited')
        self.assertFalse((self.root / f'{run}.store').exists())
        self.assert_quiet(run)
        restarted = runtime_for(DraftStore(self.store.state, [self.projects]))
        with self.service(restarted) as endpoint, patch.object(restarted, 'spawn') as spawn:
            self.assertEqual(request(endpoint, 'launch', payload)[1]['run']['id'], run)
            self.assertEqual(request(endpoint, 'launch', payload | {'task': 'different'})[0], 409)
            spawn.assert_not_called()
        self.assertEqual(self.committed(run), before)

    def test_real_child_pipe_rejects_eof_wrong_bytes_and_timeout_before_store(self):
        with self.service() as endpoint:
            for mode in ('eof', 'wrong', 'timeout'):
                with self.subTest(mode=mode):
                    def gate(write, fd, data):
                        if mode == 'wrong':
                            return write(fd, b'NO\n')
                        if mode == 'timeout':
                            held.callback(os.close, os.dup(fd))
                        return len(data)

                    with ExitStack() as held, self.process_fixture(on_go=gate):
                        self.assertEqual(request(endpoint, 'launch', self.payload())[0], 200)
                        self.assertEqual(self.children[-1].wait(timeout=15), 1)
                    run = self.latest()
                    self.assertEqual(self.kinds(self.committed(run)[1]), ['run-authorized', 'worker-bound'])
                    self.assertFalse((self.root / f'{run}.store').exists())
                    self.assert_quiet(run)

    def test_real_child_rejects_occupied_lease_and_each_identity_mismatch(self):
        with self.service() as endpoint:
            for field in ('lease', *FIELDS):
                with self.subTest(field=field):
                    def gate(write, fd, data):
                        run = self.latest()
                        if field == 'lease':
                            with lease(self.store.state / (run + '.worker.lock')):
                                result = write(fd, data)
                                self.assertNotEqual(self.children[-1].wait(timeout=8), 0)
                                return result
                        row, _ = self.committed(run)
                        saved = row['worker']
                        saved[field] = saved[field] + 1 if type(saved[field]) is int else (
                            '1' + saved[field] if field == 'start' else 'different')
                        self.update(run, worker=saved)
                        return write(fd, data)

                    with self.process_fixture(on_go=gate):
                        self.assertEqual(request(endpoint, 'launch', self.payload())[0], 200)
                    self.assertNotEqual(self.children[-1].wait(timeout=6), 0)
                    run = self.latest()
                    self.wait_mark(run, 'store')
                    self.assertEqual(self.kinds(self.committed(run)[1]), ['run-authorized', 'worker-bound'])
                    self.assert_quiet(run)

    def test_child_death_before_and_after_entered_commit_and_reentry_refusal(self):
        with self.service() as endpoint:
            for mode in ('before-enter-commit', 'after-enter-commit'):
                with self.subTest(mode=mode):
                    with self.process_fixture(mode=mode):
                        self.assertEqual(request(endpoint, 'launch', self.payload())[0], 200)
                    run = self.latest()
                    self.wait_mark(run, mode)
                    row, events = self.committed(run)
                    entered = mode == 'after-enter-commit'
                    expected = ['run-authorized', 'worker-bound'] + (['worker-entered'] if entered else [])
                    self.assertEqual(self.kinds(events), expected)
                    self.assertEqual('worker_entered_seq' in row, entered)
                    if entered:
                        self.assert_entered(row, events)
                        with self.assertRaises(Blocked):
                            worker_facts.enter_worker(self.store.db, run, row['worker'])
                    self.assert_quiet(run)
                    self.children[-1].kill()
                    self.assertEqual(self.children[-1].wait(timeout=6), -signal.SIGKILL)
                    self.assertEqual(self.committed(run), (row, events))

    def test_late_parent_failure_cannot_overwrite_entered_or_terminal_state(self):
        with self.service() as endpoint:
            for advance in ('entered', 'terminal'):
                with self.subTest(advance=advance):
                    def fail_late(write, fd, data):
                        run = self.latest()
                        if advance == 'entered':
                            write(fd, data)
                            self.wait_mark(run, 'after-enter-commit')
                        else:
                            self.update(run, state='CANCELLED', cancel_requested=True)
                        raise BrokenPipeError(PRIVATE)

                    with self.process_fixture(mode='after-enter-commit', on_go=fail_late):
                        self.assertEqual(request(endpoint, 'launch', self.payload()), (503, SERVICE_FAILURE))
                    row, events = self.committed(self.latest())
                    self.assertEqual(row['state'], 'QUEUED' if advance == 'entered' else 'CANCELLED')
                    self.assertEqual(row['cancel_requested'], advance == 'terminal')
                    self.assertEqual(self.kinds(events), ['run-authorized', 'worker-bound'] +
                                     (['worker-entered'] if advance == 'entered' else []))
                    self.assert_quiet(row['id'])

    def test_legacy_actual_worker_has_no_new_facts_and_invalid_version_refuses(self):
        with self.service() as endpoint:
            for legacy in ('untyped', 'dr001a', 'invalid'):
                with self.subTest(legacy=legacy):
                    payload = self.payload()
                    with patch.object(self.runtime, 'spawn'):
                        status, snap = request(endpoint, 'launch', payload)
                    self.assertEqual(status, 200)
                    run = snap['run']['id']
                    with self.store.db.tx() as db:
                        row = json.loads(db.execute("SELECT data FROM records WHERE kind='run' AND id=?",
                                                    (run,)).fetchone()[0])
                        for key in INTERNAL:
                            row.pop(key, None)
                        if legacy == 'invalid':
                            row['worker_lifecycle_version'] = True
                        if legacy == 'untyped':
                            db.execute("UPDATE events SET data=? WHERE run=?", ('{"kind":"legacy"}', run))
                        db.execute("UPDATE records SET data=? WHERE kind='run' AND id=?", (compact(row).decode(), run))
                    before = self.committed(run)[1]
                    if legacy == 'invalid':
                        with self.assertRaisesRegex(Blocked, INCONSISTENT['error']), self.process_fixture():
                            self.runtime.spawn(run)
                        self.assert_quiet(run)
                    else:
                        with self.process_fixture():
                            self.runtime.spawn(run)
                        self.assertEqual(self.children[-1].wait(timeout=8), 0)
                        self.wait_mark(run, 'dispatch')
                    after = self.committed(run)
                    self.assertEqual(after[1], before)
                    self.assertEqual(request(endpoint, 'launch', payload)[1]['run']['id'], run)
                    self.assertEqual(self.committed(run), after)
        self.assertEqual(len(self.children), 2)
        self.assertEqual(self.go, [GO, GO])

    def test_invalid_marker_bound_pointer_and_fact_bytes_refuse_actual_child(self):
        with self.service() as endpoint:
            for corruption in ('version', 'missing-marker', 'bound-pointer', 'bound-payload', 'bound-outer-run'):
                with self.subTest(corruption=corruption):
                    def gate(write, fd, data):
                        run = self.latest()
                        row, events = self.committed(run)
                        if corruption == 'version':
                            self.update(run, worker_lifecycle_version=2)
                        elif corruption == 'missing-marker':
                            with self.store.db.tx() as db:
                                del row['worker_lifecycle_version']
                                db.execute("UPDATE records SET data=? WHERE kind='run' AND id=?",
                                           (compact(row).decode(), run))
                        elif corruption == 'bound-pointer':
                            self.update(run, worker_bound_seq=row['worker_authorized_seq'])
                        else:
                            seq, _, fact = events[-1]
                            with self.store.db.tx() as db:
                                if corruption == 'bound-payload':
                                    fact['payload']['worker_published_at'] = 1
                                    db.execute('UPDATE events SET data=? WHERE seq=?', (compact(fact).decode(), seq))
                                else:
                                    db.execute('UPDATE events SET run=? WHERE seq=?', ('f' * 32, seq))
                        return write(fd, data)

                    with self.process_fixture(on_go=gate):
                        self.assertEqual(request(endpoint, 'launch', self.payload())[0], 200)
                    self.assertNotEqual(self.children[-1].wait(timeout=8), 0)
                    run = self.latest()
                    self.assertNotIn('worker_entered_seq', self.committed(run)[0])
                    self.assert_quiet(run)

    def test_wrong_authorized_scope_definition_and_intent_fail_closed(self):
        with self.service() as endpoint, patch.object(self.runtime, 'spawn'):
            status, response = request(endpoint, 'launch', self.payload())
        self.assertEqual(status, 200)
        unrelated, _ = self.committed(response['run']['id'])

        with self.service() as endpoint:
            for corruption in ('other-run-seq', 'definition', 'intent'):
                with self.subTest(corruption=corruption):
                    def mutate():
                        run = self.latest()
                        row, _ = self.committed(run)
                        if corruption == 'other-run-seq':
                            self.update(run, worker_authorized_seq=unrelated['worker_authorized_seq'])
                        elif corruption == 'definition':
                            self.update(run, graph_sha256='f' * 64)
                        else:
                            with self.store.db.tx() as db:
                                db.execute('UPDATE launch_intents SET payload_sha=? WHERE run_id=?', ('f' * 64, run))

                    with self.process_fixture(before_spawn=mutate):
                        self.assertEqual(request(endpoint, 'launch', self.payload()), (409, INCONSISTENT))
                    row, events = self.committed(self.latest())
                    self.assertEqual(self.kinds(events), ['run-authorized'])
                    self.assertNotIn('worker', row)
                    self.assertIsNotNone(self.children[-1].returncode)
                    self.assertEqual(self.go, [])
                    self.assert_quiet(row['id'])


if __name__ == '__main__':
    raise SystemExit(isolated_worker(os.environ['DEVFLOWER_WORKER_FIXTURE_MODE']))
