"""A native CLI failure must carry a user-facing next step, not only a category."""
import unittest
from orchestration.adapters import failure_message, FAILURE_HINTS


class CliFailureTextTests(unittest.TestCase):
 def test_the_recorded_authentication_failure_now_explains_itself(self):
  # The exact string recorded in the issue: "CLI authentication; exit=1".
  text=failure_message('authentication',1)
  self.assertTrue(text.startswith('CLI authentication; exit=1'))
  self.assertIn('认证未通过',text)
  self.assertIn('核对登录',text)

 def test_detail_is_preserved_after_the_raw_text(self):
  text=failure_message('authentication',1,'HTTP 401 unauthorized')
  self.assertIn('HTTP 401 unauthorized',text)
  self.assertLess(text.index('HTTP 401'),text.index('认证未通过'))

 def test_every_category_has_a_hint_and_none_promises_a_retry(self):
  for category in ('authentication','timeout','output-budget','cancelled','process-failure','configuration'):
   with self.subTest(category=category):
    self.assertIn(category,FAILURE_HINTS)
    text=failure_message(category,1)
    self.assertGreater(len(text),len('CLI '+category+'; exit=1')+4)
    self.assertNotIn('自动重发本次',text)

 def test_unknown_category_stays_raw_without_inventing_a_hint(self):
  self.assertEqual(failure_message('unheard-of',2),'CLI unheard-of; exit=2')


if __name__=='__main__':unittest.main()
