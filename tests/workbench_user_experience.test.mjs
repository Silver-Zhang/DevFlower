import test from 'node:test';import assert from 'node:assert/strict';
import {checkConnections} from '../tools/studio/workbench_ui/start_checks.mjs';
import {handoffDeliveries} from '../tools/studio/workbench_ui/handoff_deliveries.mjs';
import {usageSummary} from '../tools/studio/workbench_ui/usage_summary.mjs';
const permit={data:{admission_checks:[{key:'exact',selection:{profile:'p',model:'m',effort:'high',mode:'Ask'}}]}};
test('no explicit confirmation means no model calls',async()=>{let n=0;await assert.rejects(checkConnections(()=>n++,permit,''));assert.equal(n,0)});
test('valid scoped check avoids an extra diagnostic',async()=>{let calls=[];await checkConnections(async p=>{calls.push(p);return{execution_allowed:true}},permit,'Actual click');assert.deepEqual(calls,['/api/model-admission/status'])});
test('one frozen scope produces one diagnostic; actual confirmation is retained',async()=>{let calls=[];const doubled={data:{admission_checks:[...permit.data.admission_checks,...permit.data.admission_checks]}};await checkConnections(async(p,d)=>{calls.push([p,d]);return p.endsWith('/status')?{execution_allowed:false}:p.endsWith('/prepare')?{id:'id',sha256:'sha'}:{execution_allowed:true}},doubled,'Actual click');assert.equal(calls.length,3);assert.equal(calls[2][1].answer,'Actual click');assert.equal(calls[2][1].reuse_verified,true)});
test('failed or uncertain diagnostic never gets silently retried',async()=>{let n=0;await assert.rejects(checkConnections(async p=>{n++;if(p.endsWith('/status'))return{};if(p.endsWith('/prepare'))return{id:'id',sha256:'sha'};throw Error('lost response')},permit,'Actual click'),/lost response/);assert.equal(n,3)});
test('closing while a diagnostic is running cannot start the business operation',async()=>{let current=true;await assert.rejects(checkConnections(async p=>{if(p.endsWith('/status'))return{};if(p.endsWith('/prepare'))return{};current=false;return{execution_allowed:true}},permit,'Actual click',()=>{},()=>current),/取消/)});
test('diagnostic rejection is not execution permission',async()=>{await assert.rejects(checkConnections(async()=>({execution_allowed:false}),permit,'Actual click'),/未通过/)});
const e=(seq,kind,changes={})=>({seq,kind,run:'run',edge:'edge',activation:'up',node:'down',target_round:[1],attempt:1,content_sha256:'hash',...changes});
test('ready and sent are one readable delivery, late ready cannot downgrade it',()=>{const r=handoffDeliveries([e(1,'handoff-ready'),e(2,'handoff-sent'),e(3,'handoff-ready')],'run');assert.equal(r.length,1);assert.equal(r[0].kind,'handoff-sent')});
test('loops, attempts, activations, and different contents remain distinct',()=>{const rows=[e(1,'handoff-sent'),e(2,'handoff-sent',{target_round:[2]}),e(3,'handoff-sent',{activation:'other'}),e(4,'handoff-sent',{attempt:2}),e(5,'handoff-sent',{content_sha256:'different'})];assert.equal(handoffDeliveries(rows,'run').length,5);assert.equal(handoffDeliveries(rows,'wrong').length,0)});
const n=(id,t,input,output,semantics='turn',sid=id)=>({id,kind:'agent',state:'COMPLETED',provider:'claude',finished_at:t,output:{receipt:{session_id:sid,usage_scope:'dedicated-home:'+sid,usage:{semantics,usage:{input_tokens:input,output_tokens:output}}}}});
test('session cumulative Claude usage is counted once; per turn usage is separate',()=>{const r=usageSummary([n('one',1,10,2,'session-cumulative-do-not-sum','same'),n('two',2,20,4,'session-cumulative-do-not-sum','same'),n('three',3,5,1)]);assert.equal(r.input,25);assert.equal(r.output,5);assert.equal(r.reported,2);assert.equal(r.complete,true)});
test('unknown or malformed usage is not reported as a zero cost success',()=>{const r=usageSummary([n('one',1,10,2),{id:'unknown',kind:'agent',state:'COMPLETED'},n('bad',3,-1,8)]);assert.equal(r.complete,false);assert.equal(r.missing,2);assert.equal(r.input,10)});

