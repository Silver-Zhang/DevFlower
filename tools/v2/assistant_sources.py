"""Bounded source discovery through the workbench's project-file policy."""
from collections import deque
from contextlib import suppress
import unicodedata

from orchestration.common import Blocked
from studio.graph import Invalid
from studio.session_sources import Refused

from .project_files import ProjectFiles, checked_path, list_files, preview_file

MAX_DIRECTORIES = 16
MAX_PREVIEWS = 36
MAX_MATCHES = 16
MAX_LINE_BYTES = 480


def source_lines(text):
    if not text:
        return []
    return text.removesuffix('\n').split('\n')


def bounded_line(text):
    text = text.removesuffix('\r')
    raw = text.encode()
    if len(raw) <= MAX_LINE_BYTES:
        return text
    return raw[:MAX_LINE_BYTES].decode(errors='ignore') + '…'


def numbered_excerpt(file, start=1, limit=80):
    if type(start) is not int or not 1 <= start <= 16_000 or type(limit) is not int or not 1 <= limit <= 80:
        raise Invalid('请选择有效的源码起始行与行数。')
    lines = source_lines(file['text'])
    if start > max(len(lines), 1):
        raise Invalid('源码起始行超出文件范围。')
    end = min(len(lines), start + limit - 1)
    selected = lines[start - 1:end]
    text = '\n'.join(f'{index} | {bounded_line(line)}' for index, line in enumerate(selected, start))
    shortened = any(len(line.removesuffix('\r').encode()) > MAX_LINE_BYTES for line in selected)
    return {'path': file['path'], 'text': text, 'start_line': start, 'end_line': end,
            'total_lines': len(lines), 'truncated': shortened or start > 1 or end < len(lines)}


def search_sources(store, workspace, query, directory=None):
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 120 or any(
            unicodedata.category(char) in ('Cc', 'Cf') for char in query):
        raise Invalid('请输入单行文件名或源码符号。')
    relative = checked_path('' if directory is None else directory)
    project = ProjectFiles(store, workspace)
    project.check_scope(relative)
    needle = query.strip().casefold()
    remaining = deque([relative])
    entries = []
    truncated = False
    try:
        with project.open_view() as view:
            view.check_rules(relative)
            for _ in range(MAX_DIRECTORIES):
                if not remaining:
                    break
                current = remaining.popleft()
                view.rules.remember(current)
                children = list_files(project, view, current)
                truncated |= children['truncated']
                for entry in children['entries']:
                    path = checked_path(entry['path'])
                    if entry['directory']:
                        remaining.append(path)
                    else:
                        entries.append(path)
            truncated |= bool(remaining)
            names = [path for path in entries if needle in path.name.casefold()]
            matches = [{'path': path.as_posix(), 'kind': 'file'} for path in names[:MAX_MATCHES]]
            truncated |= len(names) > MAX_MATCHES or len(entries) > MAX_PREVIEWS
            for path in entries[:MAX_PREVIEWS]:
                if len(matches) >= MAX_MATCHES:
                    truncated = True
                    break
                # Recheck rules before content access. A vanished, unsupported
                # or unreadable entry is never opened through another path.
                view.check_rules(path)
                with suppress(Invalid, Refused, FileNotFoundError, NotADirectoryError, PermissionError):
                    file = preview_file(view, path)
                    for line, text in enumerate(source_lines(file['text']), 1):
                        if needle in text.casefold():
                            matches.append({'path': path.as_posix(), 'kind': 'symbol',
                                            'line': line, 'text': bounded_line(text)})
                            break
            view.rules.validate()
    except (FileNotFoundError, NotADirectoryError, Refused):
        raise Invalid('所选源码目录不存在或不属于可读取的项目目录。') from None
    except OSError:
        raise Blocked('无法安全查找项目源码，请刷新后重试。') from None
    return {'workspace': workspace, 'directory': relative.as_posix() if relative.parts else '',
            'matches': matches, 'truncated': truncated}
