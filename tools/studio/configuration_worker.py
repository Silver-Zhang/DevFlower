"""Detached supervisor for one configuration proposal; no workflow or delivery."""
import argparse,sys
from pathlib import Path
sys.dont_write_bytecode=True
sys.path.insert(0,str(Path(__file__).parents[1]))
from studio.workbench_runtime import WorkbenchRuntime
from studio.configuration_assistant import ConfigurationAssistant
p=argparse.ArgumentParser()
for key in ('operator','operator-sha256','candidate-sha256','policy','policy-sha256','job'):p.add_argument('--'+key,required=True)
a=p.parse_args();rt=WorkbenchRuntime(a.operator,a.operator_sha256,a.candidate_sha256,a.policy,a.policy_sha256)
ConfigurationAssistant(rt).execute(a.job)