const failed={execution_allowed:false,status:'failed',verification_id:'revoked-1'};
const retry={key:'exact',verificationId:'revoked-1'};
test('ordinary confirmation of a failed scope only reads status; no probe or manual intent',async()=>{
 const calls=[];await assert.rejects(checkConnections(async(p,d)=>{calls.push([p,d]);return failed},permit,'Normal confirmation'),e=>{assert.deepEqual(e.retry,retry);return true});
 assert.deepEqual(calls.map(c=>c[0]),['/api/model-admission/status']);
});
test('only a deliberate retry for the exact failed scope sends manual verification',async()=>{
 const other={key:'other',selection:{profile:'other',model:'different'}};
 const calls=[];await checkConnections(async(p,d)=>{calls.push([p,d]);return p.endsWith('/status')?(d.profile==='p'?failed:{execution_allowed:false,status:'unverified'}):p.endsWith('/prepare')?{id:d.profile,sha256:'frozen'}:{execution_allowed:true}}, {data:{admission_checks:[permit.data.admission_checks[0],other]}},'User clicked retry; readonly nonce may cost',()=>{},()=>true,retry);
 const verifies=calls.filter(c=>c[0].endsWith('/verify')).map(c=>c[1]);assert.deepEqual(verifies.map(v=>v.reuse_verified),[false,true]);assert.equal(verifies[0].id,'p');assert.equal(verifies[0].answer,'User clicked retry; readonly nonce may cost');
});
test('retry cannot authorize another key or a newer revocation of the same configuration',async()=>{
 let calls=0;await assert.rejects(checkConnections(async()=>{calls++;return failed},permit,'Actual click',()=>{},()=>true,{key:'other',verificationId:'revoked-1'}),/不一致/);assert.equal(calls,0);
 await assert.rejects(checkConnections(async()=>{calls++;return {...failed,verification_id:'revoked-2'}},permit,'Actual click',()=>{},()=>true,retry),/状态已变化/);assert.equal(calls,1);
});
for(const boundary of ['prepare','verify'])test(`revocation after status at ${boundary} cannot silently become manual verification`,async()=>{
 const calls=[];let revoked=false;
 await assert.rejects(checkConnections(async(p,d)=>{
  calls.push([p,d]);if(p.endsWith('/status'))return revoked?failed:{execution_allowed:false,status:'unverified'};
  if(p.endsWith('/prepare')){if(boundary==='prepare')revoked=true;return{id:'one',sha256:'frozen'}}
  revoked=true;const e=Error('Revoked before STARTING');e.responseStatus=400;throw e;
 },permit,'Ordinary confirmation'),e=>{assert.deepEqual(e.retry,retry);return true});
 assert.equal(calls.filter(c=>c[0].endsWith('/verify')).length,1);assert.equal(calls[2][1].reuse_verified,true);assert.equal(calls.at(-1)[0],'/api/model-admission/status');
});
test('network loss and unknown in-flight status never supply a retry intent or replay',async()=>{
 const calls=[];await assert.rejects(checkConnections(async(p,d)=>{calls.push([p,d]);if(p.endsWith('/status'))return{status:'unverified'};if(p.endsWith('/prepare'))return{id:'once',sha256:'frozen'};throw Error('lost response')},permit,'Confirmed'),e=>{assert.equal(e.retry,undefined);return /lost response/.test(e.message)});assert.equal(calls.length,3);assert.equal(calls[2][1].reuse_verified,true);
 let n=0;await assert.rejects(checkConnections(async()=>{n++;return{status:'unknown'}},permit,'Confirmed'),e=>{assert.equal(e.retry,undefined);return /未确认/.test(e.message)});assert.equal(n,1);
});
for(const boundary of ['status','prepare'])test(`cancelling after ${boundary} prevents a diagnostic and business launch`,async()=>{
 let current=true;const calls=[];await assert.rejects(checkConnections(async p=>{calls.push(p);if(p.endsWith('/'+boundary))current=false;return{}},permit,'Confirmed',()=>{},()=>current),/取消/);assert(!calls.some(p=>p.endsWith('/verify')));
});

// R1 UX07: source identity alone cannot identify a delivery's receiver.
test('two sent deliveries differing only in target activation remain separate',()=>{
 const rows=handoffDeliveries([e(1,'handoff-sent',{target_activation:'first'}),e(2,'handoff-sent',{target_activation:'second'})],'run');
 assert.equal(rows.length,2);assert.deepEqual(rows.map(r=>r.target_activation),['first','second']);
});
test('ready/sent merge only for the exact target; late ready never downgrades either target',()=>{
 const rows=handoffDeliveries([e(1,'handoff-ready',{target_activation:'first'}),e(2,'handoff-sent',{target_activation:'second'}),e(3,'handoff-sent',{target_activation:'first'}),e(4,'handoff-ready',{target_activation:'second'})],'run');
 assert.equal(rows.length,2);assert(rows.every(r=>r.kind==='handoff-sent'));assert.deepEqual(rows.map(r=>[r.target_activation,r.seq]),[['first',3],['second',2]]);
});
test('unknown target never merges into a known activation',()=>{
 const rows=handoffDeliveries([e(1,'handoff-ready'),e(2,'handoff-sent',{target_activation:'known'}),e(3,'handoff-sent')],'run');assert.equal(rows.length,2);assert.equal(rows[0].target_activation,undefined);assert.equal(rows[0].kind,'handoff-sent');assert.equal(rows[1].target_activation,'known');
});

