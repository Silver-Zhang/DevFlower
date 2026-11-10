import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from orchestration.common import Blocked
from v2.claude_native_catalog import model_id, project_catalog, receive_reply


class ClaudeNativeCatalogTests(unittest.TestCase):
    def test_projects_native_alias_default_and_effort_without_account_fields(self):
        response = {
            'models': [
                {'value': 'default', 'resolvedModel': 'fixture-provider[1m]',
                 'supportedEffortLevels': ['low', 'xhigh', 'unknown']},
                {'value': 'fixture-provider[1m]', 'resolvedModel': 'fixture-provider[1m]',
                 'displayName': 'private fixture-secret', 'supportedEffortLevels': ['high']},
                {'value': 'fixture-provider[1m]'},
                {'value': 'fixture-secret'},
                {'value': 'https://private.example'},
            ],
            'account': {'email': 'private account data'},
        }
        result = project_catalog(response, ['low', 'high', 'xhigh'], {'fixture-secret'})
        self.assertEqual(result, {
            'menu_default_model': 'fixture-provider[1m]',
            'models': [
                {'id': 'default', 'name': 'default', 'resolved_model': 'fixture-provider[1m]',
                 'efforts': ['low', 'xhigh']},
                {'id': 'fixture-provider[1m]', 'name': 'fixture-provider[1m]',
                 'resolved_model': 'fixture-provider[1m]', 'efforts': ['high']},
            ],
        })
        self.assertIsNone(model_id('provider[unverified-suffix]'))

    def test_waits_for_matching_control_reply_and_refuses_over_limit(self):
        messages = [
            {'type': 'system', 'secret': 'unprojected'},
            {'type': 'control_response', 'response': {'request_id': 'different', 'subtype': 'success'}},
            {'type': 'control_response', 'response': {'request_id': 'match', 'subtype': 'success',
                                                     'response': {'models': []}}},
        ]
        payload = b''.join(json.dumps(message).encode() + b'\n' for message in messages)
        for limit, valid in ((1000, True), (20, False)):
            read, write = os.pipe()
            with os.fdopen(write, 'wb') as stream:
                stream.write(payload)
            with os.fdopen(read, 'rb') as stream, patch('v2.claude_native_catalog.LIMIT', limit):
                process = SimpleNamespace(stdout=stream)
                if valid:
                    self.assertEqual(receive_reply(process, 'match'), {'models': []})
                else:
                    with self.assertRaises(Blocked):
                        receive_reply(process, 'match')
