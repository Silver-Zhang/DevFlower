"""The 1B0 identity index takes only explicit disposable fixture provenance."""
from dataclasses import FrozenInstanceError, replace
import hashlib
import hmac
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from orchestration.common import canonical
from v2 import session_identity as identity
from v2.session_identity import FixtureSession, LocatorIndex, ProfileRegistry, ReadProfile, SessionRefused, StorageRootIdentity

SID = '11111111-2222-4333-8444-555555555555'
OTHER = 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee'
SEED = b'\x87' * 32


def fixture_tree(base):
    anchor = base / 'sources'
    alpha, beta = anchor / 'alpha', anchor / 'beta'
    for root in (alpha, beta):
        root.mkdir(parents=True)
        (root / 'fixture.jsonl').write_text('synthetic bytes', encoding='utf-8')
    cwd, other = base / 'A', base / 'B'
    cwd.mkdir()
    other.mkdir()
    return anchor, alpha, beta, cwd, other


def registry_for(anchor, alpha, beta):
    root_a, root_b = StorageRootIdentity.capture(alpha), StorageRootIdentity.capture(beta)
    return ProfileRegistry(anchor, StorageRootIdentity.capture(anchor), [
        ReadProfile('alpha-alias', 'codex', str(alpha) + '/', root_a),
        ReadProfile('alpha', 'codex', alpha, root_a),
        ReadProfile('beta', 'codex', beta, root_b),
        ReadProfile('claude-alpha', 'claude', alpha, root_a),
    ], SEED)


def index_for(registry, cwd, other):
    return LocatorIndex(registry, {'A': [cwd], 'B': [cwd, other]}, [
        FixtureSession('alpha', cwd, 'A', SID, 'source-a', 'Primary'),
        FixtureSession('alpha-alias', cwd, 'A', SID, 'source-a', 'Alias bearer ghp_syntheticAAA1'),
        FixtureSession('alpha-alias', cwd, 'B', SID, 'source-a', 'Secondary'),
        FixtureSession('beta', other, 'B', SID, 'source-b'),
        FixtureSession('claude-alpha', cwd, 'A', SID, 'source-c'),
    ])


def snapshot(directory):
    return {
        path.relative_to(directory).as_posix(): (path.stat().st_mode, path.stat().st_mtime_ns,
                                                path.read_bytes() if path.is_file() else None)
        for path in (directory, *sorted(directory.rglob('*')))
    }


