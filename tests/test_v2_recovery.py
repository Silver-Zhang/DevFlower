"""Deterministic local process/receipt controls; zero native model inference."""
import copy
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import time
import unittest
import uuid
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from orchestration.common import Blocked,canonical,digest,save
from orchestration.store import process_identity
from studio.workbench_scheduler import Scheduler
from v2.recovery import observe
from v2.runtime import persist_completion
import test_v2_execution as fixtures


class RecoveryTests(unittest.TestCase):
    setUp=fixtures.ExecutionTests.setUp

    def child(self):
        child=subprocess.Popen([sys.executable,'-u','-c','import sys;print("READY",flush=True);sys.stdin.read()'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,start_new_session=True)
        self.assertEqual(child.stdout.readline(),b'READY\n');record=process_identity(child.pid);self.assertIsNotNone(record)
        def close():
            if child.poll() is None:child.kill()
            child.wait(timeout=3);child.stdin.close();child.stdout.close()
        self.addCleanup(close);return child,record

    def run_fixture(self,dead=True):
        with patch.object(self.rt,'spawn'):
            run=self.rt.launch(self.workspace,self.draft['id'],self.draft['revision'],'recovery-'+uuid.uuid4().hex)['run']
        worker,record=self.child()
        if dead:worker.kill();worker.wait(timeout=3)
        self.store.db.change('run',run['id'],dict(state='RUNNING',worker=record))
        scheduler=Scheduler(self.rt,run['id']);definition=self.store.db.get('definition',run['definition_id'])
        node=next(n for n in definition['graph']['nodes'] if n['kind']=='agent')
        activation=scheduler.activation(node,'',0,())
        return run,activation,worker

    def sent(self,run,activation,*,process=None,completed=False,failed=False,commit=False):
        if process is None:
            child,process=self.child()
            if completed or failed:child.kill();child.wait(timeout=3)
        operation=uuid.uuid4().hex;sid=str(uuid.uuid4());text='synthetic visible request'
        proc=dict(process=process,operation_id=operation,exit=1 if failed else 0,timeout=False,cancelled=False,overflow=False)
        value=dict(state='RUNNING',process=process,process_result=proc,request_id=uuid.uuid4().hex,
            actual_request=text,actual_request_sha256=digest(text.encode()),input_sent_sha256=digest(text.encode()),
            input_sent_operation_id=operation,observed_session=sid,executed_image_sha256='a'*64,effect_uncertain=True,
            native_terminal_events=[dict(type='turn.failed' if failed else 'turn.completed',session_id=sid,native_turn_id=None)] if (completed or failed) else [])
        self.store.db.put('native-step',operation,dict(id=operation,run=run['id'],activation=activation['id'],kind='PROCESS',status='EXITED' if completed or failed else 'RUNNING',data=proc))
        receipt=dict(session_id=sid,native_turn_id='unknown',requested_model='fixture',requested_effort='low',resolved_model='unknown',resolved_effort='unknown',process=proc)
        if failed:value.update(state='FAILED',effect_uncertain=False,terminal_failure_verified=True,failure_receipt=dict(receipt,terminal='turn.failed'))
        if completed:
            output=dict(answer='synthetic completion',summary='',result={},evidence=[],issues=[],next='',artifacts=[])
            completion=dict(run=run['id'],activation=activation['id'],request_id=value['request_id'],request_sha256=value['actual_request_sha256'],receipt=receipt,output=output)
            path=self.store.state/'flow-artifacts'/run['id']/activation['id'];path.mkdir(parents=True)
            persist_completion(path,completion,self.store.state)
            if commit:value.update(state='COMPLETED',effect_uncertain=False,receipt_verified=True,receipt=receipt,completion_sha256=digest(canonical(completion)),output=output)
        self.store.db.change('activation',activation['id'],value)
        return self.store.db.get('activation',activation['id'])

    def test_full_six_field_worker_identity_and_unknown_foreign_process(self):
        child,identity=self.child();self.assertEqual(observe(identity),'alive')
        for field in ('host','pid','start','pgid','sid'):
            bad=dict(identity);bad[field]='other' if field in ('host','start') else identity[field]+1
            self.assertEqual(observe(bad),'unknown' if field!='pid' else 'dead')
        bad=dict(identity,boot_id='prior-boot');self.assertEqual(observe(bad),'dead')
        child.kill();child.wait(timeout=3);self.assertEqual(observe(identity),'dead')

    def test_cancel_unknown_activation_never_becomes_cancelled_run(self):
        run,a,worker=self.run_fixture();self.sent(run,a)
        self.store.db.change('run',run['id'],dict(cancel_requested=True))
        self.store.db.change('activation',a['id'],dict(state='RECOVERY_REQUIRED'))
        snapshot=self.rt.snapshot(self.workspace,run['id']);listed=self.rt.runs(self.workspace)[0]
        self.assertEqual(snapshot['state'],'RECOVERY_REQUIRED');self.assertEqual(snapshot['run']['provider_outcome'],'unknown')
        self.assertEqual(listed['state'],snapshot['state']);self.assertEqual(snapshot['nodes'][0]['state'],'RECOVERY_REQUIRED')

    def test_scheduler_cancel_exception_preserves_effect_uncertainty(self):
        run,a,_=self.run_fixture(dead=False);scheduler=Scheduler(self.rt,run['id'])
        def interrupted(*args):
            self.store.db.change('run',run['id'],dict(cancel_requested=True))
            self.store.db.change('activation',a['id'],dict(state='RECOVERY_REQUIRED',effect_uncertain=True))
            raise Blocked('synthetic cancellation after possible send')
        with patch.object(scheduler,'graph',side_effect=interrupted):scheduler.run_graph()
        self.assertEqual(self.store.db.get('run',run['id'])['state'],'RECOVERY_REQUIRED')

    def test_cancel_before_send_known_failure_and_completed_controls(self):
        run,a,worker=self.run_fixture(dead=False);value=self.rt.cancel(self.workspace,run['id'])
        self.assertEqual(value['state'],'CANCEL_REQUESTED');worker.kill();worker.wait(timeout=3)
        value=self.rt.snapshot(self.workspace,run['id'])
        self.assertEqual(value['state'],'CANCELLED');self.assertEqual(value['provider_outcome'],'not_sent')
        run,a,_=self.run_fixture();self.sent(run,a,failed=True)
        self.store.db.change('run',run['id'],dict(state='FAILED',cancel_requested=True))
        self.assertEqual(self.rt.snapshot(self.workspace,run['id'])['provider_outcome'],'failed')
        self.assertEqual(self.rt.snapshot(self.workspace,run['id'])['state'],'FAILED')
        run,a,_=self.run_fixture();self.sent(run,a,completed=True,commit=True)
        self.store.db.change('run',run['id'],dict(state='CANCELLED',cancel_requested=True))
        self.assertEqual(self.rt.snapshot(self.workspace,run['id'])['state'],'COMPLETED')

    def test_completion_file_recovers_crash_before_activation_commit_without_scheduler(self):
        run,a,_=self.run_fixture();self.sent(run,a,completed=True,commit=False)
        with patch.object(self.rt,'spawn') as spawn,patch.object(Scheduler,'run_graph') as graph:
            snapshot=self.rt.snapshot(self.workspace,run['id']);listed=self.rt.runs(self.workspace)[0]
        spawn.assert_not_called();graph.assert_not_called()
        self.assertEqual(snapshot['state'],'COMPLETED');self.assertEqual(snapshot['nodes'][0]['output']['answer'],'synthetic completion')
        self.assertEqual(listed['state'],'COMPLETED');self.assertEqual(self.store.db.get('activation',a['id'])['state'],'RUNNING','Recovery is derived; original journal is preserved')

    def test_verified_failure_before_state_commit_is_failed_even_with_cancel_requested(self):
        for cancelled in (False,True):
            with self.subTest(cancelled=cancelled):
                run,a,_=self.run_fixture();self.sent(run,a,failed=True)
                self.store.db.change('activation',a['id'],dict(state='RUNNING'))
                self.store.db.change('run',run['id'],dict(cancel_requested=cancelled))
                result=self.rt.snapshot(self.workspace,run['id']);node=result['nodes'][0]
                self.assertEqual(result['state'],'CANCELLED' if cancelled else 'FAILED')
                self.assertEqual(result['provider_outcome'],'failed');self.assertEqual(node['state'],'FAILED')
                self.assertTrue(node['terminal_failure_verified']);self.assertTrue(node['input_sent_sha256'])
                self.assertIn('失败',node['reason']);self.assertNotIn('没有请求发送证据',node['reason'])
                self.assertEqual(next(r for r in self.rt.runs(self.workspace) if r['id']==run['id'])['state'],result['state'])
                # Missing, malformed or conflicting terminal evidence never
                # becomes a definite failure or an HTTP handler exception.
                original=self.store.db.get('activation',a['id'])
                for changes in (dict(native_terminal_events=[]),dict(native_terminal_events=[None]),dict(process_result=None),dict(failure_receipt={'process':[]})):
                    self.store.db.put('activation',a['id'],dict(original,**changes))
                    self.assertEqual(self.rt.snapshot(self.workspace,run['id'])['state'],'RECOVERY_REQUIRED')

    def test_terminal_failure_with_exact_process_still_alive_is_unknown_and_cancellable(self):
        run,a,_=self.run_fixture();native,identity=self.child();self.sent(run,a,failed=True,process=identity)
        self.store.db.change('run',run['id'],dict(state='FAILED'))
        result=self.rt.snapshot(self.workspace,run['id']);self.assertEqual(result['state'],'RECOVERY_REQUIRED')
        self.assertFalse(result['nodes'][0]['terminal_failure_verified'])
        # The contradictory receipt never prevents local ownership-checked stop.
        self.rt.cancel(self.workspace,run['id']);native.wait(timeout=3)

    def test_missing_corrupt_mismatched_and_symlink_completion_remain_unknown(self):
        for mode in ('missing','corrupt','session','process','hash','symlink','alive'):
            with self.subTest(mode=mode):
                run,a,_=self.run_fixture();self.sent(run,a,completed=True,commit=True,process=self.child()[1] if mode=='alive' else None)
                path=self.store.state/'flow-artifacts'/run['id']/a['id']/'completion.json'
                if mode=='alive':pass
                elif mode=='missing':path.unlink()
                elif mode=='corrupt':path.write_text('{broken')
                elif mode=='symlink':
                    target=path.with_name('original.json');path.rename(target);path.symlink_to(target)
                else:
                    value=json.loads(path.read_text())
                    if mode=='session':value['receipt']['session_id']='wrong-session'
                    elif mode=='process':value['receipt']['process']['operation_id']='wrong-operation'
                    else:value['output']['answer']='different output'
                    save(path,value)
                self.store.db.change('run',run['id'],dict(state='COMPLETED'))
                self.assertEqual(self.rt.snapshot(self.workspace,run['id'])['state'],'RECOVERY_REQUIRED')

    def test_crash_window_requires_independent_digest_proof_and_detects_valid_json_mutation(self):
        for mode in ('missing-proof','changed-output','wrong-proof','proof-symlink','proof-oversize','completion-oversize'):
            with self.subTest(mode=mode):
                run,a,_=self.run_fixture();self.sent(run,a,completed=True,commit=False)
                folder=self.store.state/'flow-artifacts'/run['id']/a['id']
                if mode=='missing-proof':(folder/'completion-proof.json').unlink()
                elif mode=='wrong-proof':save(folder/'completion-proof.json',dict(version=1,run='other'))
                elif mode=='proof-symlink':
                    proof=folder/'completion-proof.json';proof.rename(folder/'moved-proof.json');proof.symlink_to(folder/'moved-proof.json')
                elif mode=='proof-oversize':(folder/'completion-proof.json').write_text(' '*1025)
                elif mode=='completion-oversize':(folder/'completion.json').write_text(' '*2_000_001)
                else:
                    value=json.loads((folder/'completion.json').read_text());value['output']['answer']='synthetic altered output';save(folder/'completion.json',value)
                self.assertEqual(self.rt.snapshot(self.workspace,run['id'])['state'],'RECOVERY_REQUIRED')

    def test_cancel_dead_worker_stops_only_associated_owned_group(self):
        run,a,_=self.run_fixture();native,identity=self.child();unrelated,_=self.child();self.sent(run,a,process=identity)
        self.assertIsNone(native.poll());value=self.rt.cancel(self.workspace,run['id']);native.wait(timeout=3)
        self.assertIsNone(unrelated.poll());self.assertEqual(value['state'],'RECOVERY_REQUIRED')
        self.assertTrue(self.store.db.get('run',run['id'])['cancel_requested'])
        self.assertTrue(self.store.db.get('activation',a['id'])['input_sent_sha256'])

    def test_cancel_mismatched_identity_or_operation_never_signals_unrelated_process(self):
        for field in ('host','boot_id','start','pgid','sid','operation'):
            with self.subTest(field=field):
                run,a,_=self.run_fixture();native,identity=self.child();bad=dict(identity)
                if field in ('host','boot_id','start'):bad[field]='other'
                elif field!='operation':bad[field]+=1
                self.sent(run,a,process=bad)
                if field=='operation':self.store.db.change('activation',a['id'],dict(input_sent_operation_id='wrong-operation'))
                with patch('orchestration.process.stop',wraps=__import__('orchestration.process',fromlist=['stop']).stop) as stop:
                    value=self.rt.cancel(self.workspace,run['id'])
                stop.assert_not_called();self.assertIsNone(native.poll());self.assertEqual(value['state'],'RECOVERY_REQUIRED')

    def test_completed_expected_nodes_do_not_hide_extra_unknown_process_or_activation(self):
        for extra in ('process','activation','notice'):
            with self.subTest(extra=extra):
                run,a,_=self.run_fixture();self.sent(run,a,completed=True,commit=True)
                if extra=='process':
                    self.store.db.put('native-step','unrelated-'+run['id'],dict(id='unrelated-'+run['id'],run=run['id'],activation='missing',kind='PROCESS',status='RUNNING',data=dict(process=self.child()[1])))
                elif extra=='activation':
                    other=copy.deepcopy(self.store.db.get('activation',a['id']));other.update(id=uuid.uuid4().hex,state='RECOVERY_REQUIRED',receipt_verified=False)
                    self.store.db.put('activation',other['id'],other)
                else:
                    from studio.workbench_failures import _persist_recovery_notice
                    _persist_recovery_notice(self.rt,run['id'],'SyntheticFailure')
                self.assertEqual(self.rt.snapshot(self.workspace,run['id'])['state'],'RECOVERY_REQUIRED')

    def test_completion_publication_uses_synced_bytes_atomic_rename_and_synced_directory(self):
        folder=self.store.state/'flow-artifacts'/'publication';folder.mkdir(parents=True)
        value=dict(run='synthetic',activation='synthetic',request_sha256='a'*64,output={})
        events=[];sync=os.fsync;replace=os.replace
        def fsync(fd):
            events.append('directory-sync' if stat.S_ISDIR(os.fstat(fd).st_mode) else 'file-sync');return sync(fd)
        def rename(source,target):
            result=replace(source,target);events.append(Path(target).name);return result
        with patch('os.fsync',side_effect=fsync),patch('os.replace',side_effect=rename):persist_completion(folder,value,self.store.state)
        first=events.index('completion.json');second=events.index('completion-proof.json')
        self.assertIn('file-sync',events[:first]);self.assertIn('directory-sync',events[first+1:second]);self.assertIn('file-sync',events[first+1:second]);self.assertIn('directory-sync',events[second+1:])
        self.assertEqual(json.loads((folder/'completion-proof.json').read_text())['completion_sha256'],digest(canonical(value)))
        self.assertFalse(list(folder.glob('*.tmp')))

if __name__=='__main__':unittest.main()
