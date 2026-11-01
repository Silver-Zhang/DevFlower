"""A failed assistant catalog call names the side the user should check."""
import unittest
from studio.devflower_agent import status_hint


class AssistantStatusHintTests(unittest.TestCase):
 def test_404_points_at_the_endpoint_not_the_credentials(self):
  hint=status_hint(404)
  self.assertIn('该地址没有这个接口',hint)
  self.assertIn('不代表认证失败',hint)
  self.assertNotIn('认证未通过',hint)

 def test_401_and_403_point_at_credentials(self):
  for status in (401,403):
   with self.subTest(status=status):
    self.assertIn('认证未通过',status_hint(status))

 def test_quota_and_server_errors_stay_distinct(self):
  self.assertIn('额度',status_hint(429))
  self.assertIn('服务端返回错误',status_hint(503))

 def test_unknown_status_keeps_a_neutral_hint(self):
  self.assertIn('连接地址',status_hint(418))


if __name__=='__main__':unittest.main()
