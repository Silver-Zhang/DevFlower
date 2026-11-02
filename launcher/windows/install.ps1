$ErrorActionPreference = 'Stop'
# DevFlower does not support Windows yet (#141). The starter pins its state
# directory through POSIX dir_fd/O_NOFOLLOW/O_DIRECTORY and verifies the live
# service through Linux /proc, so a shortcut here could only fail. Refuse
# instead of installing an entry point that looks supported.
throw 'DevFlower does not support Windows yet; nothing was installed. See README.md.'
