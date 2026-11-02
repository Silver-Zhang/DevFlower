import test from 'node:test';
import assert from 'node:assert/strict';
import {projectRun,failureDiagnostic} from '../tools/studio/workbench_ui/runtime_projection.mjs';

const graph={version:3,name:'失败流程',nodes:[
  {id:'start',kind:'start',name:'输入'},
  {id:'agent',kind:'agent',name:'处理任务'},
  {id:'result',kind:'result',name:'结束'}],
  edges:[{source:'start',target:'agent'},{source:'agent',target:'result'}]};
const run={id:'r1',workspace:'w',state:'FAILED',task:'检查登录页',graph};

function snapshot(nodes){
  return {run,state:'FAILED',nodes,events:[],worker_alive:false,recovery_pending:false};
}

test('failed run diagnostic cites the real node and reason',()=>{
  const snap=snapshot([
    {id:'a0',run:'r1',node:'start',state:'COMPLETED',iteration:1,iteration_path:[1]},
    {id:'a1',run:'r1',node:'agent',state:'FAILED',reason:'模型接口返回 500',iteration:1,iteration_path:[1]},
    {id:'a2',run:'r1',node:'result',state:'SKIPPED',iteration:1,iteration_path:[1]}]);
  const value=failureDiagnostic(snap,projectRun(snap,'connected','r1'));
  assert.ok(value,'diagnostic expected');
  assert.match(value.text,/处理任务/);
  assert.match(value.text,/模型接口返回 500/);
  assert.match(value.text,/已跳过 1 个节点/);
  assert.match(value.text,/不要修改本次已冻结的运行/);
  assert.deepEqual(value.failed,['agent']);
});

test('completed or untrusted runs produce no diagnostic',()=>{
  const done=snapshot([]); done.state='COMPLETED';
  assert.equal(failureDiagnostic(done,projectRun(done,'connected','r1')),null);
  const failed=snapshot([]);
  assert.equal(failureDiagnostic(failed,projectRun(failed,'connecting','r1')),null);
});

test('a failed run without a recorded node still explains the limit',()=>{
  const snap=snapshot([]);
  const value=failureDiagnostic(snap,projectRun(snap,'connected','r1'));
  assert.match(value.text,/未记录到具体的失败节点与原因/);
  assert.match(value.text,/不要修改本次已冻结的运行/);
});
