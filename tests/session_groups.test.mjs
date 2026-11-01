import test from 'node:test';
import assert from 'node:assert/strict';
import {timeOf,recentBucket,groupKeyFor} from '../tools/studio/workbench_ui/session_groups.mjs';

const now=Date.UTC(2026,10,1,12,0,0);

test('recent activity buckets split today, this week and older',()=>{
 const hour=3600_000;
 assert.equal(recentBucket({updated:now-hour},now),'今天');
 assert.equal(recentBucket({updated:now-3*24*hour},now),'最近 7 天');
 assert.equal(recentBucket({updated:now-10*24*hour},now),'更早');
 assert.equal(recentBucket({updated:undefined},now),'活动时间未知');
});

test('seconds and ISO timestamps are both understood',()=>{
 assert.equal(timeOf(1_700_000_000),1_700_000_000_000);
 assert.equal(timeOf('2026-11-01T12:00:00Z'),now);
});

test('grouping by recent activity uses the bucket, other modes stay unchanged',()=>{
 const session={updated:now-3600_000,provider:'codex',workflow:'研究流程',workflow_group_id:'draft:a'};
 assert.equal(groupKeyFor(session,'recent',now),'今天');
 assert.equal(groupKeyFor(session,'provider',now),'codex');
 assert.equal(groupKeyFor(session,'workflow',now),'draft:a');
 assert.equal(groupKeyFor({provider:'claude'},'workflow',now),'unlinked');
});
