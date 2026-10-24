"""Detached native-turn supervisor; never writes Controller authority."""
import argparse
from pathlib import Path
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from studio.usable_runtime import Runtime

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--operator', required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--operation', required=True)
    parser.add_argument('--candidate-sha256')
    parser.add_argument('--policy')
    parser.add_argument('--policy-sha256')
    args = parser.parse_args()
    if args.policy or args.policy_sha256:
        if not (args.policy and args.policy_sha256 and args.candidate_sha256):
            parser.error('Workbench workers require exact policy and candidate pins')
        from studio.workbench_runtime import WorkbenchRuntime
        runtime = WorkbenchRuntime(args.operator, args.sha256, args.candidate_sha256, args.policy, args.policy_sha256)
    elif args.candidate_sha256:
        from studio.session_runtime import SessionRuntime
        runtime = SessionRuntime(args.operator, args.sha256, args.candidate_sha256)
    else:
        runtime = Runtime(args.operator, args.sha256)
    runtime.session_center.execute(args.operation)