class SessionIdentityTests(TestCase):
    def setUp(self):
        temp = TemporaryDirectory(prefix='v2-session-identity-')
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.anchor, self.alpha, self.beta, self.cwd, self.other = fixture_tree(self.base)
        self.registry = registry_for(self.anchor, self.alpha, self.beta)

    def refused(self, code, action, *args):
        with self.assertRaises(SessionRefused) as caught:
            action(*args)
        self.assertEqual(caught.exception.code, code)
        self.assertNotIn(str(self.base), str(caught.exception))
        self.assertNotIn(SID, str(caught.exception))

    def test_alias_labels_deduplicate_and_physical_provider_roots_stay_separate(self):
        before = snapshot(self.anchor)
        index = index_for(self.registry, self.cwd, self.other)
        by_scope = {scope: {row.profile_id: (handle, row) for handle in index.handles(scope)
                            for row in [index.resolve(scope, handle)]} for scope in ('A', 'B')}
        self.assertEqual(set(by_scope['A']), {'alpha', 'claude-alpha'})
        self.assertEqual(set(by_scope['B']), {'alpha', 'beta'})
        handle, alpha = by_scope['A']['alpha']
        other_handle, same_alpha = by_scope['B']['alpha']
        _, beta = by_scope['B']['beta']
        _, claude = by_scope['A']['claude-alpha']
        self.assertNotEqual(handle, other_handle)
        self.assertEqual(alpha.lease_key, same_alpha.lease_key)
        self.assertEqual(len({alpha.lease_key, beta.lease_key, claude.lease_key}), 3)
        self.assertEqual(index.source_labels('A', handle), ('Alias bearer [REDACTED]', 'Primary'))
        self.refused('LOCATOR_REFUSED', index.resolve, 'A', by_scope['B']['beta'][0])
        self.refused('LOCATOR_REFUSED', index.resolve, 'B', handle)
        self.assertEqual(snapshot(self.anchor), before)

    def test_locator_has_six_immutable_fields_and_exact_key_algorithm(self):
        index = index_for(self.registry, self.cwd, self.other)
        locator = next(index.resolve('A', handle) for handle in index.handles('A')
                       if index.resolve('A', handle).provider == 'codex')
        expected_key = hashlib.sha256(canonical(['v2-native-lease-v1', locator.root_identity.fields(),
                                                  'codex', SID])).hexdigest()
        self.assertEqual(locator.lease_key, expected_key)
        expected = 'v1.' + hmac.new(SEED, locator.canonical_bytes(), hashlib.sha256).hexdigest()
        self.assertEqual(self.registry.handle(locator), expected)
        self.assertEqual(len(expected), 67)
        for modified in (replace(locator, provider='claude'), replace(locator, profile_id='different'),
                         replace(locator, root_identity=StorageRootIdentity.capture(self.beta)),
                         replace(locator, original_cwd=str(self.other)), replace(locator, workspace_id='B'),
                         replace(locator, native_uuid=OTHER)):
            self.assertNotEqual(self.registry.handle(modified), expected)
        with self.assertRaises(FrozenInstanceError):
            locator.native_uuid = OTHER
        self.assertNotIn(str(self.cwd), repr(locator))
        self.assertNotIn(SID, repr(locator))

    def test_collisions_and_unverified_membership_refuse_every_affected_scope(self):
        good = FixtureSession('alpha', self.cwd, 'A', SID, 'same-source')
        for other in (FixtureSession('alpha-alias', self.cwd, 'B', SID, 'different-source'),
                      FixtureSession('alpha', self.other, 'B', SID, 'same-source'),
                      FixtureSession('alpha', self.other, 'A', SID, 'same-source'),
                      FixtureSession('alpha', self.cwd, 'unknown-scope', SID, 'same-source'),
                      replace(good, verified=False)):
            with self.subTest(other=other):
                index = LocatorIndex(self.registry, {'A': [self.cwd], 'B': [self.cwd, self.other]}, [good, other])
                for scope in ('A', 'B'):
                    for handle in index.handles(scope):
                        self.refused('LOCATOR_REFUSED', index.resolve, scope, handle)

    def test_a_single_incomplete_source_does_not_resolve(self):
        partial = FixtureSession('alpha', self.cwd, 'A', SID, 'source', verified=False)
        index = LocatorIndex(self.registry, {'A': [self.cwd]}, [partial])
        handle, = index.handles('A')
        self.refused('LOCATOR_REFUSED', index.resolve, 'A', handle)
        self.refused('LOCATOR_REFUSED', index.source_labels, 'A', handle)

    def test_root_scope_refuses_symlinks_traversal_case_and_mount_aliases(self):
        link = self.anchor / 'link'
        link.symlink_to(self.alpha, target_is_directory=True)
        alpha = StorageRootIdentity.capture(self.alpha)
        for path in ('relative/path', self.anchor / '..' / 'sources' / 'alpha'):
            self.refused('PROFILE_REFUSED', ReadProfile, 'bad', 'codex', path, alpha)
        for path in (link, self.anchor / 'ALPHA', self.cwd):
            profile = ReadProfile('bad', 'codex', path, alpha)
            self.refused('PROFILE_REFUSED', ProfileRegistry, self.anchor, self.registry.anchor_identity, [profile], SEED)

        actual = identity.mount_id

        def mount_changed(fd):
            return actual(fd) + (os.readlink(f'/proc/self/fd/{fd}') == str(self.alpha))

        with patch.object(identity, 'mount_id', side_effect=mount_changed):
            alias = StorageRootIdentity.capture(self.alpha)
            self.assertEqual((alias.device, alias.inode), (alpha.device, alpha.inode))
            profile = ReadProfile('mounted', 'codex', self.alpha, alias)
            self.refused('PROFILE_REFUSED', ProfileRegistry, self.anchor, self.registry.anchor_identity, [profile], SEED)
        with patch.object(identity, 'mount_id', return_value=None):
            self.refused('PROFILE_REFUSED', StorageRootIdentity.capture, self.alpha)

    def test_root_replacement_after_registration_refuses_index_resolution(self):
        index = index_for(self.registry, self.cwd, self.other)
        alpha = next(handle for handle in index.handles('A') if index.resolve('A', handle).provider == 'codex')
        self.alpha.rename(self.anchor / 'original-alpha')
        self.alpha.mkdir()
        self.refused('ROOT_CHANGED', index.resolve, 'A', alpha)

    def test_old_run_claims_and_missing_sources_have_no_authority(self):
        legacy = self.base / 'v1.json'
        legacy.write_text('synthetic run, native-session-claim, and duplicate activation', encoding='utf-8')
        record = {'native-session-claim': SID, 'cwd': str(self.cwd), 'workspace': 'A'}
        before = snapshot(self.base)
        self.refused('LOCATOR_REFUSED', LocatorIndex, self.registry, {'A': [self.cwd]}, [record])
        index = LocatorIndex(self.registry, {'A': [self.cwd]}, [])
        self.assertEqual(index.handles('A'), ())
        self.refused('LOCATOR_REFUSED', index.resolve, 'A', SID)
        self.refused('LOCATOR_REFUSED', index.resolve, 'A', 'v1.' + 'a' * 64)
        self.assertEqual(snapshot(self.base), before)
