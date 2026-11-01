import test from 'node:test';
import assert from 'node:assert/strict';
import {nodeGroups} from '../tools/studio/workbench_ui/journal_groups.mjs';
test('one activation groups input, answer and tool-only records without changing evidence',()=>{
 const entries=[{id:'i',node:'a',name:'处理任务',activation:'turn1',kind:'input'},{id:'a',node:'a',name:'处理任务',activation:'turn1',kind:'assistant',text:'answer',at:12},{id:'t1',node:'a',name:'处理任务',activation:'turn1',kind:'tool',tool:{call_id:'c1'}},{id:'t2',node:'a',name:'处理任务',activation:'turn1',kind:'tool',tool:{call_id:'c2'}}];
 const raw=JSON.stringify(entries),group=nodeGroups(entries)[0];assert.equal(group.entries.length,1);assert.equal(group.entries[0].kind,'activation');assert.equal(group.entries[0].members.length,4);assert.equal(group.entries[0].at,12);assert.equal(JSON.stringify(entries),raw);
});
test('identical names and missing clocks never merge distinct rounds or nodes',()=>{
 const entries=['round1','round2'].flatMap(activation=>['assistant','tool'].map(kind=>({id:activation+kind,node:'a',name:'same',activation,kind})));
 entries.push({id:'other',node:'b',name:'same',activation:'round1',kind:'assistant'});const groups=nodeGroups(entries);assert.equal(groups.length,2);assert.equal(groups[0].entries.length,2);assert.equal(groups[1].entries.length,1);assert.equal(groups[0].entries[0].at,null);
});
test('legacy entries without exact activation and handoffs stay independent',()=>{
 const entries=[{id:'a',node:'x',kind:'assistant'},{id:'t',node:'x',kind:'tool'},{id:'h',node:'x',kind:'handoff'}];assert.deepEqual(nodeGroups(entries)[0].entries,entries);
});
