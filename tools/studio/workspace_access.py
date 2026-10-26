"""Directory selection is local navigation, never execution authority."""
import os
from pathlib import Path
from .graph import Invalid
from .session_sources import clean_path, opened


def browse_directory(path):
    try:
        requested = clean_path(path)
        actual = str(Path(requested).resolve(strict=True))
        with opened(actual, directory=True): pass
        return actual
    except (OSError, ValueError, RuntimeError):
        raise Invalid('无法打开这个位置：请检查文件夹是否存在，以及当前用户是否有读取权限。') from None


def view_policy(rt, path):
    p = Path(path)
    private = [*rt.protected, rt.storage, rt.native]
    inside = any(p.is_relative_to(x) for x in private)
    contains = any(Path(x).is_relative_to(p) for x in private)
    system = path in ('/', str(Path.home())) or any(p.is_relative_to(x) for x in
        ('/proc','/sys','/dev','/etc','/usr','/bin','/sbin','/lib','/lib64','/boot','/root'))
    return dict(protected_container=inside or contains or system,
                hosted_records=inside or system,
                reason='此目录可以浏览；为保护系统或工作台数据，记录保存在独立本机记录区。' if inside or system else None)
