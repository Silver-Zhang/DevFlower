"""Managed history selection for one explicitly enrolled independent HOME."""
import json

from orchestration.common import Blocked, canonical, digest
from studio.graph import Invalid

from .native_session_links import verified_workflow, workflow_use
from .native_sessions import prove_file


def managed_home_sessions(store, workspace, cwd, home):
    from .agent_execution import history_source
    try:
        source = history_source(store, home)
    except (OSError, ValueError, Blocked):
        return [], digest(canonical([workspace,cwd,home['sha256'],[]]))[:32]
    provider, root = home['provider'], home['config_root']
    results, seen = [], set()
    with store.db.tx(read=True) as db:
        rows = db.execute("""
            SELECT data FROM records WHERE kind='node-session'
            AND json_extract(data,'$.workspace')=?
            AND json_extract(data,'$.binding.provider')=?
            AND json_extract(data,'$.binding.config_root')=?
            AND json_type(data,'$.native_history')='object'
            ORDER BY rowid DESC LIMIT 48
        """, (workspace,provider,root)).fetchall()
        for raw, in rows:
            chat = json.loads(raw)
            sid = chat.get('native_session_id')
            if not isinstance(sid,str) or sid in seen:
                continue
            seen.add(sid)
            try:
                if chat['binding'].get('native_home') != home or chat['binding']['cwd'] != cwd:
                    continue
                _,_,_,_,receipt,binding,actual_cwd = workflow_use(store,db,chat,root,sid,provider)
                creation = verified_workflow(store,root,sid,provider)
                history = chat['native_history']
                if (creation.get('native_home') != home['id'] or creation['workspace'] != workspace
                        or creation['cwd'] != cwd or actual_cwd != cwd or binding.get('native_home') != home
                        or history['history_identity'] != source.profile.identity.fields()
                        or receipt.get('native_history') != {'sha256':history['sha256']}):
                    continue
                locator,ref,updated = prove_file(source,workspace,cwd,sid,history['relative'])
                if ref.sha != history['sha256']:
                    continue
                public = dict(handle=source.registry.handle(locator),provider=provider,updated_at=updated)
                results.append((public,locator,ref))
            except (OSError, ValueError, KeyError, TypeError, Blocked, Invalid, StopIteration):
                continue
    revision = digest(canonical([workspace,cwd,home['sha256'],
                                  [[row['handle'],ref.sha] for row,_,ref in results]]))[:32]
    return results, revision
