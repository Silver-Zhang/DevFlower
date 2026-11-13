"""Public data projection, independent from authorization and native receipts."""
from orchestration.privacy import redact
from .agent_sessions import safe_text
from .graph import Invalid

PRIVATE_FIELDS = frozenset({'receipt', 'credentials', 'authorization', 'authentication',
    'api_key', 'access_token', 'refresh_token', 'auth_token', 'password', 'secret', 'cookie'})


REPLY = {'type': 'object', 'additionalProperties': False,
         'required': ['reply','summary','accepted','result_json','artifacts','evidence','issues','next'], 'properties': {
    'reply': {'type': 'string'}, 'summary': {'type': 'string'}, 'accepted': {'type': 'boolean'},
    'result_json': {'type': 'string'}, 'artifacts': {'type': 'array', 'items': {'type': 'string'}},
    'evidence': {'type': 'array', 'items': {'type': 'string'}},
    'issues': {'type': 'array', 'items': {'type': 'string'}}, 'next': {'type': 'string'}}}


def public_field(path):
    if isinstance(path, str) and any(part.casefold() in PRIVATE_FIELDS for part in path.split('.')):
        raise Invalid('私有回执和认证字段不能作为上下游数据')
    return path


def public_data(value, secrets=()):
    if isinstance(value, dict):
        return {key: public_data(child, secrets) for key, child in value.items()
                if key.casefold() not in PRIVATE_FIELDS}
    if isinstance(value, list):
        return [public_data(child, secrets) for child in value]
    if isinstance(value, str):
        return safe_text(redact(value, secrets))
    return value
