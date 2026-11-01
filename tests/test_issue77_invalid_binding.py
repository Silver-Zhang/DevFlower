import unittest
from unittest.mock import patch
from studio.local_codex_adapter import LocalCodex
from orchestration.common import Blocked

class InvalidBindingTests(unittest.TestCase):
 def test_missing_executable_fails_closed_without_process_or_credentials(self):
  for role,shared in [('reviewer',False),('reviewer',True),('implementer',False)]:
   for value in (None,'', '  ', 17):
    with self.subTest(role=role,shared=shared,value=value):
     binding=dict(execution_backend='native-local',native_interaction='ordinary',role=role,execution_mode='Ask',shared_conversation=shared,executable=value)
     with patch('studio.local_codex_adapter.CodexRPC') as rpc,patch('studio.local_codex_adapter.credentials') as credentials,patch('studio.native_codex_image.NativeImage') as image:
      with self.assertRaisesRegex(Blocked,'绑定不完整'):LocalCodex(binding,{}).start('synthetic','/tmp','/tmp')
      rpc.assert_not_called();credentials.assert_not_called();image.assert_not_called()
