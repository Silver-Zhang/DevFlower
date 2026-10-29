import test from 'node:test';
import assert from 'node:assert/strict';
import {projectRun,edgeEvidence,latestActivation,preflight,statusInfo,duration,stateDuration} from '../tools/studio/workbench_ui/runtime_projection.mjs';
const snapshot={run:{id:'A',graph:{nodes:[{id:'a',name:'分析'},{id:'b',name:'汇合'}]}},state:'RUNNING',nodes:[{run:'A',node:'a',state:'COMPLETED'},{run:'A',node:'b',state:'PENDING',received:1,expected:2,waiting:['c']}]};
test('offline hides passed, running, progress and waits instead of retaining success',()=>{const p=projectRun(snapshot,'unknown','A');assert.equal(p.state,'UNKNOWN');assert.equal(p.done,0);assert.deepEqual(p.active,[]);assert.deepEqual(p.waiting,[]);assert(!p.trusted)});
test('late run A cannot project onto B',()=>{const p=projectRun(snapshot,'connected','B');assert.equal(p.graph,null);assert.equal(p.state,'UNKNOWN')});
test('persisted merge wait and completion count remain truthful',()=>{const p=projectRun(snapshot,'connected','A');assert.equal(p.done,1);assert.equal(p.total,2);assert.equal(p.waiting[0].activation.received,1);assert.deepEqual(p.waiting[0].activation.waiting,['c'])});
test('paused run retains actual in-flight activations without scheduling claims',()=>{const p=projectRun({...snapshot,run:{...snapshot.run,paused:true},nodes:[{node:'a',state:'RUNNING'}]},'connected','A');assert.equal(p.state,'PAUSED');assert.equal(p.active.length,1)});
test('recovery state never projects normal running',()=>assert.equal(projectRun({...snapshot,state:'RECOVERY_REQUIRED'},'connected','A').trusted,false));
test('terminal nodes cannot retain stale upstream wait reasons',()=>{for(const state of ['COMPLETED','FAILED','SKIPPED','CANCELLED']){const p=projectRun({...snapshot,nodes:[{node:'b',state,waiting:['a'],received:0,expected:1}]},'connected','A');assert.equal(p.waiting.length,0,state)}});
test('newest nested iteration wins over late old round completion',()=>assert.equal(latestActivation([{node:'a',iteration_path:[1,5]},{node:'a',iteration_path:[2,1]},{node:'a',iteration_path:[1,5],started_at:999}],'a').iteration_path[0],2));
test('edge evidence binds run, edge, source and target; ready is not received',()=>{const e={id:'e',source:'a',target:'b'},base={edge:'e',run:'A',source:'loop/a',node:'loop/b',kind:'handoff-sent'};const events=[base,{...base,run:'B'},{...base,node:'other'},{...base,source:'other'},{...base,kind:'handoff-ready'}];assert.equal(edgeEvidence(events,e,'loop/','A'),base)});
test('all nine states have readable labels and noncolour marks',()=>{for(const state of ['PENDING','RUNNING','WAITING_INPUT','WAITING_CONFIRMATION','COMPLETED','FAILED','SKIPPED','CANCELLED','UNKNOWN']){const [label,tone,symbol]=statusInfo(state);assert(label&&!/^[A-Z_]+$/.test(label));assert(tone&&symbol)}});
test('preflight locates missing nested configuration without mutation',()=>{const graph={nodes:[{id:'loop',body:{nodes:[{id:'agent',kind:'agent',name:'复核'}]}}]};const before=JSON.stringify(graph);assert.equal(preflight(graph,{},'真实任务').node,'loop/agent');assert.equal(JSON.stringify(graph),before)});
test('preflight never pretends to replace backend graph and permission validation',()=>{const g={nodes:[{id:'a',kind:'agent',profile:'p',model:'m',effort:'high',round_prompt:'只读'}]};assert.equal(preflight(g,{},'真实任务'),null);assert.match(preflight(g,null,'任务').message,/打开/);assert.match(preflight(g,{},'').message,/填写/)});
test('elapsed uses actual timestamps and clamps invalid/future start',()=>{assert.equal(duration(10,75),'1 分 5 秒');assert.equal(duration(90,75),'0 秒');assert.equal(duration(undefined),'耗时未知')});

test('terminal and unknown durations require an end timestamp',()=>{const old=Date.now;try{for(const now of [100000,200000]){Date.now=()=>now;for(const state of ['COMPLETED','FAILED','CANCELLED','SKIPPED','UNKNOWN','RECOVERY_REQUIRED','UNRECOGNIZED'])assert.equal(stateDuration(1,undefined,state),'耗时未知',state);assert.equal(stateDuration(10,75,'COMPLETED'),'1 分 5 秒');assert.equal(stateDuration(90,75,'FAILED'),'0 秒');}Date.now=()=>100000;assert.equal(stateDuration(1,undefined,'RUNNING'),'1 分 39 秒');assert.equal(stateDuration(1,undefined,'PAUSED'),'1 分 39 秒');}finally{Date.now=old}});

test('terminal run wins over a persisted pause without rewriting evidence',()=>{
 for(const state of ['COMPLETED','FAILED','CANCELLED']){
  const v={...snapshot,state,run:{...snapshot.run,paused:true,finished_at:75}};
  const before=JSON.stringify(v),p=projectRun(v,'connected','A');
  assert.equal(p.state,state);assert.deepEqual(p.active,[]);assert.deepEqual(p.waiting,[]);
  assert.equal(stateDuration(10,v.run.finished_at,p.state),'1 分 5 秒');
  assert.equal(JSON.stringify(v),before);
 }
});
test('terminal run does not advertise stale nodes as active or waiting',()=>{
 for(const state of ['COMPLETED','FAILED','CANCELLED'])for(const recorded of ['STARTING','RUNNING','PENDING','WAITING_CONFIRMATION']){
  const v={...snapshot,state,nodes:[{run:'A',node:'a',state:recorded,started_at:10,waiting:['b']},{run:'A',node:'b',state:'COMPLETED'}]};
  const before=JSON.stringify(v),p=projectRun(v,'connected','A'),a=p.latest[0].activation;
  assert.equal(a.state,'UNKNOWN');assert.equal(a.recorded_state,recorded);
  assert.match(a.reason,/尚未确认/);assert.deepEqual(p.active,[]);assert.deepEqual(p.waiting,[]);
  assert.equal(p.done,1);assert.equal(p.latest[1].activation.state,'COMPLETED');
  assert.equal(stateDuration(a.started_at,undefined,a.state),'耗时未知');
  assert.equal(JSON.stringify(v),before);
 }
});
