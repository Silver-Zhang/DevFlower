"""Canonical portable V2 drafts and a scoped, read-only validation path."""
import hashlib
import hmac
import json
import math
import re
from urllib.parse import parse_qs

from orchestration.common import Blocked
from studio.server import Conflict

from .documents import validate_draft
from .workspace_select import capture_workspace_proof, require_current

FORMAT = 'devflower.v2.workflow-draft'
MAX_ARCHIVE_BYTES = 420000
MAX_QUERY_BYTES = 1024
HEX_64 = re.compile(r'[0-9a-f]{64}')
LENGTH = re.compile(r'[0-9]+')
ARCHIVE_FIELDS = {'format', 'version', 'document', 'document_sha256'}
ERRORS = {
    'DRAFT_TRANSFER_REQUEST_INVALID': (400, '工作流移交请求无效。'),
    'DRAFT_TRANSFER_FORMAT_INVALID': (400, '工作流文件格式或编码无效。'),
    'DRAFT_TRANSFER_DIGEST_MISMATCH': (400, '工作流文件内容校验失败。'),
    'DRAFT_TRANSFER_DOCUMENT_INVALID': (400, '工作流草稿结构无效或超过限制。'),
    'DRAFT_TRANSFER_SCOPE_STALE': (409, '当前工作空间暂不能核验，请重新打开后再试。'),
}


class DraftTransferError(ValueError):
    def __init__(self, code):
        self.code = code
        self.status, message = ERRORS[code]
        super().__init__(message)


def read_request(query, headers, stream):
    try:
        if len(query.encode('utf-8')) > MAX_QUERY_BYTES:
            raise ValueError()
        values = parse_qs(query, keep_blank_values=True, strict_parsing=True, errors='strict', max_num_fields=1)
        if set(values) != {'workspace'} or len(values['workspace']) != 1 or not HEX_64.fullmatch(values['workspace'][0]):
            raise ValueError()
        lengths = headers.get_all('Content-Length', [])
        types = headers.get_all('Content-Type', [])
        if (len(lengths) != 1 or not LENGTH.fullmatch(lengths[0]) or headers.get_all('Transfer-Encoding', [])
                or len(types) != 1 or types[0].split(';', 1)[0].strip().lower() != 'application/json'):
            raise ValueError()
        length = int(lengths[0])
        if not 1 <= length <= MAX_ARCHIVE_BYTES:
            raise ValueError()
        raw = stream.read(length)
        if len(raw) != length:
            raise ValueError()
        return values['workspace'][0], raw
    except (OSError, ValueError, OverflowError):
        raise DraftTransferError('DRAFT_TRANSFER_REQUEST_INVALID') from None


def ecmascript_number(value):
    # Python and ECMAScript choose the nearest shortest round-trip binary64
    # representation. Reposition Python's decimal point at ECMAScript cutoffs.
    if type(value) is int and abs(value) <= (1 << 53) - 1:
        return str(value)
    value = float(value)
    if not math.isfinite(value):
        raise ValueError()
    if value == 0:
        return '0'
    coefficient, _, exponent = repr(abs(value)).partition('e')
    whole, _, decimal = coefficient.partition('.')
    digits = whole + decimal
    significant = digits.lstrip('0')
    point = len(whole) + int(exponent or 0) - (len(digits) - len(significant))
    significant = significant.rstrip('0')
    sign = '-' if value < 0 else ''
    if 0 < point <= 21:
        if point >= len(significant):
            return sign + significant + '0' * (point - len(significant))
        return sign + significant[:point] + '.' + significant[point:]
    if -6 < point <= 0:
        return sign + '0.' + '0' * (-point) + significant
    mantissa = significant[0] + ('.' + significant[1:] if len(significant) > 1 else '')
    return sign + mantissa + 'e' + format(point - 1, '+d')


def canonical_json(value):
    if value is None:
        return 'null'
    if type(value) is bool:
        return 'true' if value else 'false'
    if type(value) in (int, float):
        return ecmascript_number(value)
    if isinstance(value, str):
        if '\0' in value:
            raise ValueError()
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return '[' + ','.join(canonical_json(item) for item in value) + ']'
    if isinstance(value, dict):
        # Supported graph keys are ASCII. UTF-16 code-unit order also matches
        # the browser when rejecting a document with unsupported keys.
        keys = sorted(value, key=lambda key: key.encode('utf-16-be'))
        return '{' + ','.join(canonical_json(key) + ':' + canonical_json(value[key]) for key in keys) + '}'
    raise ValueError()


def parse_archive(raw):
    try:
        archive = json.loads(raw.decode('utf-8'))
        if (not isinstance(archive, dict) or set(archive) != ARCHIVE_FIELDS or archive['format'] != FORMAT
                or type(archive['version']) is not int or archive['version'] != 1
                or not isinstance(archive['document_sha256'], str) or not HEX_64.fullmatch(archive['document_sha256'])):
            raise ValueError()
        # A canonical encoder cannot produce duplicate keys, alternate
        # escapes, a BOM, or any extra whitespace, even after JSON decoding.
        if (canonical_json(archive) + '\n').encode('utf-8') != raw:
            raise ValueError()
        document = archive['document']
        actual = hashlib.sha256(canonical_json(document).encode('utf-8')).hexdigest()
    except (ValueError, RecursionError, OverflowError):
        raise DraftTransferError('DRAFT_TRANSFER_FORMAT_INVALID') from None
    if not hmac.compare_digest(actual, archive['document_sha256']):
        raise DraftTransferError('DRAFT_TRANSFER_DIGEST_MISMATCH')
    try:
        validate_draft(document)
    except (ValueError, TypeError, RecursionError, OverflowError):
        raise DraftTransferError('DRAFT_TRANSFER_DOCUMENT_INVALID') from None
    return actual


def validate_archive(store, workspace, raw):
    document_sha = parse_archive(raw)
    try:
        proof = capture_workspace_proof(store.state)
        row = store.workspace(workspace, existing_only=True)
        if row.get('id') != workspace:
            raise ValueError()
        require_current(store.state, proof)
    except (ValueError, TypeError, KeyError, OSError, Conflict, Blocked):
        raise DraftTransferError('DRAFT_TRANSFER_SCOPE_STALE') from None
    return {'workspace': workspace, 'document_sha256': document_sha, 'valid': True}
