"""Synthetic task-card revision and preservation."""
import tempfile,unittest
from studio.usable_store import Storage
from studio.usable_graph import template

class TaskCardTests(unittest.TestCase):
 def test_task_card_is_immutable_and_part_of_revision(self):
  from studio.usable_store import Storage
  from studio.usable_graph import template
  with tempfile.TemporaryDirectory() as d:
   store=Storage(d);graph=template('blank');task=dict(title='synthetic',body='goal',scope=['src'],acceptance='test',issue_url='')
   first=store.save_draft(graph,task=task);task['body']='new goal'
   second=store.save_draft(graph,first['id'],first['revision'],task=task)
   self.assertNotEqual(first['revision'],second['revision']);self.assertEqual(first['task']['body'],'goal')
   self.assertEqual(store.get('draft',first['id'])['task']['body'],'new goal')
   legacy=store.save_draft(graph,second['id'],second['revision']);self.assertEqual(legacy['task'],second['task'])
   with self.assertRaises(Exception):store.save_draft(graph,task=dict(task,authorization=True))
