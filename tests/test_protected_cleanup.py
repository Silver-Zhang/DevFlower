"""Real filesystem guard mutations; no native model calls."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from orchestration.common import Blocked
import studio.native_codex_exec as guard


class ProtectedCleanupTests(unittest.TestCase):
    def scenario(self, setup, mutate, allowed):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder); root = base/'root'; root.mkdir()
            setup(base, root)
            real = os.fwalk; calls = 0
            def walk(*args, **kwargs):
                nonlocal calls
                calls += 1
                yield from real(*args, **kwargs)
                if calls == 1: mutate(base, root)
            with patch.object(guard.os, 'fwalk', walk):
                if allowed: guard.check_protected_hardlinks([str(root)])
                else:
                    with self.assertRaises(Blocked): guard.check_protected_hardlinks([str(root)])

    def test_real_cleanup_and_internal_rename(self):
        def nested(b, r):
            (r/'d'/'child').mkdir(parents=True); (r/'d'/'child'/'file').write_text('x')
        def link(b, r): (r/'link').symlink_to('target')
        for setup, mutate in ((lambda b,r:(r/'d').mkdir(), lambda b,r:(r/'d').rmdir()),
                              (nested, lambda b,r:shutil.rmtree(r/'d')),
                              (nested, lambda b,r:(r/'d').rename(r/'moved')),
                              (link, lambda b,r:(r/'link').unlink()),
                              (link, lambda b,r:(r/'link').rename(r/'moved'))):
            with self.subTest(mutate=mutate): self.scenario(setup, mutate, True)

    def test_incomplete_first_scan_cannot_reset_root_or_shared_anchor(self):
        for replace_root in (True, False):
            with self.subTest(root=replace_root), tempfile.TemporaryDirectory() as folder:
                base = Path(folder); root = base/'root'; root.mkdir()
                (root/'vanishing').write_text('ordinary state')
                (root/'a').write_text('protected'); os.link(root/'a', root/'b')
                real = os.fwalk; calls = 0
                def walk(*args, **kwargs):
                    nonlocal calls
                    calls += 1
                    for row in real(*args, **kwargs):
                        yield row
                        if calls == 1:
                            if replace_root:
                                root.rename(base/'outside'); root.mkdir()
                            else:
                                (root/'b').unlink(); os.link(root/'a', root/'b')
                            raise FileNotFoundError('incomplete first scan')
                before = len(os.listdir('/proc/self/fd'))
                with patch.object(guard.os, 'fwalk', walk), self.assertRaises(Blocked):
                    guard.check_protected_hardlinks([str(root)])
                self.assertEqual(calls, 1, 'Initial anchors must never be silently reset')
                self.assertEqual(len(os.listdir('/proc/self/fd')), before)

    def test_replacement_and_escape_are_refused(self):
        def directory(b, r): (r/'d').mkdir()
        def link(b, r): (r/'link').symlink_to('first')
        def replace_link(b, r): (r/'link').unlink(); (r/'link').symlink_to('other')
        def replace_root(b, r): r.rename(b/'old'); r.mkdir()
        for setup, mutate in ((directory, lambda b,r:(r/'d').rename(b/'outside')),
                              (directory, lambda b,r:(r/'d').chmod(0o711)),
                              (lambda b,r:None, replace_root), (link, replace_link),
                              (link, lambda b,r:(r/'link').rename(b/'outside'))):
            with self.subTest(mutate=mutate): self.scenario(setup, mutate, False)

    def test_shared_inode_mutations_are_refused(self):
        def setup(b, r):
            (r/'d').mkdir(); (r/'d'/'a').write_text('x'); os.link(r/'d'/'a',r/'d'/'b')
        def relink(b,r): (r/'d'/'b').unlink(); os.link(r/'d'/'a',r/'d'/'b')
        def move_shared(b,r): (r/'d').rename(r/'moved')
        def outside_link(b,r): os.link(r/'d'/'a',b/'outside')
        for mutate in (relink, move_shared, outside_link):
            with self.subTest(mutate=mutate): self.scenario(setup, mutate, False)

    def test_cross_root_move_and_old_name_replacement_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder); left = base/'left'; right = base/'right'
            left.mkdir(); right.mkdir(); (left/'d').mkdir()
            real = os.fwalk; calls = 0
            def walk(*args, **kwargs):
                nonlocal calls
                calls += 1
                yield from real(*args, **kwargs)
                if calls == 2: (left/'d').rename(right/'d')
            with patch.object(guard.os, 'fwalk', walk), self.assertRaises(Blocked):
                guard.check_protected_hardlinks([str(left), str(right)])
        def replace(b, r):
            (r/'d').rename(r/'moved'); (r/'d').write_text('replacement')
        self.scenario(lambda b,r:(r/'d').mkdir(), replace, False)

    def test_descriptor_budget_errors_and_success_do_not_leak(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root/'dir').mkdir(); (root/'link').symlink_to('target')
            before = len(os.listdir('/proc/self/fd'))
            guard.check_protected_hardlinks([str(root)])
            self.assertEqual(len(os.listdir('/proc/self/fd')), before)
            with patch.object(guard, '_PROTECTED_FD_LIMIT', 1), self.assertRaises(Blocked):
                guard.check_protected_hardlinks([str(root)])
            self.assertEqual(len(os.listdir('/proc/self/fd')), before)
            real = os.open
            def failed(path, flags, *args, **kwargs):
                if path == 'link': raise OSError('synthetic descriptor failure')
                return real(path, flags, *args, **kwargs)
            with patch.object(guard.os, 'open', failed), self.assertRaises(Blocked):
                guard.check_protected_hardlinks([str(root)])
            self.assertEqual(len(os.listdir('/proc/self/fd')), before)

    def test_replacement_during_anchor_acquisition_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); link = root/'link'; link.symlink_to('first')
            real = os.open; changed = False
            def replace(path, flags, *args, **kwargs):
                nonlocal changed
                if path == 'link' and not changed:
                    changed = True; link.unlink(); link.symlink_to('different')
                return real(path, flags, *args, **kwargs)
            with patch.object(guard.os, 'open', replace), self.assertRaises(Blocked):
                guard.check_protected_hardlinks([str(root)])
            self.assertTrue(changed)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); link = root/'link'; link.symlink_to('first')
            real = os.open; changed = False
            def remove(path, flags, *args, **kwargs):
                nonlocal changed
                if path == 'link' and not changed: changed = True; link.unlink()
                return real(path, flags, *args, **kwargs)
            with patch.object(guard.os, 'open', remove), self.assertRaises(Blocked):
                guard.check_protected_hardlinks([str(root)])
            self.assertTrue(changed)

    def test_symlink_returned_to_original_name_and_cross_root_shared_move_refused(self):
        def move_back(b, r):
            (r/'link').rename(r/'other'); (r/'other').rename(r/'link')
        self.scenario(lambda b,r:(r/'link').symlink_to('target'), move_back, False)
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder); left = base/'left'; right = base/'right'
            left.mkdir(); right.mkdir(); (left/'a').write_text('x'); os.link(left/'a',left/'b')
            real = os.fwalk; calls = 0
            def walk(*args, **kwargs):
                nonlocal calls
                calls += 1
                yield from real(*args, **kwargs)
                if calls == 2: (left/'b').rename(right/'b')
            with patch.object(guard.os, 'fwalk', walk), self.assertRaises(Blocked):
                guard.check_protected_hardlinks([str(left), str(right)])
