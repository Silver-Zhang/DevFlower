"""Read existing managed runs through the same evidence projection as recovery."""
import json
import re

from studio.graph import Invalid
from studio.server import Conflict

from .recovery import project
from .worker_facts import INTERNAL_FIELDS


class ManagedRunHistory:
    def __init__(self, store):
        self.store, self.flow, self.storage = store, store.db, store.state

    def _records(self, run_id=None, *, include_events=True, workspace=None):
        # One committed SQLite snapshot. Run detail does not load another run's
        # payloads, and its compatibility event tail is separate from the cursor.
        with self.flow.tx(read=True) as db:
            if workspace is not None:
                records = db.execute('''WITH scoped_runs AS (
                    SELECT id, data FROM records WHERE kind='run'
                      AND CASE WHEN json_valid(data) THEN json_extract(data,'$.workspace') END=?
                )
                SELECT 'run', id, data FROM scoped_runs
                UNION ALL SELECT kind,id,data FROM records WHERE kind='definition' AND id IN (
                    SELECT json_extract(data,'$.definition_id') FROM scoped_runs)
                UNION ALL SELECT kind,id,data FROM records WHERE kind IN ('activation','native-step')
                    AND CASE WHEN json_valid(data) THEN json_extract(data,'$.run') END IN (SELECT id FROM scoped_runs)''',
                    (workspace,))
            elif run_id is None:
                records = db.execute("SELECT kind,id,data FROM records WHERE kind IN ('run','definition','activation','native-step')")
            else:
                records = db.execute("""SELECT kind,id,data FROM records WHERE kind='run' AND id=?
                    UNION ALL SELECT kind,id,data FROM records WHERE kind='definition' AND id=(
                        SELECT json_extract(data,'$.definition_id') FROM records WHERE kind='run' AND id=?)
                    UNION ALL SELECT kind,id,data FROM records WHERE kind IN ('activation','native-step')
                        AND CASE WHEN json_valid(data) THEN json_extract(data,'$.run') END=?""", (run_id, run_id, run_id))
            rows = {(kind,key): json.loads(raw) for kind,key,raw in records}
            if not include_events:
                return rows, []
            if run_id is None:
                events = db.execute('SELECT seq,data FROM events ORDER BY seq')
            else:
                tail = db.execute('SELECT seq,data FROM events WHERE run=? ORDER BY seq DESC LIMIT 100', (run_id,))
                events = reversed(tail.fetchall())
            return rows, [dict(json.loads(raw), seq=seq) for seq,raw in events]

    def _snapshot(self,run,rows,events):
        definition=rows.get(('definition',run['definition_id']))
        if definition is None:raise Invalid('冻结执行定义缺失，请保留本次运行并核验状态目录')
        nodes=[r for (kind,_),r in rows.items() if kind=='activation' and r.get('run')==run['id']]
        steps=[r for (kind,_),r in rows.items() if kind=='native-step' and r.get('run')==run['id']]
        value=project(self,run,definition,nodes,steps)
        for key in INTERNAL_FIELDS:
            value['run'].pop(key, None)
        value.update(definition={k:v for k,v in definition.items() if k not in ('bindings','runtime_sources')},
                     events=[e for e in events if e.get('run')==run['id']][-1000:])
        return value

    def snapshot(self,workspace,run_id):
        self.store.workspace(workspace)
        if not isinstance(run_id,str) or not re.fullmatch(r'[0-9a-f]{32}',run_id):raise Invalid('运行身份无效')
        rows,events=self._records(run_id);run=rows.get(('run',run_id))
        if run is None:raise Invalid('找不到此运行，请从当前工作空间历史选择')
        if run['workspace']!=workspace:raise Conflict('运行不属于当前工作空间')
        return self._snapshot(run,rows,events)

    def runs(self, workspace):
        self.store.workspace(workspace)
        rows, events = self._records(include_events=False, workspace=workspace)
        result = []
        for (kind, _), run in rows.items():
            if kind != 'run':
                continue
            value = self._snapshot(run, rows, events)
            public = value['run']
            fields = ('id', 'definition_id', 'draft_id', 'draft_revision', 'launch_intent',
                      'created_at', 'reason', 'state', 'cancel_requested', 'provider_outcome', 'local_status')
            result.append(dict({key: public.get(key) for key in fields},
                               name=value['definition']['draft_document']['name']))
        return result
