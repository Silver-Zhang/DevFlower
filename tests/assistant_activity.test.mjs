import test from 'node:test';
import assert from 'node:assert/strict';
import {activityLabel,activityResolved} from '../tools/studio/workbench_ui/assistant_activity.mjs';

test('a start with a later result no longer reads as in progress',()=>{
 const items=[{name:'search_public_source',state:'started'},{name:'search_public_source',state:'completed',count:1}];
 assert.equal(activityResolved(items,0),true);
 assert.equal(activityLabel(items[0],{resolved:activityResolved(items,0),turnFinished:false}),'公开源码搜索 · 已完成');
 assert.equal(activityLabel(items[1],{turnFinished:false}),'公开源码搜索 · 1 条命中');
});

test('a start inside a finished turn without a result is not still running',()=>{
 const items=[{name:'read_public_source',state:'started'}];
 assert.equal(activityResolved(items,0),false);
 assert.equal(activityLabel(items[0],{resolved:false,turnFinished:true}),'公开源码读取 · 已结束，未返回结果');
});

test('a live start still reads as in progress',()=>{
 assert.equal(activityLabel({name:'read_public_source',state:'started'},{resolved:false,turnFinished:false}),'公开源码读取 · 进行中');
});

test('failures and results keep their own wording',()=>{
 assert.equal(activityLabel({name:'read_public_source',state:'failed',error:'超时'}),'公开源码读取 · 失败：超时');
 assert.equal(activityLabel({name:'read_public_source',state:'completed',path:'a.py',start:570,end:650}),'a.py · 570–650 行');
 assert.equal(activityResolved([{name:'x',state:'started'},{name:'y',state:'completed'}],0),false);
});
