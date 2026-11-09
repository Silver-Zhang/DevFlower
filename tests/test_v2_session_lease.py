"""Cross-process and persistence tests for the fixture-only session lease."""
import json
import multiprocessing
import os
from pathlib import Path
import stat
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from test_v2_session_identity import SEED, SID, fixture_tree, index_for, registry_for, snapshot
from v2 import session_lease
from v2.session_identity import LocatorIndex, ProfileRegistry, SessionRefused, StorageRootIdentity
from v2.session_lease import BUSY, IDLE_VERIFIED, UNKNOWN, SessionLeaseAuthority


def idle(_):
    return IDLE_VERIFIED


def child_writer(base, authority_identity, scope, handle, ready, wait_for_start=None):
    base = Path(base)
    anchor = base / 'sources'
    registry = registry_for(anchor, anchor / 'alpha', anchor / 'beta')
    index = index_for(registry, base / 'A', base / 'B')
    authority = SessionLeaseAuthority(base / 'authority', authority_identity, registry)
    if wait_for_start is not None and not wait_for_start.wait(10):
        raise RuntimeError('fixture start did not arrive')
    try:
        with authority.acquire(index, scope, handle, 'child-op', 'child-' + scope, idle) as lease:
            ready.send(lease.receipt['phase'])
            if ready.poll(10) and ready.recv() == 'finish':
                lease.check_before_fixture()
                lease.record_outcome(completed=True)
    except SessionRefused as error:
        ready.send(error.code)
    finally:
        ready.close()


