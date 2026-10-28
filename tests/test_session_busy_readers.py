"""Real flock contention: occupancy readers coexist, writers remain exclusive."""
from contextlib import contextmanager
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from orchestration.common import digest
from orchestration.store import lease
from studio.session_center import SessionCenter


class BusyReaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / 'managed'
        self.home.mkdir()
        self.lock = self.home.parent / (digest(str(self.home.resolve()).encode()) + '.session.lock')
        self.center = SimpleNamespace(db=SimpleNamespace(listing=lambda *a, **kw: []))

    def busy(self):
        return SessionCenter._busy(self.center, str(self.home))

    def test_two_actual_read_probes_do_not_report_a_writer(self):
        entered = threading.Event()
        release = threading.Event()
        results = []

        @contextmanager
        def observed(path, **kwargs):
            with lease(path, **kwargs):
                if threading.current_thread().name == 'first-probe':
                    entered.set()
                    if not release.wait(3):
                        raise RuntimeError('test did not release reader')
                yield

        self.lock.write_text('original writer receipt')
        with patch('studio.session_center.lease', observed):
            first = threading.Thread(target=lambda: results.append(self.busy()), name='first-probe')
            first.start()
            try:
                self.assertTrue(entered.wait(3))
                self.assertFalse(self.busy(), 'another read probe is not an Agent writer')
            finally:
                release.set()
                first.join(3)
            self.assertFalse(first.is_alive())
        self.assertEqual(results, [False])
        self.assertEqual(self.lock.read_text(), 'original writer receipt')

    def test_actual_exclusive_writer_is_still_busy(self):
        with lease(self.lock):
            self.assertTrue(self.busy())
        self.assertFalse(self.busy())

    def test_unfinished_operation_stays_busy_without_lock_probe(self):
        for state in ('QUEUED', 'STARTING', 'RUNNING'):
            with self.subTest(state=state):
                self.center.db.listing = lambda *a, **kw: [dict(binding=dict(home=str(self.home)), state=state)]
                with patch('studio.session_center.lease', side_effect=AssertionError('must check operation first')):
                    self.assertTrue(self.busy())

    def test_terminal_operation_without_writer_is_not_busy(self):
        self.center.db.listing = lambda *a, **kw: [dict(binding=dict(home=str(self.home)), state='COMPLETED')]
        self.assertFalse(self.busy())
