"""P1 contract and candidate validation embedded verbatim in native exports."""
from contextlib import ExitStack
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
import unicodedata

OUTPUT_BYTES = 80_000
CANDIDATE_FILES = 8
CANDIDATE_FILE_BYTES = 1_000_000
CANDIDATE_TOTAL_BYTES = CANDIDATE_FILES * CANDIDATE_FILE_BYTES
REVIEW_SEVERITIES = ('critical', 'major', 'minor')
OUTPUT_TYPES = ('any', 'string', 'number', 'boolean', 'object', 'array', 'null')
ROUTER_TYPES = ('string', 'number', 'boolean')
JSON_TYPES = {str: 'string', int: 'number', float: 'number', bool: 'boolean',
              dict: 'object', list: 'array', type(None): 'null'}
MISSING_OUTPUT_FIELD = object()
SENSITIVE_COMPONENT = re.compile(
    r'(secret|token|password|api.?key|receipt|credential)|\.(pem|key|p12|pfx)$|'
    r'(^|[._-])id_(rsa|dsa|ecdsa|ed25519)($|[._-])|(^|[._-])auth($|[._-])', re.IGNORECASE)


class OutputContractError(ValueError):
    pass


def output_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def strict_output(raw):
    def unique(pairs):
        obj = dict(pairs)
        if len(obj) != len(pairs):
            raise ValueError('duplicate key')
        return obj

    try:
        if len(raw.encode('utf-8')) > OUTPUT_BYTES:
            raise OutputContractError('Structured answer exceeds 80000 UTF-8 bytes.')
        value = json.loads(raw, object_pairs_hook=unique)
        # Also rejects nonfinite exponents, surrogate escapes and nonstandard constants.
        output_json(value).encode('utf-8')
    except OutputContractError:
        raise
    except (ValueError, RecursionError):
        raise OutputContractError('Expected strict JSON with unique keys, finite numbers and valid UTF-8.') from None
    return value


def output_shape(value, keys, label):
    if not isinstance(value, dict) or value.keys() != set(keys.split()):
        raise OutputContractError(label + ' has missing or unknown fields.')


def artifact_parts(path):
    if not isinstance(path, str) or len(path) > 4096 or '\\' in path or re.match(r'^[A-Za-z]:', path):
        raise OutputContractError('Artifact must be a workspace-relative file path.')
    parts = path.split('/')
    if len(parts) > 32 or any(
            not part or part.startswith('.') or SENSITIVE_COMPONENT.search(part)
            or any(unicodedata.category(char) in ('Cc', 'Cf', 'Cs') for char in part) for part in parts):
        raise OutputContractError('Artifact path contains an unsafe or protected component.')
    return parts


def artifact_selection(paths):
    if not isinstance(paths, list) or len(paths) > CANDIDATE_FILES:
        raise OutputContractError('Expected an artifact list with at most eight files.')
    for path in paths:
        artifact_parts(path)
    if len(set(paths)) != len(paths):
        raise OutputContractError('Duplicate artifact path.')
    return paths


def native_output_spec(node):
    options = node['native_output']
    output_shape(options, 'version kind', 'Native output contract')
    if type(options['version']) is not int or options['version'] != 1 or options['kind'] not in ('result', 'review'):
        raise OutputContractError('Native output requires version 1 and kind result or review.')
    paths = artifact_selection(node.get('artifact_paths', []))
    fields = node.get('output_contract', [])
    if not isinstance(fields, list) or len(fields) > 32:
        raise OutputContractError('Native output permits at most 32 field rules.')
    if options['kind'] == 'review' and (paths or fields):
        raise OutputContractError('Review v1 has fixed fields and an empty artifacts list.')
    rules = {}
    for field in fields:
        if not isinstance(field, dict) or set(field) - {'field', 'type', 'required'}:
            raise OutputContractError('Invalid output field rule.')
        path = field.get('field')
        kind, required = field.get('type', 'any'), field.get('required', False)
        if (not isinstance(path, str) or len(path) > 200
                or not re.fullmatch(r'result(?:\.[A-Za-z0-9_-]+)+', path)
                or path in rules or kind not in OUTPUT_TYPES or type(required) is not bool):
            raise OutputContractError('Output rules require distinct typed result.* paths.')
        rules[path] = dict(field=path, type=kind, required=required)
    return dict(version=1, kind=options['kind'], fields=list(rules.values()), allowed_paths=sorted(paths))


def output_text(value, maximum, label):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or '\0' in value:
        raise OutputContractError(f'{label} must contain 1–{maximum} characters of text.')


def output_field(envelope, path):
    value = envelope
    for part in path.split('.'):
        if not isinstance(value, dict) or part not in value:
            return MISSING_OUTPUT_FIELD
        value = value[part]
    return value


def output_type(value):
    return JSON_TYPES.get(type(value))


def check_output_fields(envelope, fields):
    for rule in fields:
        actual = output_field(envelope, rule['field'])
        if actual is MISSING_OUTPUT_FIELD:
            if rule['required']:
                raise OutputContractError('Missing required output field: ' + rule['field'])
        elif rule['type'] not in ('any', output_type(actual)):
            raise OutputContractError('Output field type mismatch: ' + rule['field'])


