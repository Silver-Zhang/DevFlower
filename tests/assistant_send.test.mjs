import test from 'node:test';
import assert from 'node:assert/strict';
import {sendFailureIsAmbiguous} from '../tools/studio/workbench_ui/assistant_send.mjs';

test('a rejected request the server answered stays editable',()=>{
 for(const status of [400,409,422]){
  assert.equal(sendFailureIsAmbiguous(status),false,'status '+status);
 }
});

test('a lost reply or a server fault keeps the uncertain path',()=>{
 assert.equal(sendFailureIsAmbiguous(undefined),true);
 assert.equal(sendFailureIsAmbiguous(null),true);
 assert.equal(sendFailureIsAmbiguous(500),true);
 assert.equal(sendFailureIsAmbiguous(503),true);
});

test('an unusable status is treated as unknown rather than as a rejection',()=>{
 assert.equal(sendFailureIsAmbiguous(''),true);
 assert.equal(sendFailureIsAmbiguous(Number.NaN),true);
});
