import React, {useEffect, useRef} from 'react';
import {canonical, sha256} from './draftTransfer.js';

const FORMAT = 'devflower.v2.run-summary';
const MAX_BYTES = 8192;
const MAX_CREATED_AT_MS = 253402300799999;
const HEX_32 = /^[0-9a-f]{32}$/;
const HEX_64 = /^[0-9a-f]{64}$/;
const ENCODER = new TextEncoder();
const ENVELOPE_KEYS = ['authority', 'format', 'snapshot', 'snapshot_sha256', 'version'];
const SNAPSHOT_KEYS = ['agent_count', 'created_at_ms', 'edge_count', 'source_fingerprint', 'source_recorded_state'];
const STATES = new Map([
 ['COMPLETED', '来源记录已完成'],
 ['FAILED', '来源记录失败'],
 ['CANCELLED', '来源记录的本机调度已取消'],
 ['RECOVERY_REQUIRED', '结果未知'],
]);
export const SUMMARY_FILE_ERROR = '运行摘要文件格式或校验无效。';

function boundedInteger(value, max) {
 return Number.isSafeInteger(value) && value >= 0 && value <= max;
}
function exact(value, keys) {
 if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
 const actual = Object.keys(value).sort();
 return actual.length === keys.length && actual.every((key, index) => key === keys[index]);
}
function validSnapshot(value) {
 return exact(value, SNAPSHOT_KEYS) &&
  boundedInteger(value.agent_count, 64) && boundedInteger(value.edge_count, 128) &&
  (value.created_at_ms === null || boundedInteger(value.created_at_ms, MAX_CREATED_AT_MS)) &&
  typeof value.source_fingerprint === 'string' && HEX_64.test(value.source_fingerprint) &&
  STATES.has(value.source_recorded_state);
}

export async function parseRunSummary(bytes) {
 if (!boundedInteger(bytes.length, MAX_BYTES) || bytes.length === 0) throw Error(SUMMARY_FILE_ERROR);
 const value = JSON.parse(new TextDecoder('utf-8', {fatal:true, ignoreBOM:true}).decode(bytes));
 if (!exact(value, ENVELOPE_KEYS) ||
     value.authority !== false || value.format !== FORMAT || value.version !== 1 ||
     !validSnapshot(value.snapshot) || typeof value.snapshot_sha256 !== 'string' ||
     !HEX_64.test(value.snapshot_sha256)) throw Error(SUMMARY_FILE_ERROR);
 const encoded = ENCODER.encode(canonical(value) + '\n');
 if (bytes.length !== encoded.length || !bytes.every((byte, index) => byte === encoded[index]) ||
     value.snapshot_sha256 !== await sha256(ENCODER.encode(canonical(value.snapshot)))) throw Error(SUMMARY_FILE_ERROR);
 return value.snapshot;
}

export async function openRunSummary(file) {
 if (!boundedInteger(file.size, MAX_BYTES) || file.size === 0) throw Error(SUMMARY_FILE_ERROR);
 const bytes = new Uint8Array(await file.arrayBuffer());
 if (file.size !== bytes.length) throw Error(SUMMARY_FILE_ERROR);
 return parseRunSummary(bytes);
}

export function selectedRunMetadata(value, workspace, runId) {
 const run = value?.run, definition = value?.definition;
 if (!run || !definition || !workspace || typeof runId !== 'string' || !HEX_32.test(runId) ||
     run.workspace !== workspace || definition.workspace !== workspace || run.id !== runId ||
     run.definition_id !== definition.id || typeof definition.id !== 'string' ||
     !HEX_64.test(definition.id) || !STATES.has(run.persisted_state)) return null;
 const graph = definition.draft_document;
 if (!Array.isArray(graph?.nodes) || !Array.isArray(graph?.edges) ||
     graph.nodes.length > 64 || graph.edges.length > 128) return null;
 const time = typeof run.created_at === 'number' ? Math.floor(run.created_at * 1000) : NaN;
 return {
  runId, definitionId:definition.id, source_recorded_state:run.persisted_state,
  created_at_ms:boundedInteger(time, MAX_CREATED_AT_MS) ? time : null,
  agent_count:graph.nodes.length, edge_count:graph.edges.length,
 };
}

export async function readRunSummary(workspace, source, token) {
 const query = new URLSearchParams({workspace, run:source.runId});
 const response = await fetch('/api/v2/run-summary?' + query, {headers:{Authorization:'Bearer ' + token}});
 const size = Number(response.headers.get('Content-Length'));
 if (response.status !== 200 || !boundedInteger(size, MAX_BYTES) || size === 0) throw Error();
 const bytes = new Uint8Array(await response.arrayBuffer());
 if (bytes.length !== size) throw Error();
 const snapshot = await parseRunSummary(bytes);
 const {runId, definitionId, ...expected} = source;
 expected.source_fingerprint = await sha256(ENCODER.encode(FORMAT + '\0' + runId + '\0' + definitionId));
 if (canonical(snapshot) !== canonical(expected)) throw Error();
 return bytes;
}

export function downloadRunSummary(bytes) {
 const url = URL.createObjectURL(new Blob([bytes], {type:'application/json'}));
 try {
  const link = document.createElement('a');
  link.href = url;
  link.download = 'devflower-run-summary.json';
  link.click();
 } finally {
  setTimeout(() => URL.revokeObjectURL(url), 0);
 }
}

export function RunSummaryFile({snapshot, onClose}) {
 const dialog = useRef(null);
 useEffect(() => {
  const element = dialog.current;
  element.showModal();
  return () => element.close();
 }, []);
 return <dialog ref={dialog} className="run-summary-file" aria-labelledby="run-summary-file-title"
  onCancel={event=>{event.preventDefault();onClose()}} onKeyDown={event=>event.stopPropagation()}>
  <h2 id="run-summary-file-title">运行摘要文件</h2>
  <p>只读档案 · 来源状态由导出方记录</p>
  <p>当前状态未核验</p>
  {snapshot?<dl>
   <dt>来源记录状态</dt><dd>{STATES.get(snapshot.source_recorded_state)}</dd>
   <dt>来源记录时间</dt><dd>{snapshot.created_at_ms === null ? '未知' : new Date(snapshot.created_at_ms).toISOString()}</dd>
   <dt>Agent 数量</dt><dd>{snapshot.agent_count}</dd>
   <dt>连线数量</dt><dd>{snapshot.edge_count}</dd>
   <dt>来源指纹</dt><dd><code>{snapshot.source_fingerprint}</code></dd>
  </dl>:<p role="alert" className="error-text">{SUMMARY_FILE_ERROR}</p>}
  <footer><button autoFocus onClick={onClose}>关闭</button></footer>
 </dialog>;
}
