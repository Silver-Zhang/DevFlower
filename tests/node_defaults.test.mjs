import {test} from 'node:test';
import assert from 'node:assert/strict';
import {profileTimeout,newAgentDefaults,CODEX_NEW_AGENT} from '../tools/studio/workbench_ui/node_defaults.mjs';

test('new node timeout inherits the configured provider and stays bounded',()=>{
 for(const [value,want] of [[1800,1800],[10,10],[3600,3600],[1,10],[9000,3600],[null,300],[true,300],['1800',300],[2.5,300]])
  assert.equal(profileTimeout({timeout_seconds:value}),want);
 assert.equal(profileTimeout({}),300);
 const saved={timeout:300},before=structuredClone(saved);profileTimeout({timeout_seconds:1800});assert.deepEqual(saved,before);
});

test('a new Codex agent starts at Luna/low without rewriting the profile',()=>{
 const profile={id:'codex',provider:'codex',model:'gpt-6.1-terra',effort:'high'},before=structuredClone(profile);
 assert.deepEqual(newAgentDefaults(profile),CODEX_NEW_AGENT);
 assert.deepEqual(newAgentDefaults(profile),{model:'gpt-6-luna',effort:'low'});
 assert.deepEqual(profile,before);
 assert.notEqual(newAgentDefaults(profile),CODEX_NEW_AGENT);
});

test('a new non-Codex agent keeps the provider profile or nothing',()=>{
 assert.deepEqual(newAgentDefaults({provider:'claude',model:'claude-opus-5-5',effort:'high'}),{model:'claude-opus-5-5',effort:'high'});
 assert.deepEqual(newAgentDefaults({provider:'claude'}),{model:undefined,effort:undefined});
 assert.deepEqual(newAgentDefaults(undefined),{model:undefined,effort:undefined});
});
