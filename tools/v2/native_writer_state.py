"""Refuse unresolved effects across all V2 writers of the same native session."""
import json

from studio.session_sources import UUID

from .session_identity import SessionRefused


def require_native_writer_idle(store, binding, target, *, ignore_operation=None, ignore_pty=None, ignore_chat=None,
                               ignore_title=None):
    """Run under the production writer lease, before starting a new effect."""
    if not isinstance(target, str) or not UUID.fullmatch(target):
        raise SessionRefused('LOCATOR_REFUSED')
    provider, root = binding['provider'], binding['config_root']
    with store.db.tx(read=True) as db:
        operations = db.execute("""
            SELECT kind, id, data FROM records
            WHERE kind IN ('native-session-operation', 'native-session-pty', 'native-session-title')
            AND json_extract(data, '$.binding.provider')=?
            AND json_extract(data, '$.binding.config_root')=?
            AND ? IN (json_extract(data, '$.observed_session'), json_extract(data, '$.target'),
                      json_extract(data, '$.session_id'))
        """, (provider, root, target))
        for kind, ident, raw in operations:
            if (kind=='native-session-operation' and ident==ignore_operation
                    or kind=='native-session-pty' and ident==ignore_pty
                    or kind=='native-session-title' and ident==ignore_title):
                continue
            row = json.loads(raw)
            if (row.get('state') == 'RECOVERY_REQUIRED' or row.get('effect_uncertain') is True
                    or row.get('pending_effect') is True or row.get('input_status') in ('SENDING', 'UNKNOWN')):
                raise SessionRefused('RECOVERY_REQUIRED')
            if row.get('state') in ('STARTING', 'RUNNING'):
                raise SessionRefused('LEASE_HELD')

        workflow = db.execute("""
            SELECT chat.id, json_extract(chat.data, '$.native_session_id'), activation.data
            FROM records chat LEFT JOIN records activation ON activation.kind='activation'
            AND (json_extract(activation.data, '$.session_binding_id')=chat.id
                 OR json_extract(activation.data, '$.chat_key')=chat.id)
            WHERE chat.kind='node-session'
            AND json_extract(chat.data, '$.binding.provider')=?
            AND json_extract(chat.data, '$.binding.config_root')=?
            AND ? IN (json_extract(chat.data, '$.native_session_id'),
                      json_extract(chat.data, '$.binding.session_id'),
                      json_extract(chat.data, '$.binding.new_session_id'))
        """, (provider, root, target))
        for ident, observed, raw in workflow:
            if ident == ignore_chat:
                continue
            if raw is None:
                if observed is not None:
                    raise SessionRefused('RECOVERY_REQUIRED')
                continue
            row = json.loads(raw)
            if (row.get('state') == 'RECOVERY_REQUIRED' or row.get('effect_uncertain') is True
                    or row.get('control_stop_unconfirmed') is True):
                raise SessionRefused('RECOVERY_REQUIRED')
            if row.get('state') == 'RUNNING' and row.get('receipt_verified') is not True:
                raise SessionRefused('LEASE_HELD')
