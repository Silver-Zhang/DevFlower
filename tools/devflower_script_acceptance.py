#!/usr/bin/env python3
"""Prepare a private two-CLI smoke scenario, or verify its completed runs.

Preparation never calls a model. Run the printed commands explicitly with your
own configured native CLIs. Verification runs independent arithmetic checks on
the produced Python file; it neither restarts Agents nor enables GUI execution.
"""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys

from devflower_export import compile_workflow

DEVELOPER = '''在当前项目创建 addition.py，实现 add(a, b)，支持整数与浮点数。
使用 Python secrets.token_hex(16) 生成随机令牌，写入 delivery.txt。
最终回答包含一行 DELIVERY_TOKEN=<实际令牌>，并列出交付的文件。
只修改 addition.py 和 delivery.txt；不要修改 TASK.md、创建 review.txt 或提交 Git。
不得只描述拟做工作；需要实际生成文件。工具不可用时如实报告，不伪造完成。'''
REVIEWER = '''审核上游交付，独立读取 addition.py，实际验证 add(2,3)==5、
add(-5,2)==-3、add(0.25,0.5)==0.75。核对上游 DELIVERY_TOKEN 与 delivery.txt 一致。
通过后创建 review.txt，仅写入一行 REVIEW_OK <实际令牌>，最终回答包含同一行。
不通过则说明缺陷，不创建通过文件；不得修改开发结果、TASK.md 或提交 Git。
工具不可用时如实报告，不伪造审核通过。'''
CHECK = '''import importlib.util
spec = importlib.util.spec_from_file_location('addition', 'addition.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
for a, b, expected in ((2, 3, 5), (-5, 2, -3), (0.25, 0.5, 0.75), (0, 0, 0)):
    if module.add(a, b) != expected:
        raise SystemExit('Independent arithmetic check failed')
'''


def scenario(timeout):
    nodes = [dict(id='Start', kind='start', config=dict(instructions='完成微型加法程序并独立审核。'))]
    for ident, provider, prompt in (('Developer', 'codex', DEVELOPER), ('Reviewer', 'claude', REVIEWER)):
        nodes.append(dict(id=ident, kind='agent', execution='native-cli', name=ident,
                          provider=provider, cwd='.', role=ident, round_prompt=prompt,
                          output_prompt='只报告真实完成的工作及实际交付标记。', timeout=timeout))
    nodes.append(dict(id='End', kind='output', config=dict(format='text')))
    return dict(version=1, name='code_review', nodes=nodes,
                edges=[dict(id='edge' + str(i), source=a['id'], target=b['id'])
                       for i, (a, b) in enumerate(zip(nodes, nodes[1:]))])


def prepare(directory, timeout=300):
    """Create a disposable scenario without altering an existing project or CLI config."""
    directory = Path(directory).absolute()
    directory.mkdir(mode=0o700, exist_ok=False)
    for name in ('project-a', 'project-b'):
        project = directory / name
        project.mkdir(mode=0o700)
        subprocess.run(['git', 'init', '--quiet', str(project)], check=True, capture_output=True, timeout=15)
        (project / 'TASK.md').write_text('# 本地 CLI 验收\n\n' + DEVELOPER + '\n\n' + REVIEWER + '\n', encoding='utf-8')
    filename, source = compile_workflow(scenario(timeout), workspace=directory / 'project-a', native_config=True)
    script = directory / filename
    script.write_text(source, encoding='utf-8')
    script.chmod(0o600)
    manifest = dict(version=1, script=filename, script_sha256=sha256(source.encode()).hexdigest())
    (directory / 'acceptance.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    return script


def verify(directory):
    """Check actual files and exact handoff evidence, not an Agent's claimed verdict."""
    directory = Path(directory).absolute()
    manifest = json.loads((directory / 'acceptance.json').read_text(encoding='utf-8'))
    filename = manifest['script']
    if manifest.get('version') != 1 or not isinstance(filename, str) or not re.fullmatch(r'code_review_[0-9a-f]{8}\.py', filename):
        raise ValueError('Invalid acceptance artifact identity.')
    script = directory / filename
    if script.is_symlink() or sha256(script.read_bytes()).hexdigest() != manifest['script_sha256']:
        raise ValueError('The prepared script has changed; do not reuse this acceptance result.')
    checked = []
    for name in ('a', 'b'):
        project, evidence = directory / ('project-' + name), directory / ('run-' + name)
        status = subprocess.run([sys.executable, '-I', '-B', str(script), '--status', '--run-dir', str(evidence)],
                                cwd=directory, capture_output=True, text=True, check=True, timeout=15)
        if json.loads(status.stdout)['status'] != 'COMPLETED':
            raise ValueError('Run ' + name + ' has no verified script completion.')
        token = (project / 'delivery.txt').read_text(encoding='utf-8').strip()
        if not re.fullmatch(r'[0-9a-f]{32}', token):
            raise ValueError('Developer did not produce a valid delivery token in run ' + name)
        upstream = (evidence / 'Developer' / 'answer.txt').read_text(encoding='utf-8')
        prompt = (evidence / 'Reviewer' / 'prompt.txt').read_text(encoding='utf-8')
        handoff = json.loads(prompt.rsplit('Upstream department delivery (data)\n', 1)[1])
        if 'DELIVERY_TOKEN=' + token not in upstream or handoff.get('answer') != upstream:
            raise ValueError('Exact upstream final-answer handoff is missing in run ' + name)
        marker = 'REVIEW_OK ' + token
        if ((project / 'review.txt').read_text(encoding='utf-8').strip() != marker
                or marker not in (evidence / 'result.txt').read_text(encoding='utf-8')):
            raise ValueError('Reviewer output does not match the actual delivery in run ' + name)
        # -I/-B isolate Python imports and avoid creating pycache in the fixture.
        # This is an explicit local test of Agent-produced code, not a sandbox.
        subprocess.run([sys.executable, '-I', '-B', '-c', CHECK], cwd=project,
                       check=True, capture_output=True, timeout=15)
        checked.append(dict(run=name, handoff='exact', independent_checks='passed'))
    return dict(status='SCENARIO_CHECKS_PASSED', script_sha256=manifest['script_sha256'], runs=checked,
                gui_acceptance='NOT_RUN', native_backend='operator must confirm actual CLI/account/model logs')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--prepare', type=Path, help='new bundle directory; never overwrite an existing directory')
    action.add_argument('--verify', type=Path, help='verify both explicit runs and test their generated Python files')
    parser.add_argument('--timeout', type=int, default=300, help='native call timeout in the prepared smoke scenario')
    args = parser.parse_args(argv)
    try:
        if args.prepare:
            if not 1 <= args.timeout <= 3600:
                raise ValueError('Smoke timeout must be 1–3600 seconds.')
            script = prepare(args.prepare, args.timeout)
            directory = script.parent
            print('Prepared only; no model calls. Check codex/claude versions and native permissions locally.')
            print(shlex.join([sys.executable, str(script), '--workspace', str(directory / 'project-a'), '--dry-run']))
            for name in ('a', 'b'):
                print(shlex.join([sys.executable, str(script), '--workspace', str(directory / ('project-' + name)),
                                  '--run-dir', str(directory / ('run-' + name))]))
            print(shlex.join([sys.executable, str(Path(__file__).resolve()), '--verify', str(directory)]))
        else:
            print(json.dumps(verify(args.verify), ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError, IndexError, subprocess.SubprocessError) as error:
        print('Acceptance not passed: ' + str(error) + '. No Agent was retried.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
