"""Fail-closed accounting of full browser runs; all workers are synthetic mocks."""
import json,os,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import workbench_browser as browser_runner

ISOLATED=['issue49-delegated-origin','issue58-session-context','issue64-shared-codex-pure-chat-choice']
class BrowserInventoryTests(unittest.TestCase):
 def invoke(self,kind):
  with tempfile.TemporaryDirectory() as folder:
   out=Path(folder)/'artifacts';inventory=['shared-alpha','shared-beta',*ISOLATED]
   def worker(command,env,timeout):
    dest=Path(command[command.index('--artifacts')+1]);dest.mkdir(parents=True)
    names=[env['TEST_FILTER']] if env.get('TEST_FILTER') else ['shared-alpha','shared-beta']
    if dest.name=='shared':
     if kind=='missing':names=['shared-alpha']
     if kind=='unknown':names=['shared-alpha','FORGED-CASE']
     if kind=='duplicate':names=['shared-alpha','shared-alpha','shared-beta']
    rows=[dict(name=name,passed=True) for name in names]
    (dest/'workbench-results.json').write_text(json.dumps(dict(complete=kind!='incomplete',results=rows)))
    return subprocess.CompletedProcess(command,1 if kind=='nonzero' else 0)
   args=['workbench_browser.py','--chromium','synthetic','--artifacts',str(out)]
   with patch.dict(os.environ,{},clear=True),patch.object(sys,'argv',args),patch.object(browser_runner.subprocess,'check_output',return_value=json.dumps(dict(cases=inventory)).encode()),patch.object(browser_runner.subprocess,'run',side_effect=worker):
    result=browser_runner.main()
   return result,json.loads((out/'workbench-results.json').read_text())
 def test_exact_full_inventory_is_accounted_once(self):
  code,result=self.invoke('valid');self.assertEqual(code,0);self.assertTrue(result['inventory_verified']);self.assertEqual(result['tests'],5)
 def test_missing_case_cannot_report_green(self):
  with self.assertRaisesRegex(RuntimeError,'inventory mismatch'):self.invoke('missing')
 def test_unknown_case_cannot_replace_an_expected_case(self):
  with self.assertRaisesRegex(RuntimeError,'inventory mismatch'):self.invoke('unknown')
 def test_duplicate_case_is_not_accepted(self):
  with self.assertRaisesRegex(RuntimeError,'Missing or repeated'):self.invoke('duplicate')
 def test_nonzero_worker_never_turns_green(self):
  code,_=self.invoke('nonzero');self.assertEqual(code,1)
 def test_incomplete_worker_never_turns_green(self):
  with self.assertRaisesRegex(RuntimeError,'did not finish'):self.invoke('incomplete')
