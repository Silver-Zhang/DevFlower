"""Synthetic private-list projection; preserves native records."""
import json,unittest
from test_workbench_runtime import fixture,structured_session
from studio.workbench_sessions import WorkbenchSessions,_list_projection

class PrivateProjectionTests(unittest.TestCase):
 def test_private_url_title_never_rewrites_history(self):
  base,rt=fixture(self);path,row=structured_session(base,rt,'private-title','codex')
  with path.open('a') as f:f.write(json.dumps(dict(type='custom-title',customTitle='Synthetic https://private.example.invalid/share/SECRET token=synthetic-secret'))+'\n')
  before=path.read_bytes();title=WorkbenchSessions(rt).title(row)
  self.assertNotIn('private.example',title);self.assertNotIn('synthetic-secret',title);self.assertIn('链接已隐藏',title);self.assertEqual(before,path.read_bytes())
 def test_list_fields_are_redacted_without_mutating_input(self):
  row={key:'https://private.example.invalid/SYNTHETIC' for key in ('title','workflow','node_name','task_summary','note')}
  row['identity']='keep'; result=_list_projection(row)
  for key in ('title','workflow','node_name','task_summary','note'):
   self.assertNotIn('private.example',result[key]);self.assertIn('链接已隐藏',result[key]);self.assertIn('private.example',row[key])
  self.assertEqual(result['identity'],'keep')
