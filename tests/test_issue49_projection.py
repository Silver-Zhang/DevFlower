"""Synthetic delegated task projection; no real native session."""
import unittest
from test_workbench_runtime import fixture,structured_session
from studio.workbench_sessions import WorkbenchSessions

class DelegatedProjectionTests(unittest.TestCase):
 def test_delegated_child_displays_its_task_and_preserves_parent(self):
  from studio.conversation_projection import conversation_turns
  base,rt=fixture(self);path,row=structured_session(base,rt,'delegated-projection','codex');manager=WorkbenchSessions(rt);w=base.project['id'];native=rt.db.get('session-handle',row['handle'])
  a=rt.flow.get('activation','delegated-projection-turn-1');rt.flow.change('activation',a['id'],dict(parent_activation='synthetic-parent',request_sections=[dict(title='每轮任务',text='SYNTHETIC CHILD ONLY')]))
  turns=conversation_turns(manager,w,native,manager.related_activations(w,native),[],[])
  child=next(t for t in turns if t['id']==a['id']);self.assertTrue(child['delegated']);self.assertEqual(child['user_text'],'SYNTHETIC CHILD ONLY');self.assertNotEqual(rt.flow.get('run',a['run'])['task'],child['user_text'])
