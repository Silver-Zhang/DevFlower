"""Pinned configuration for pure conversation or bounded client-tool threads.

The native store stays at its original path. User/project configuration is not
edited or copied into a new HOME. The app-server sees a pinned empty config and
an empty workspace, with only its own native store writable. Narrow readers
operate in the host controller; native tools never receive the host workspace.
"""
from contextlib import contextmanager, ExitStack
import os
from pathlib import Path
import stat
import tempfile

from orchestration.common import Blocked
from orchestration.sandbox import runtime_paths
from .session_sources import opened, clean_path

STORE_ALIAS='/run/devflower-native-store'


class SharedConversationConfig:
    def __init__(self, binding, cwd, root, empty, config_fd, storage_fds, auth_fd=None, read_scope=None, executable_image=None, executable_reader=None):
        self.binding, self.cwd, self.root, self.empty = binding, cwd, root, empty
        self.config_fd=config_fd
        self.auth_fd=auth_fd
        self.storage_fds=storage_fds
        self.read_scope=read_scope
        self.executable_image=executable_image
        self.executable_fd=executable_reader
        self.pass_fds=(config_fd,*storage_fds.values(),*((auth_fd,) if auth_fd is not None else ()),self.executable_fd)
        self.closed = False

    def wrap(self, argv):
        if self.closed:
            raise Blocked('共享会话配置已经关闭；未启动任务')
        b=self.binding;home=Path(b['home']);config=Path(b['config_root'])
        args=['/usr/bin/bwrap','--die-with-parent','--unshare-user','--unshare-pid',
              '--unshare-ipc','--unshare-uts','--cap-drop','ALL',
              '--proc','/proc','--dev','/dev','--tmpfs','/tmp']
        for value in runtime_paths():
            if value.startswith(('/proc/','/dev/')):continue
            if Path(value).exists():args+=['--ro-bind',value,value]
        from .native_codex_image import EXECUTABLE_ALIAS
        self.executable_image.validate()
        args+=['--dir','/run/devflower-cli','--perms','0500','--file',str(self.executable_fd),EXECUTABLE_ALIAS]
        # Copy only sealed bytes into the namespace-owned root, remounted read-only
        # below. current_exe() and self-helpers use this same private file.
        # An anonymous bind mount cannot be execve'd on this kernel.
        argv=[EXECUTABLE_ALIAS,*argv[1:]]
        # All mount destinations are namespace-owned. In particular, never put
        # an empty config submount on the writable host store: bwrap would create
        # that host file if it disappeared between validation and mounting.
        args+=['--dir',str(home),'--dir',self.cwd,'--bind-fd',str(self.config_fd),STORE_ALIAS,
               '--tmpfs',str(config),'--ro-bind',str(self.empty),str(config/'config.toml')]
        # These links preserve native history paths. SQLite uses the pinned
        # store alias directly, so its DB/WAL/SHM files stay in the original
        # directory and remain the same files used by Desktop and CLI.
        for name,fd in self.storage_fds.items():
            args+=['--bind-fd',str(fd),str(config/name)]
        for name in ('session_index.jsonl','models_cache.json'):
            path=config/name
            if path.is_symlink():raise Blocked('原生会话存储路径不能是符号链接')
            if self.read_scope is not None and name=='models_cache.json':
                # Do not let mutable user metadata change a previously probed
                # model's tool registry. The native store/cache is preserved.
                args+=['--ro-bind',str(self.empty),str(path)]
            else:args+=['--symlink',STORE_ALIAS+'/'+name,str(path)]
        # Credentials are mounted read-only, never copied or refreshed here.
        if self.auth_fd is not None:args+=['--ro-bind-fd',str(self.auth_fd),str(config/'auth.json')]
        return args+['--remount-ro','/','--chdir',self.cwd,'--',*argv]


@contextmanager
def shared_conversation_config(binding, cwd, artifacts, *, read_scope=None, executable_image=None):
    from .codex_narrow_read import NarrowReadScope
    narrow=type(read_scope) is NarrowReadScope and not read_scope.closed and read_scope.cwd==str(cwd)
    if read_scope is not None and not narrow:raise Blocked('窄读取授权对象与本次工作区不匹配')
    if (binding.get('provider')!='codex' or binding.get('execution_backend')!='native-local'
            or (not narrow and (binding.get('shared_conversation') is not True or binding.get('no_tools') is not True))
            or (narrow and (binding.get('shared_conversation') or binding.get('no_tools')))
            or binding.get('role')!='reviewer' or binding.get('execution_mode') not in ('Ask','Plan')
            or binding.get('skills') or binding.get('subagents','disabled')!='disabled'):
        raise Blocked('共享原生会话目前仅支持无工具、无扩展的 Ask/Plan 对话')
    home=Path(clean_path(binding['home']));config=Path(clean_path(binding['config_root']))
    cwd=clean_path(str(cwd))
    if config!=home/'.codex' or Path(cwd)==home or Path(cwd).is_relative_to(config) or config.is_relative_to(cwd):
        raise Blocked('共享会话的 HOME、配置和工作区不匹配')
    # Do not bypass an installation's managed configuration in this capsule.
    if Path('/etc/codex').exists():
        raise Blocked('此安装具有系统 Codex 配置；共享会话的策略继承尚未验证')
    with opened(config,directory=True) as fd:
        if os.fstat(fd).st_uid!=os.getuid():raise Blocked('原生会话根不属于当前用户')
        with opened(config/'config.toml') as config_file:
            if not stat.S_ISREG(os.fstat(config_file).st_mode):raise Blocked('原生配置文件不是普通文件')
        # A write operation may initialize these native storage directories, as
        # Codex itself does. Do this through the pinned root and reject links.
        with ExitStack() as stack, tempfile.TemporaryDirectory(prefix='shared-config-',dir=artifacts) as folder:
            storage_fds={}
            for name in ('sessions','archived_sessions','thread-writer-locks'):
                try:os.mkdir(name,0o700,dir_fd=fd)
                except FileExistsError:pass
                child=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=fd)
                stack.callback(os.close,child)
                if os.fstat(child).st_uid!=os.getuid():raise Blocked('原生会话子目录不属于当前用户')
                storage_fds[name]=child
            auth_fd=None
            if (config/'auth.json').exists() or (config/'auth.json').is_symlink():
                auth_fd=stack.enter_context(opened(config/'auth.json'))
                if not stat.S_ISREG(os.fstat(auth_fd).st_mode):raise Blocked('原生认证不是普通文件')
            root=Path(folder);empty=root/'config.toml';empty.write_text('');empty.chmod(0o400)
            from .native_codex_image import NativeImage
            image=executable_image
            if image is None:image=stack.enter_context(NativeImage(binding['executable'],expected_sha256=getattr(read_scope,'binary_sha256',None)))
            image.validate()
            if narrow and (not getattr(read_scope,'binary_sha256',None) or image.sha256!=read_scope.binary_sha256):
                raise Blocked('原生CLI未通过本轮窄读取兼容性核验')
            reader=os.open('/proc/self/fd/'+str(image.fd),os.O_RDONLY|os.O_CLOEXEC)
            stack.callback(os.close,reader)
            value=SharedConversationConfig(binding,cwd,root,empty,fd,storage_fds,auth_fd,read_scope,image,reader)
            try:yield value
            finally:value.closed=True
