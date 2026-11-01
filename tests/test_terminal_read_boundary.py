"""Public terminal boundary; separate from internal protocol-mechanics fixtures."""
import unittest
from pathlib import Path
from orchestration.common import Blocked

class NativeTerminalBoundaryTests(unittest.TestCase):
 def test_native_terminal_cannot_bypass_narrow_ask_contract(self):
  import importlib.util
  p=Path(__file__).resolve().parents[1]/'tools/devflower_native_terminal.py';spec=importlib.util.spec_from_file_location('isolated_terminal_entry',p);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
  for ordinary in [False,True]:
   with self.assertRaisesRegex(Blocked,'读取边界'):module.native_cli_argv(dict(provider='codex',execution_backend='native-local',native_interactive=ordinary),'synthetic-id')