def validate_structured_answer(answer, spec, candidate=None):
    envelope = strict_output(answer)
    output_shape(envelope, 'result artifacts', 'Structured answer')
    result = envelope['result']
    paths = artifact_selection(envelope['artifacts'])
    if spec['kind'] == 'review':
        output_shape(result, 'schema_version verdict candidate_sha256 summary issues', 'Review result')
    else:
        output_shape(result, 'schema_version summary data', 'Structured result')
        if not isinstance(result['data'], dict):
            raise OutputContractError('Structured result data must be an object.')
        if set(paths) - set(spec['allowed_paths']):
            raise OutputContractError('Answer reports an undeclared artifact.')
        if spec['allowed_paths'] and not paths:
            raise OutputContractError('A delivery with allowed artifact paths must report at least one file.')
    if type(result['schema_version']) is not int or result['schema_version'] != spec['version']:
        raise OutputContractError('Unsupported output schema_version.')
    output_text(result['summary'], 2000, 'Summary')
    if spec['kind'] == 'review':
        validate_review(result, paths, candidate)
    check_output_fields(envelope, spec['fields'])
    return envelope


def validate_review(result, paths, candidate):
    if paths or not candidate or result['candidate_sha256'] != candidate['candidate_sha256']:
        raise OutputContractError('Review must reference the exact candidate SHA-256 and report no artifacts.')
    verdict, issues = result['verdict'], result['issues']
    if verdict not in ('accept', 'needs_correction'):
        raise OutputContractError('Unknown review verdict.')
    if not isinstance(issues, list) or len(issues) > 32 or bool(issues) != (verdict == 'needs_correction'):
        raise OutputContractError('Accept requires no issues; needs_correction requires 1–32 issues.')
    files = {row['path'] for row in candidate['manifest']['files']}
    for issue in issues:
        output_shape(issue, 'severity file description suggestion', 'Review issue')
        if issue['severity'] not in REVIEW_SEVERITIES or not isinstance(issue['file'], str) or issue['file'] not in files:
            raise OutputContractError('Review issue has an unsupported severity or file outside the candidate.')
        output_text(issue['description'], 2000, 'Description')
        output_text(issue['suggestion'], 2000, 'Suggestion')


def candidate_mount(fd):
    with open(f'/proc/self/fdinfo/{fd}', encoding='ascii') as info:
        for line in info:
            if line.startswith('mnt_id:'):
                return line.split(':', 1)[1].strip()
    raise OutputContractError('Candidate file mount could not be verified.')


def hash_candidate_file(root_fd, mount, path):
    parts = artifact_parts(path)
    with ExitStack() as opened:
        directory = root_fd
        for part in parts[:-1]:
            directory = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            opened.callback(os.close, directory)
            if candidate_mount(directory) != mount:
                raise OutputContractError('Candidate path crosses a mount boundary.')
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        opened.callback(os.close, fd)
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or candidate_mount(fd) != mount:
                raise OutputContractError('Candidate must be a regular file without links inside the workspace mount.')
            if before.st_size > CANDIDATE_FILE_BYTES:
                raise OutputContractError('Candidate file exceeds 1000000 bytes.')
            data = stream.read(CANDIDATE_FILE_BYTES + 1)
            after = os.stat(parts[-1], dir_fd=directory, follow_symlinks=False)
            def stamp(meta):
                return (meta.st_dev, meta.st_ino, meta.st_nlink, meta.st_size, meta.st_mtime_ns, meta.st_ctime_ns)
            if len(data) != before.st_size or stamp(before) != stamp(after):
                raise OutputContractError('Candidate file changed during hashing.')
    return dict(path=path, bytes=len(data), sha256=sha256(data).hexdigest()), data


def save_candidate_file(root_fd, path, data):
    parts = artifact_parts(path)
    with ExitStack() as opened:
        directory = root_fd
        for part in parts[:-1]:
            try:
                os.mkdir(part, mode=0o700, dir_fd=directory)
            except FileExistsError:
                pass
            directory = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            opened.callback(os.close, directory)
        fd = os.open(parts[-1], os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)


def candidate_record(workspace, paths, *, snapshot=None):
    paths = artifact_selection(paths)
    if not paths:
        raise OutputContractError('Candidate requires at least one file.')
    files = []
    try:
        workspace = Path(workspace)
        if workspace.resolve(strict=True) != workspace:
            raise OutputContractError('Candidate workspace path has changed.')
        with ExitStack() as opened:
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            fd = os.open(workspace, flags)
            opened.callback(os.close, fd)
            if snapshot is not None:
                snapshot.mkdir(mode=0o700)
                snapshot_fd = os.open(snapshot, flags)
                opened.callback(os.close, snapshot_fd)
            mount = candidate_mount(fd)
            for path in sorted(paths):
                file, data = hash_candidate_file(fd, mount, path)
                files.append(file)
                if snapshot is not None:
                    save_candidate_file(snapshot_fd, path, data)
    except (OSError, RuntimeError):
        raise OutputContractError('Candidate file is missing, unreadable or uses a symbolic link.') from None
    if sum(row['bytes'] for row in files) > CANDIDATE_TOTAL_BYTES:
        raise OutputContractError('Candidate total exceeds 8000000 bytes.')
    manifest = dict(schema_version=1, files=files)
    return dict(manifest=manifest, candidate_sha256=sha256(output_json(manifest).encode('utf-8')).hexdigest())
