"""Mandatory host probe. Run in a disposable private Linux user/mount namespace."""
from contextlib import contextmanager
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

from v2.session_identity import FixtureSession, LocatorIndex, ProfileRegistry, ReadProfile, SessionRefused, StorageRootIdentity
from v2.session_lease import IDLE_VERIFIED, SessionLeaseAuthority


def mount_command(*arguments):
    try:
        result = subprocess.run(arguments, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        raise RuntimeError('fixture mount command unavailable') from None
    if result.returncode:
        raise RuntimeError('fixture mount command failed')


@contextmanager
def bind(source, target):
    mount_command('mount', '--bind', str(source), str(target))
    try:
        yield
    finally:
        mount_command('umount', str(target))


def must_refuse(code, action, *args):
    try:
        action(*args)
    except SessionRefused as error:
        if error.code == code:
            return
    raise RuntimeError('fixture was not refused with the expected code')


def probe():
    with TemporaryDirectory(prefix='devflower-v2-session-bind-') as scratch:
        base = Path(scratch)
        anchor = base / 'registered'
        original = anchor / 'native'
        original.mkdir(parents=True)
        alias = anchor / 'alias'
        alias.mkdir()
        authority = base / 'authority'
        authority.mkdir(mode=0o700)
        workspace = base / 'project'
        workspace.mkdir()
        seed = b'\x47' * 32
        anchor_id = StorageRootIdentity.capture(anchor)
        original_id = StorageRootIdentity.capture(original)
        profile = ReadProfile('native', 'codex', original, original_id)
        with bind(original, alias):
            alias_id = StorageRootIdentity.capture(alias)
            if ((alias_id.device, alias_id.inode) != (original_id.device, original_id.inode)
                    or alias_id.mount_id == original_id.mount_id):
                raise RuntimeError('fixture did not produce a same-device same-inode bind alias')
            refused = ReadProfile('alias', 'codex', alias, alias_id)
            must_refuse('PROFILE_REFUSED', ProfileRegistry, anchor, anchor_id, [profile, refused], seed)
            registry = ProfileRegistry(anchor, anchor_id, [profile], seed)
            index = LocatorIndex(registry, {'A': [workspace]}, [
                FixtureSession('native', workspace, 'A', '11111111-2222-4333-8444-555555555555', 'source')])
            handle, = index.handles('A')
            if index.resolve('A', handle).root_identity != original_id:
                raise RuntimeError('the original physical root did not resolve')
            leases = SessionLeaseAuthority.provision(authority, StorageRootIdentity.capture(authority), registry)
            with leases.acquire(index, 'A', handle, 'fixture-op', 'fixture-intent', lambda _: IDLE_VERIFIED) as lease:
                lease.check_before_fixture()
                lease.record_outcome(completed=True)
            print('PASS original bound root has a verified locator and one managed lease')
            print('PASS same-device and same-inode bind alias is rejected despite the distinct mount ID')

            # A separately mounted authority alias of a native descendant is
            # outside its registered path text but still physically overlaps.
            native_child = original / 'private'
            native_child.mkdir(mode=0o700)
            authority_alias = base / 'authority-alias'
            authority_alias.mkdir(mode=0o700)
            with bind(native_child, authority_alias):
                must_refuse('AUTHORITY_REFUSED', SessionLeaseAuthority.provision, authority_alias,
                            StorageRootIdentity.capture(authority_alias), registry)
            if list(native_child.iterdir()):
                raise RuntimeError('refused authority created native files')
            print('PASS a bind alias of a native descendant cannot hold the lease authority')


if __name__ == '__main__':
    try:
        probe()
    except Exception:
        print('FAIL mandatory session bind fixture did not complete', file=sys.stderr)
        raise SystemExit(1) from None
