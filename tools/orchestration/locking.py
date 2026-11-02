"""Advisory file locks: POSIX fcntl.flock, Windows msvcrt.locking.

The Windows branch locks one byte at offset 0 of a regular file descriptor.
Directory descriptors are POSIX-only; callers that need Windows must lock a
file instead of a directory.
"""
import os


def lock(fd, *, exclusive=True, blocking=False):
    if os.name == 'nt':
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
        try:
            msvcrt.locking(fd, mode, 1)
        except OSError as exc:
            raise BlockingIOError(getattr(exc, 'errno', None), 'lock busy') from None
        return
    import fcntl
    flags = (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
    if not blocking:
        flags |= fcntl.LOCK_NB
    fcntl.flock(fd, flags)


def unlock(fd):
    if os.name == 'nt':
        import msvcrt
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        return
    import fcntl
    fcntl.flock(fd, fcntl.LOCK_UN)