const billed=(id,at,cost,semantics='turn',scope=id)=>{const v=n(id,at,2,3,semantics,scope);v.output.receipt.usage.total_cost_usd=cost;return v};
test('Provider per-turn costs add actual reported amounts independently of tokens',()=>{
 const r=usageSummary([billed('a',1,.125),billed('b',2,.25)]);assert.equal(r.cost,.375);assert.equal(r.costReported,2);assert.equal(r.costMissing,0);assert.equal(r.costComplete,true);assert.equal(r.input,4);assert.equal(r.output,6);
});
test('cumulative cost keeps latest report of the same native scope, not the sum or maximum',()=>{
 const r=usageSummary([billed('new',3,.25,'session-cumulative-do-not-sum','same'),billed('old',1,.5,'session-cumulative-do-not-sum','same')]);assert.equal(r.cost,.25);assert.equal(r.costReported,1);assert.equal(r.costComplete,true);
});
test('multiple cumulative sessions and per-turn reports remain separate accounts',()=>{
 const r=usageSummary([billed('old',1,.125,'session-cumulative-do-not-sum','A'),billed('new',2,.25,'session-cumulative-do-not-sum','A'),billed('other',3,.5,'session-cumulative-do-not-sum','B'),billed('turn',4,.125)]);assert.equal(r.cost,.875);assert.equal(r.costReported,3);assert.equal(r.costComplete,true);
});
test('mixed known and unknown Provider cost is explicitly partial reporting',()=>{
 const r=usageSummary([billed('known',1,.125),billed('unknown',2,'unknown')]);assert.equal(r.cost,.125);assert.equal(r.costReported,1);assert.equal(r.costMissing,1);assert.equal(r.costComplete,false);assert.equal(r.complete,true);
 const all=usageSummary([billed('unknown',1,'unknown')]);assert.equal(all.cost,null);assert.equal(all.costReported,0);assert.equal(all.costComplete,false);
});
test('a missing cumulative round preserves the last actual amount as partial, without adding rounds',()=>{
 const r=usageSummary([billed('old',1,.125,'session-cumulative-do-not-sum','same'),billed('unknown',2,'unknown','session-cumulative-do-not-sum','same')]);assert.equal(r.cost,.125);assert.equal(r.costReported,1);assert.equal(r.costComplete,false);
});
test('zero reported USD is known cost, not unavailable or a fabricated saving',()=>{
 const r=usageSummary([billed('zero',1,0)]);assert.equal(r.cost,0);assert.equal(r.costReported,1);assert.equal(r.costMissing,0);assert.equal(r.costComplete,true);
});
for(const invalid of [-1,NaN,Infinity,-Infinity,null,true,'0'])test(`invalid Provider cost ${String(invalid)} is unavailable and cannot replace token statistics`,()=>{
 const r=usageSummary([billed('invalid',1,invalid)]);assert.equal(r.cost,null);assert.equal(r.costReported,0);assert.equal(r.costMissing,1);assert.equal(r.costComplete,false);assert.equal(r.input,2);assert.equal(r.reported,1);
});
test('cost report does not require invented tokens; cumulative cost without scope is unknown',()=>{
 const one=billed('cost-only',1,.125);one.output.receipt.usage.usage={};const r=usageSummary([one]);assert.equal(r.cost,.125);assert.equal(r.costComplete,true);assert.equal(r.reported,0);assert.equal(r.missing,1);
 const unscoped=billed('bad',2,.5,'session-cumulative-do-not-sum');delete unscoped.output.receipt.usage_scope;const missing=usageSummary([unscoped]);assert.equal(missing.cost,null);assert.equal(missing.costComplete,false);
});
test('overflow of finite reports cannot be displayed as an infinite bill',()=>{
 const r=usageSummary([billed('a',1,Number.MAX_VALUE),billed('b',2,Number.MAX_VALUE)]);assert.equal(r.cost,null);assert.equal(r.costReported,2);assert.equal(r.costComplete,false);
});

import {providerLabel} from "../tools/studio/workbench_ui/provider_label.mjs";
test("Provider labels preserve Copilot and never guess Claude",()=>{assert.equal(providerLabel("copilot"),"GitHub Copilot");assert.equal(providerLabel("claude"),"Claude Code");assert.equal(providerLabel("codex"),"Codex");assert.equal(providerLabel("unverified"),"Agent（未确认）")});
