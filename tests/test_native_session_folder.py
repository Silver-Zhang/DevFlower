"""Standalone native chat must use the same source lease as general workflows."""
from pathlib import Path
import sys
import time
import secrets
import unittest
from unittest.mock import patch
sys.path[:0] = [str(Path(__file__).parent), str(Path(__file__).resolve().parents[1] / 'tools')]
from test_workbench_runtime import fixture
from studio.workbench_scheduler import source_lease


class NativeFolderTests(unittest.TestCase):
    def test_selected_non_git_folder_can_finish_one_native_turn(self):
        self.run_turn(False)

    def test_existing_folder_writer_prevents_native_call(self):
        self.run_turn(True)

    def run_turn(self, locked):
        base, rt = fixture(self)
        folder = base.source.parent / 'native-nongit'
        folder.mkdir()
        workspace = rt.open_workspace(str(folder))
        permit = rt.session_center.prepare(dict(workspace=workspace['id'], handle='new',
            profile='default-claude', prompt='SYNTHETIC turn; no real Provider',
            permission='read', auto_verify=True))
        # Persist an actual consumed unit-fixture operation without a child.
        value, receipt = rt.db.consume(permit['id'], permit['sha256'], ['Synthetic unit harness'])
        row = dict(value, id=secrets.token_hex(16), state='QUEUED', created_at=time.time(), cancel_requested=False, authorization=receipt)
        rt.db.put('session-operation', row['id'], row)
        reply = dict(report=dict(reply='SYNTHETIC response'), session_id='00000000-0000-4000-8000-000000000001')
        with patch('studio.session_center.CLI.start', return_value=reply) as native, \
             patch('studio.model_admission.ModelAdmission.require', return_value={'evidence':'SYNTHETIC certificate'}):
            if locked:
                with source_lease(rt.source_lock(str(folder)), True):
                    rt.session_center.execute(row['id'])
                native.assert_not_called()
                self.assertEqual(rt.session_center.operation(row['id'], workspace['id'])['state'], 'FAILED')
            else:
                rt.session_center.execute(row['id'])
                native.assert_called_once()
                self.assertEqual(rt.session_center.operation(row['id'], workspace['id'])['state'], 'COMPLETED')


if __name__ == '__main__': unittest.main()
