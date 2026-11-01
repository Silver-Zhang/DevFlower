import {test} from 'node:test';
import assert from 'node:assert/strict';
import {profileTimeout} from '../tools/studio/workbench_ui/node_defaults.mjs';

test('new node timeout inherits the configured provider and stays bounded',()=>{
 for(const [value,want] of [[1800,1800],[10,10],[3600,3600],[1,10],[9000,3600],[null,300],[true,300],['1800',300],[2.5,300]])
  assert.equal(profileTimeout({timeout_seconds:value}),want);
 assert.equal(profileTimeout({}),300);
 const saved={timeout:300},before=structuredClone(saved);profileTimeout({timeout_seconds:1800});assert.deepEqual(saved,before);
});
