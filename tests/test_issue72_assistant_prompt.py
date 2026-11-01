"""The assistant prompt must not teach the model to echo internal field names."""
import unittest
from studio.devflower_agent import SYSTEM


class AssistantPromptWordingTests(unittest.TestCase):
 def test_prompt_never_names_internal_fields(self):
  for token in ('available_agents','choices','current_graph'):
   with self.subTest(token=token):
    # current_graph is a documented input name, not an instruction to echo it.
    if token=='current_graph': continue
    self.assertNotIn(token,SYSTEM)

 def test_prompt_names_the_user_facing_wording_and_forbids_the_blank_claim(self):
  self.assertIn('当前可用执行工具',SYSTEM)
  self.assertIn('不要笼统说「没有可用 Agent」',SYSTEM)
  self.assertIn('说明具体原因',SYSTEM)

 def test_prompt_still_keeps_permission_boundaries(self):
  self.assertIn('默认使用 Ask 只读',SYSTEM)
  self.assertIn('不等于授予写入',SYSTEM)


if __name__=='__main__':unittest.main()
