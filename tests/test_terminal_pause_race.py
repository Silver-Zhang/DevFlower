import unittest
from unittest.mock import patch
from studio.server import Conflict
from test_workbench_runtime import fixture

class TerminalPauseRaceTests(unittest.TestCase):
 def test_terminal_pause_race_does_not_mutate_or_log(self):
  base,rt=fixture(self);rt.flow.put('run','run',dict(id='run',state='RUNNING',paused=False))
  original=rt.flow.get
  def read(kind,key):
   row=original(kind,key)
   if kind=='run':rt.flow.change('run','run',{'state':'FAILED'})
   return row
  with patch.object(rt.flow,'get',side_effect=read),self.assertRaises(Conflict):rt.control('run','pause')
  self.assertFalse(original('run','run')['paused']);self.assertEqual(rt.flow.events('run'),[])
