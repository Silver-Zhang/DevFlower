"""Real HTTP/Chromium UI regressions with SQLite fixtures, not model acceptance."""
import argparse
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from test_usable import RuntimeTests
from test_studio import database_fixture
from studio.usable_server import handler
from orchestration.common import tree_hash
from orchestration.store import Store, process_identity
from test_studio_workflow import custom_document


def canvas_runs(runtime, fixture):
    """Controller database display fixtures; explicitly not Stable/model evidence."""
    request = fixture.request();request['workflow'] = custom_document()
    config = runtime.prepare(request)['data']['config']
    store = Store(runtime.control)
    runs = {}
    try:
        for case in ('failed', 'stale', 'dead', 'delivered'):
            number = 2 if case == 'stale' else 1
            data = dict(config=config, source={'root': str(fixture.source)}, round=number,
                acceptance_sha256='d' * 64, authorization_sha256='e' * 64,
                sessions={'implementer': '11111111-1111-1111-1111-111111111111'})
            run = store.create(data);runs[case] = run
            folder = runtime.control / run / 'round-1';candidate = folder / 'candidate'
            candidate.mkdir(parents=True);(candidate / 'README.md').write_text('display fixture only')
            (folder / 'changes.diff').write_text('+ display fixture diff only\n')
            candidate_hash = tree_hash(candidate)
            report_round = 1
            verdict = 'NEEDS_CORRECTION' if case == 'failed' else 'ACCEPT'
            review = dict(report=dict(run_id=run, round=report_round, candidate_sha256=candidate_hash,
                schema_version=1, acceptance_sha256='d' * 64, verdict=verdict, limitations=[],
                contracts=[dict(id=identity, status='PASS', evidence_refs=['source-inspection'])
                           for identity in config['policy']['required_contracts']],
                findings=[dict(id='F-canvas', status='OPEN', severity='P1', title='实际审核展示夹具',
                               contract_ids=config['policy']['required_contracts'][:1],
                               evidence_refs=['source-inspection'])] if case == 'failed' else []))
            store.transition(run, 'IMPLEMENTING' if case == 'dead' else 'COMPLETED' if case == 'delivered' else 'BLOCKED',
                round=number, candidate={'sha256': candidate_hash}, candidate_round=report_round,
                candidate_path=str(candidate), review=review)
            for i, identity in enumerate(config['studio_workflow']['verification_ids']):
                op = store.intent(run, 'VERIFICATION', dict(run_id=run, round=report_round, verification_id=identity,
                    candidate_sha256=candidate_hash, acceptance_sha256='d' * 64))
                store.finish(op, 'VALIDATED', passed=not(case == 'failed' and i == 1),
                    counts=dict(tests=i + 1, failures=int(case == 'failed' and i == 1), errors=0, skipped=0))
            if case == 'dead':
                proc = process_identity(os.getpid());proc['start'] = '0'
                op = store.intent(run, 'WORKER', {});store.finish(op, 'RUNNING', process=proc)
            if case == 'delivered':
                delivered_config = json.loads(json.dumps(config))
                delivered_config['policy']['delivery'].update(commit=True, push=True)
                delivered_config['github_delivery'] = {'repository': 'fixture/project'}
                store.transition(run, 'COMPLETED', config=delivered_config,
                    github_receipt=dict(commit='a' * 40, pushed=True, pr=dict(number=42,
                        url='https://github.com/fixture/project/pull/42', head='a' * 40)))
        for case in ('legacy-delivered', 'legacy-mismatched', 'legacy-unsafe'):
            data = json.loads(json.dumps(store.get(runs['delivered'])['data']))
            data['config'].pop('studio_workflow')
            run = store.create(data);runs[case] = run
            data['review']['report']['run_id'] = run
            if case == 'legacy-mismatched':
                data['github_receipt']['pr']['head'] = 'b' * 40
            if case == 'legacy-unsafe':
                data['github_receipt']['pr']['url'] = 'javascript:alert(1)'
            store.transition(run, 'COMPLETED', **data)
    finally:
        store.db.close()
    return runs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chromium", required=True)
    parser.add_argument("--node", default="node")
    parser.add_argument("--playwright-module", default="playwright")
    parser.add_argument("--artifacts", required=True)
    args = parser.parse_args()
    output = Path(args.artifacts).resolve()
    root = Path(__file__).resolve().parents[1]
    if output.is_relative_to(root) or root.is_relative_to(output):
        parser.error("Artifacts must be outside source")
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    fixture = RuntimeTests()
    fixture.setUp()
    runtime = fixture.runtime
    # Fixed GitHub data for preview-only HTTP/Chromium tests; no model or delivery.
    runtime.github.api = lambda endpoint, *args, **kwargs: (dict(title='真实 HTTP 导入夹具', body='检查任务范围与验收',
        html_url='https://github.com/fixture/project/issues/6') if '/issues/' in endpoint else [dict(number=42)])
    runtime.github.tip = lambda *args: "f" * 40
    db, run_a, data = database_fixture(runtime.control, state="READY")
    data["config"]["project"] = fixture.project["id"]
    data["source"] = {"root": str(fixture.source)}
    data["acceptance_sha256"] = "c" * 64
    data["config"]["bindings"] = {}
    data["sessions"] = {}
    data["round"] = data["candidate_round"] = 1
    data["review"] = {"report": dict(schema_version=1, run_id=run_a, round=1,
        candidate_sha256=data['candidate']['sha256'], acceptance_sha256=data['acceptance_sha256'],
        verdict='ACCEPT', findings=[], limitations=[],
        contracts=[dict(id=identity, status='PASS', evidence_refs=['source-inspection'])
                   for identity in data['config']['policy']['required_contracts']])}
    db.execute("UPDATE runs SET data=? WHERE id=?", (json.dumps(data), run_a))
    run_b = "b" * 32
    other = json.loads(json.dumps(data))
    other["round"] = other["candidate_round"] = 2
    other["review"]["report"].update(run_id=run_b, round=2, verdict="BLOCKED")
    db.execute("INSERT INTO runs VALUES(?,?,?)", (run_b, "BLOCKED", json.dumps(other)))
    db.commit();db.close()
    canvas = canvas_runs(runtime, fixture)
    token = "test-fixture-only-capability-not-a-real-operator-permit"
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(runtime, token))
    thread = threading.Thread(target=server.serve_forever, daemon=True);thread.start()
    (output / "access.json").write_text(json.dumps(dict(
        url=f"http://127.0.0.1:{server.server_port}/", token=token, source=str(fixture.source), a=run_a, b=run_b, canvas=canvas)))
    try:
        result = subprocess.run([args.node, str(Path(__file__).with_suffix(".cjs")), str(output)],
                                env=dict(os.environ, CHROMIUM=args.chromium, PLAYWRIGHT_MODULE=args.playwright_module,
                                         TMPDIR=os.environ.get('BROWSER_TMPDIR', os.environ.get('TMPDIR', '/tmp'))),
                                timeout=180)
        return result.returncode
    finally:
        server.shutdown();server.server_close();thread.join()
        fixture.doCleanups()


if __name__ == "__main__":
    raise SystemExit(main())