class SessionLeaseTests(TestCase):
    def setUp(self):
        temp = TemporaryDirectory(prefix='v2-session-lease-')
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.anchor, self.alpha, self.beta, self.cwd, self.other = fixture_tree(self.base)
        self.registry = registry_for(self.anchor, self.alpha, self.beta)
        self.index = index_for(self.registry, self.cwd, self.other)
        self.root = self.base / 'authority'
        self.root.mkdir(mode=0o700)
        self.identity = StorageRootIdentity.capture(self.root)
        self.authority = SessionLeaseAuthority.provision(self.root, self.identity, self.registry)
        self.a = self.handle('A', 'alpha')
        self.b = self.handle('B', 'alpha')
        self.key = self.index.resolve('A', self.a).lease_key
        self.book = self.root / (self.key + '.json')
        self.lock = self.root / (self.key + '.lock')

    def handle(self, scope, profile):
        return next(handle for handle in self.index.handles(scope)
                    if self.index.resolve(scope, handle).profile_id == profile)

    def acquire(self, intent='intent-one', observer=idle):
        return self.authority.acquire(self.index, 'A', self.a, 'operation', intent, observer)

    def refused(self, code, action, *args, **kwargs):
        with self.assertRaises(SessionRefused) as caught:
            action(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        self.assertNotIn(str(self.base), str(caught.exception))
        self.assertNotIn(SID, str(caught.exception))

    def rows(self):
        return json.loads(self.book.read_bytes())['receipts']

    def spawn_writer(self, scope, handle, wait_for_start=None):
        ctx = multiprocessing.get_context('spawn')
        local, remote = ctx.Pipe()
        child = ctx.Process(target=child_writer, args=(self.base, self.identity, scope, handle, remote, wait_for_start))
        child.start()
        remote.close()

        def cleanup():
            if child.is_alive():
                child.kill()
            child.join(timeout=10)
            local.close()

        self.addCleanup(cleanup)
        return child, local

    def message(self, connection):
        self.assertTrue(connection.poll(10), 'no fixture result from the child')
        return connection.recv()

    def test_closed_receipts_are_durable_bounded_and_idempotent(self):
        native_before = snapshot(self.anchor)
        with self.acquire() as lease:
            self.assertEqual(lease.receipt['phase'], 'PREPARED')
            self.refused('RECOVERY_REQUIRED', lease.record_outcome, completed=True)
            self.assertEqual(lease.check_before_fixture()['phase'], 'CHECKED')
            self.refused('RECOVERY_REQUIRED', lease.check_before_fixture)
            self.assertEqual(lease.record_outcome(completed=True)['phase'], 'CLOSED')
        old = self.rows()
        lock_inode = self.lock.stat().st_ino
        self.assertEqual(set(old[0]), session_lease.RECEIPT_FIELDS)
        self.assertEqual(set(old[0]['owner']), set(session_lease.OWNER_FIELDS))
        self.assertEqual(stat.S_IMODE(self.book.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.lock.stat().st_mode), 0o600)
        self.refused('INTENT_EXISTS', self.acquire)
        with self.acquire('new-intent') as second:
            second.check_before_fixture()
            second.record_outcome(completed=True)
        self.assertEqual(self.rows()[:1], old)
        self.assertEqual(self.lock.stat().st_ino, lock_inode)
        with patch.object(session_lease, 'MAX_INTENTS', 2):
            self.refused('RECOVERY_REQUIRED', self.acquire, 'overflow')
        raw = self.book.read_text()
        self.assertNotIn(SID, raw)
        self.assertNotIn(str(self.base), raw)
        self.assertEqual(snapshot(self.anchor), native_before)

    def test_external_initial_busy_and_unverifiable_create_no_lease(self):
        before = snapshot(self.root)
        for value, code in ((BUSY, 'EXTERNAL_BUSY'), (UNKNOWN, 'EXTERNAL_UNKNOWN'),
                            (None, 'EXTERNAL_UNKNOWN'), ('idle', 'EXTERNAL_UNKNOWN')):
            self.refused(code, self.acquire, observer=lambda _: value)
        with patch.object(session_lease, 'process_identity', return_value=None):
            self.refused('OWNER_UNKNOWN', self.acquire)
        with patch.object(session_lease.os, 'readlink', return_value=str(os.getpid() + 1)):
            self.refused('OWNER_UNKNOWN', self.acquire)
        self.assertEqual(snapshot(self.root), before)
        self.assertEqual(self.index.resolve('A', self.a).lease_key, self.key)

    def test_late_external_writer_persists_uncertainty_without_dispatch(self):
        before = snapshot(self.anchor)
        states = iter([IDLE_VERIFIED, BUSY])
        with self.acquire(observer=lambda _: next(states)) as lease:
            self.refused('EXTERNAL_BUSY', lease.check_before_fixture)
            self.assertEqual(lease.receipt['phase'], 'RECOVERY_REQUIRED')
            self.assertEqual(lease.receipt['external_state'], BUSY)
            self.refused('RECOVERY_REQUIRED', lease.record_outcome, completed=True)
        self.assertEqual(self.rows()[-1]['external_state'], BUSY)
        self.refused('RECOVERY_REQUIRED', self.acquire, 'another')
        self.assertEqual(snapshot(self.anchor), before)

    def test_external_exception_and_unclean_context_exit_never_complete(self):
        seen = []

        def observer(_):
            seen.append(True)
            if len(seen) > 1:
                raise OSError(str(self.base))
            return IDLE_VERIFIED

        with self.acquire(observer=observer) as lease:
            self.refused('EXTERNAL_UNKNOWN', lease.check_before_fixture)
        self.assertEqual(self.rows()[-1]['external_state'], UNKNOWN)
        self.assertEqual(self.rows()[-1]['phase'], 'RECOVERY_REQUIRED')
        other = self.handle('B', 'beta')
        with self.authority.acquire(self.index, 'B', other, 'op', 'intent', idle) as lease:
            lease.check_before_fixture()
        self.refused('RECOVERY_REQUIRED', self.authority.acquire, self.index, 'B', other, 'op', 'next', idle)

    def test_read_only_resolution_while_held_and_cross_process_alias_conflict(self):
        before_native = snapshot(self.anchor)
        with self.acquire() as holder:
            before_authority = snapshot(self.root)
            self.assertEqual(self.index.resolve('B', self.b).lease_key, holder.receipt['lease_key'])
            self.assertEqual(snapshot(self.root), before_authority)
            child, pipe = self.spawn_writer('B', self.b)
            self.assertEqual(self.message(pipe), 'LEASE_HELD')
            child.join(timeout=10)
            self.assertEqual(child.exitcode, 0)
            self.assertEqual(snapshot(self.root), before_authority)
            holder.check_before_fixture()
            holder.record_outcome(completed=True)
        self.assertEqual(snapshot(self.anchor), before_native)

    def test_simultaneous_first_acquisition_creates_exactly_one_winner(self):
        ctx = multiprocessing.get_context('spawn')
        start = ctx.Event()
        child_a, pipe_a = self.spawn_writer('A', self.a, start)
        child_b, pipe_b = self.spawn_writer('B', self.b, start)
        start.set()
        a, b = self.message(pipe_a), self.message(pipe_b)
        self.assertEqual({a, b}, {'PREPARED', 'LEASE_HELD'})
        (pipe_a if a == 'PREPARED' else pipe_b).send('finish')
        for child in (child_a, child_b):
            child.join(timeout=10)
            self.assertEqual(child.exitcode, 0)
        self.assertEqual(self.rows()[-1]['phase'], 'CLOSED')

    def test_same_uuid_in_distinct_root_or_provider_can_hold_distinct_locks(self):
        with self.acquire() as first:
            for scope, profile in (('B', 'beta'), ('A', 'claude-alpha')):
                other = self.handle(scope, profile)
                with self.authority.acquire(self.index, scope, other, 'op', 'intent', idle) as second:
                    self.assertNotEqual(first.receipt['lease_key'], second.receipt['lease_key'])
                    second.check_before_fixture()
                    second.record_outcome(completed=True)
            first.check_before_fixture()
            first.record_outcome(completed=True)

    def test_dead_holder_is_not_recovered_and_fork_cannot_operate_or_unlock(self):
        child, pipe = self.spawn_writer('A', self.a)
        self.assertEqual(self.message(pipe), 'PREPARED')
        child.kill()
        child.join(timeout=10)
        self.assertIsNotNone(child.exitcode)
        self.assertEqual(self.rows()[-1]['phase'], 'PREPARED')
        self.refused('RECOVERY_REQUIRED', self.acquire, 'after-death')

        other = self.handle('B', 'beta')
        with self.authority.acquire(self.index, 'B', other, 'op', 'fork-parent', idle) as lease:
            read, write = os.pipe()
            pid = os.fork()
            if pid == 0:
                os.close(read)
                try:
                    lease.check_before_fixture()
                except SessionRefused as error:
                    os.write(write, error.code.encode())
                finally:
                    lease.close()
                    os.close(write)
                    os._exit(0)
            os.close(write)
            try:
                self.assertEqual(os.read(read, 64), b'OWNER_UNKNOWN')
            finally:
                os.close(read)
                os.waitpid(pid, 0)
            self.refused('LEASE_HELD', self.authority.acquire, self.index, 'B', other, 'op', 'competing', idle)
            lease.check_before_fixture()
            lease.record_outcome(completed=True)

    def test_truncated_book_replacement_and_fsync_uncertainty_stop_reuse(self):
        with self.acquire() as lease:
            lease.check_before_fixture()
            fsync = os.fsync

            def fail_after_close_publication(fd):
                if stat.S_ISDIR(os.fstat(fd).st_mode) and self.rows()[-1]['phase'] == 'CLOSED':
                    raise OSError('synthetic directory fsync error')
                return fsync(fd)

            with patch.object(session_lease.os, 'fsync', side_effect=fail_after_close_publication):
                self.refused('RECOVERY_REQUIRED', lease.record_outcome, completed=True)
        marker = self.root / (self.key + '.pending')
        self.assertTrue(marker.is_file())
        self.assertEqual(self.rows()[-1]['phase'], 'CLOSED')
        self.refused('RECOVERY_REQUIRED', self.acquire, 'uncertain')
        original = b'{"receipts":['
        marker.unlink()  # Fixture tampering exposes the independent malformed-book check.
        self.book.write_bytes(original)
        self.refused('RECOVERY_REQUIRED', self.acquire, 'after-corrupt')
        self.assertEqual(self.book.read_bytes(), original)

    def test_lock_name_replacement_and_nonregular_lock_names_refuse(self):
        for make in (lambda: self.lock.symlink_to(self.alpha / 'fixture.jsonl'),
                     lambda: os.mkfifo(self.lock),
                     lambda: os.link(self.alpha / 'fixture.jsonl', self.lock)):
            make()
            try:
                self.refused('AUTHORITY_REFUSED', self.acquire)
            finally:
                self.lock.unlink()
        with self.acquire() as lease:
            self.lock.rename(self.root / 'moved-lock')
            self.lock.touch(mode=0o600)
            self.refused('AUTHORITY_REFUSED', lease.check_before_fixture)
            self.refused('RECOVERY_REQUIRED', self.acquire, 'different-lock')
        self.assertEqual(self.rows()[-1]['phase'], 'PREPARED')

    def test_authority_rejects_boot_namespace_permission_registry_and_overlap(self):
        manifest = self.root / 'authority.json'
        original = manifest.read_bytes()
        foreign_registry = ProfileRegistry(self.anchor, self.registry.anchor_identity,
                                           self.registry.profiles, bytes(32))
        self.assertNotEqual(foreign_registry.handle_seed, SEED)
        self.refused('AUTHORITY_REFUSED', SessionLeaseAuthority.provision, self.root, self.identity, foreign_registry)
        self.assertEqual(manifest.read_bytes(), original)
        self.refused('AUTHORITY_REFUSED', SessionLeaseAuthority, self.root, self.identity, foreign_registry)
        self.root.chmod(0o755)
        try:
            self.refused('AUTHORITY_REFUSED', SessionLeaseAuthority, self.root, self.identity, self.registry)
        finally:
            self.root.chmod(0o700)
        self.refused('AUTHORITY_REFUSED', SessionLeaseAuthority.provision,
                     self.alpha, StorageRootIdentity.capture(self.alpha), self.registry)
        owner = dict(session_lease.current_owner(), boot_id='other-boot')
        with patch.object(session_lease, 'current_owner', return_value=owner):
            self.refused('AUTHORITY_REFUSED', self.acquire)
        original_stat = os.stat

        def namespace_change(path, *args, **kwargs):
            observed = original_stat(path, *args, **kwargs)
            if path == '/proc/self/ns/mnt':
                return SimpleNamespace(st_dev=observed.st_dev, st_ino=observed.st_ino + 1)
            return observed

        with patch.object(session_lease.os, 'stat', side_effect=namespace_change):
            self.refused('AUTHORITY_REFUSED', self.acquire)
        table = session_lease.mount_table()
        root, target, _ = table[self.identity.mount_id]
        table[self.identity.mount_id] = root, target, 'nfs'
        with patch.object(session_lease, 'mount_table', return_value=table):
            self.refused('AUTHORITY_REFUSED', self.acquire)
        self.assertEqual(manifest.read_bytes(), original)

    def test_changed_root_and_unknown_owner_before_fixture_preserve_prepared(self):
        with self.acquire() as lease:
            with patch.object(session_lease, 'process_identity', return_value=None):
                self.refused('OWNER_UNKNOWN', lease.check_before_fixture)
            self.alpha.rename(self.anchor / 'old-alpha')
            self.alpha.mkdir()
            self.refused('ROOT_CHANGED', lease.check_before_fixture)
        self.assertEqual(self.rows()[-1]['phase'], 'PREPARED')

    def test_failed_post_publication_root_check_does_not_authorize_outcome(self):
        with self.acquire() as lease:
            publish = self.authority._publish
            old = self.anchor / 'moved'
            def replace_root_after_checked(*args):
                publish(*args)
                self.alpha.rename(old)
                self.alpha.mkdir()

            with patch.object(self.authority, '_publish', side_effect=replace_root_after_checked):
                self.refused('ROOT_CHANGED', lease.check_before_fixture)
            self.alpha.rmdir()
            old.rename(self.alpha)
            self.assertEqual(self.rows()[-1]['phase'], 'CHECKED')
            self.refused('RECOVERY_REQUIRED', lease.record_outcome, completed=True)
        self.refused('RECOVERY_REQUIRED', self.acquire, 'after-check-failure')

    def test_only_scoped_fixture_index_can_acquire_not_legacy_claims(self):
        empty = LocatorIndex(self.registry, {'A': [self.cwd]}, [])
        before = snapshot(self.root)
        self.refused('LOCATOR_REFUSED', self.authority.acquire, empty, 'A', self.a, 'op', 'legacy', idle)
        self.refused('LOCATOR_REFUSED', self.authority.acquire, self.index, 'B', self.a, 'op', 'legacy', idle)
        self.refused('LOCATOR_REFUSED', self.authority.acquire, self.index, 'A', SID, 'op', 'legacy', idle)
        self.assertEqual(snapshot(self.root), before)
