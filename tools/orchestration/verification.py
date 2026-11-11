"""Controller-owned commands and machine criteria; result paths never come from models."""
import json
from pathlib import Path
import xml.etree.ElementTree as ET
from .common import Blocked, canonical, digest, tree_hash, save
from .process import execute


def counts(path, format):
    if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
        raise Blocked('invalid machine result path')
    if path.stat().st_size > 8 * 1024 * 1024:
        raise Blocked('oversized machine result')
    if format == 'controller-json':
        obj = json.loads(path.read_bytes())
        if set(obj) != {'tests', 'failures', 'errors', 'skipped'} or any(type(v) is not int or v < 0 for v in obj.values()):
            raise Blocked('invalid test counters')
        return obj
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        raise Blocked('invalid JUnit XML') from None
    local = lambda x: x.tag.rsplit('}', 1)[-1]
    if local(root) not in ('testsuite', 'testsuites'):
        raise Blocked('JUnit suite root required')
    def totals(node):
        cases = [x for x in node.iter() if local(x) == 'testcase']
        has = lambda case, tag: any(local(child) == tag for child in case)
        return {'tests': len(cases), 'failures': sum(has(x, 'failure') for x in cases),
                'errors': sum(has(x, 'error') for x in cases),
                'skipped': sum(has(x, 'skipped') or x.get('status') in ('notrun', 'skipped', 'disabled')
                               or x.get('result') == 'suppressed' for x in cases)}
    for node in root.iter():
        if local(node) in ('testsuite', 'testsuites'):
            actual = totals(node)
            for key in actual:
                if key in node.attrib:
                    try:
                        matched = int(node.attrib[key]) == actual[key]
                    except ValueError:
                        matched = False
                    if not matched:
                        raise Blocked('JUnit summary contradicts actual cases')
    return totals(root)


def verify(config, candidate, artifacts, run, round_number, candidate_hash, acceptance_hash, store=None, *, deny_network=False, capture_asserted_json=False, cancel_check=None):
    artifacts = Path(artifacts)
    artifacts.mkdir(parents=True, exist_ok=False)
    reports = []
    for spec in config['policy']['verification']:
        result_path = artifacts / (digest(spec['id'].encode())[:16] + ('.xml' if spec['result_format'] == 'junit' else '.json'))
        # No stale result can pass. A unique directory is allocated for each round.
        argv = [x.replace('{artifacts}', str(artifacts)).replace('{result}', str(result_path)) for x in spec['argv']]
        runner = spec.get('runner')
        for path, expected in spec.get('dependency_sha256', {}).items():
            if digest(Path(path).read_bytes()) != expected:
                raise Blocked('Approved verifier dependency changed')
        binding = {'run_id': run, 'round': round_number, 'verification_id': spec['id'],
                   'candidate_sha256': candidate_hash, 'acceptance_sha256': acceptance_hash}
        operation = store.intent(run, 'VERIFICATION', binding) if store else None
        try:
            if runner not in ('json-assertion', 'exit-code'):
                raise Blocked('Execution requires parent-owned assertions; legacy candidate-reported counters are unsupported')
            cancellation = {'cancel_check': cancel_check} if cancel_check is not None else {}
            process, stdout, _ = execute(argv, candidate, artifacts / 'home',
                read=[str(candidate), *spec.get('read_paths', [])], input_text=spec.get('stdin', ''),
                write=[],
                timeout=config['policy']['limits']['step_timeout_seconds'], cap=config['policy']['limits']['max_log_bytes'],
                store=store, run=run, backend=config.get('verification_isolation', 'landlock'),
                context={**binding, 'verification_operation_id': operation}, deny_network=deny_network, **cancellation)
            # Exit is observed before validation, and is never a PASS signal.
            if store:
                store.finish(operation, 'EXECUTED', process_operation_id=process['operation_id'])
            if runner:
                if runner == 'json-assertion':
                    matched = False
                    try:
                        def unique(pairs):
                            obj = {}
                            for key, value in pairs:
                                if key in obj:
                                    raise ValueError('duplicate JSON key')
                                obj[key] = value
                            return obj
                        actual = json.loads(stdout, object_pairs_hook=unique, parse_constant=lambda value: (_ for _ in ()).throw(ValueError('nonfinite JSON')))
                        matched = canonical(actual) == canonical(spec['expected_json'])
                    except (ValueError, TypeError, UnicodeError):
                        pass
                    counter = dict(tests=1, failures=int(process['exit'] != 0 or not matched), errors=0, skipped=0)
                elif runner == 'exit-code':
                    counter = dict(tests=1, failures=int(process['exit'] != 0), errors=0, skipped=0)
                else:
                    raise Blocked('Unknown trusted verification runner')
                # Only the Controller can reach this result path; candidate code
                # received neither the path nor write access to its directory.
                save(result_path, counter)
            counter = counts(result_path, spec['result_format'])
            passed = process['exit'] == 0 and not any(process[k] for k in ('timeout', 'overflow', 'cancelled'))
            passed = passed and counter['tests'] >= spec['min_cases'] and counter['skipped'] <= spec['max_skipped']
            passed = passed and counter['failures'] == counter['errors'] == 0 and sum(counter[k] for k in ('failures', 'errors', 'skipped')) <= counter['tests']
            if tree_hash(candidate) != candidate_hash:
                raise Blocked('candidate changed during verification')
            report = {'id': spec['id'], 'run_id': run, 'round': round_number, 'operation_id': operation,
                      'candidate_sha256': candidate_hash, 'acceptance_sha256': acceptance_hash,
                      'argv': argv, 'cwd': str(candidate), 'executor': 'controller-runner',
                      'assertion': {k: spec[k] for k in ('runner', 'stdin', 'expected_json', 'read_paths', 'dependency_sha256', 'min_cases', 'max_skipped') if k in spec},
                      'process': process, 'counts': counter, 'passed': bool(passed),
                      'result_path': str(result_path), 'result_sha256': digest(result_path.read_bytes())}
            if capture_asserted_json and passed and runner == 'json-assertion':
                report['asserted_json'] = actual
            if store:
                # Only Controller-validated counters are published. No argv, path or source in these events.
                store.finish(operation, 'VALIDATED', counts=counter, passed=bool(passed), result_sha256=report['result_sha256'])
            reports.append(report)
        except Exception:
            if store:
                store.finish(operation, 'VERIFICATION_BLOCKED')
            raise
    return reports
