"""Advisory file locks: POSIX ``fcntl.flock`` only.

DevFlower has no Windows lock path (#141): the starter pins its state
directory through POSIX directory descriptors (``dir_fd``, ``O_DIRECTORY``,
``O_NOFOLLOW``) and Linux ``/proc`` verification. Shipping an ``msvcrt``
branch that ``platform_mode()`` never reaches would suggest Windows support
that does not exist, so Windows is refused up front instead.
"""


def lock(fd, *, exclusive=True, blocking=False):
    import fcntl
    flags = (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
    if not blocking:
        flags |= fcntl.LOCK_NB
    fcntl.flock(fd, flags)


def unlock(fd):
    import fcntl
    fcntl.flock(fd, fcntl.LOCK_UN)
