"""Trusted in-namespace entry points for Claude Code and its Bash tool."""
import os
import re
import sys

ROOT = '/run/df-cli'
HOME = '/run/df-home'
TEMP = HOME + '/runtime/tmp'
RUNTIME = ('/usr', '/etc', '/dev/urandom', '/dev/random', '/proc/cpuinfo', '/proc/meminfo')


def main():
    # The only imported local module is the read-only snapshot installed by bwrap.
    sys.path.insert(0, ROOT)
    from sandbox import restrict

    mode, *args = sys.argv[1:]
    cwd = os.getcwd()
    os.closerange(3, os.sysconf('SC_OPEN_MAX'))
    if mode == 'bash':
        # Native Bash appends a pwd write for cwd tracking. Grant only that
        # marker in Claude's private scratch directory to the command process.
        command = args[-1] if args else ''
        markers = re.findall(re.escape(TEMP) + r'/claude-[a-f0-9]{4}-cwd\b', command)
        if markers:
            fd = os.open(markers[-1], os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            os.close(fd)
        restrict((*RUNTIME, '/dev/null'), (cwd, '/tmp', '/dev/null', *markers[-1:]), deny_network=True)
        env = dict(PATH='/usr/bin:/bin', HOME='/tmp', TMPDIR='/tmp', LANG='C.UTF-8',
                   GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null', GIT_TERMINAL_PROMPT='0')
        os.execve('/usr/bin/bash', ['/usr/bin/bash', *args], env)
    if mode in ('claude','claude-read'):
        history, executable, *argv = args
        read = (*RUNTIME, ROOT, cwd)
        write = ('/tmp', HOME + '/runtime', history, '/dev/null')
        restrict(read,write if mode=='claude-read' else (*write,cwd))
        # Bun needs the procfs mount for path lookup. Actual process files are
        # outside the Landlock allowlist, including this process's own env/fds.
        try:
            fd = os.open('/proc/self/environ', os.O_RDONLY)
        except PermissionError:
            os.execv(executable, [executable, *argv])
        os.close(fd)
        raise RuntimeError('Claude process environment is readable')
    raise RuntimeError('invalid Claude scope entry point')


if __name__ == '__main__':
    main()
