import {test} from 'node:test';
import {strict as assert} from 'node:assert';
import {inspectionCurrent,inspectionSummary} from '../tools/studio/workbench_ui/inspection_identity.mjs';
test('checked workspace, graph and task must all match',()=>{
 const graph={name:'same',nodes:[{id:'outer',body:{nodes:[{id:'same'}]}}]};const check={workspace:'a',doc:JSON.stringify(graph),task:'task'};
 assert.equal(inspectionCurrent(check,'a',graph,'task'),true);
 assert.equal(inspectionCurrent(check,'b',graph,'task'),false);
 assert.equal(inspectionCurrent(check,'a',{...graph,nodes:[]},'task'),false);
 assert.equal(inspectionCurrent(check,'a',graph,'changed'),false);
 assert.equal(inspectionCurrent({...check,stale:true},'a',graph,'task'),false);
 assert.match(inspectionSummary({issues:[{severity:'error',message:'需要修正'}]}),/错误：需要修正/);
});
