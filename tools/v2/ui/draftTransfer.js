const FORMAT = 'devflower.v2.workflow-draft';
const MAX_BYTES = 420000;
const FIELDS = ['document', 'document_sha256', 'format', 'version'];
const ENCODER = new TextEncoder();
const HEX_64 = /^[0-9a-f]{64}$/;
const INVALID_STRING = /[\u0000\ud800-\udfff]/u;
const INVALID_FORMAT = '工作流文件格式或编码无效。';
const INVALID_DIGEST = '工作流文件内容校验失败。';

function canonical(value) {
 if (value === null || typeof value === 'boolean') return JSON.stringify(value);
 if (typeof value === 'number' && Number.isFinite(value)) return JSON.stringify(value);
 if (typeof value === 'string' && !INVALID_STRING.test(value)) return JSON.stringify(value);
 if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']';
 if (value && typeof value === 'object') {
  return '{' + Object.keys(value).sort().map(key => canonical(key) + ':' + canonical(value[key])).join(',') + '}';
 }
 throw new Error(INVALID_FORMAT);
}

async function sha256(bytes) {
 const hash = new Uint8Array(await crypto.subtle.digest('SHA-256', bytes));
 return Array.from(hash, value => value.toString(16).padStart(2, '0')).join('');
}

function archiveBytes(archive) {
 return ENCODER.encode(canonical(archive) + '\n');
}

export async function readDraftArchive(file) {
 let archive, bytes, documentBytes;
 try {
  if (!Number.isSafeInteger(file.size) || file.size < 1 || file.size > MAX_BYTES) throw new Error();
  bytes = new Uint8Array(await file.arrayBuffer());
  if (bytes.length !== file.size) throw new Error();
  archive = JSON.parse(new TextDecoder('utf-8', {fatal: true, ignoreBOM: true}).decode(bytes));
  const keys = archive && typeof archive === 'object' ? Object.keys(archive).sort() : [];
  if (keys.length !== FIELDS.length || !keys.every((key, index) => key === FIELDS[index])
      || archive.format !== FORMAT || archive.version !== 1
      || typeof archive.document_sha256 !== 'string' || !HEX_64.test(archive.document_sha256)) throw new Error();
  const encoded = archiveBytes(archive);
  if (encoded.length !== bytes.length || !encoded.every((byte, index) => byte === bytes[index])) throw new Error();
  documentBytes = ENCODER.encode(canonical(archive.document));
 } catch {
  throw new Error(INVALID_FORMAT);
 }
 const documentSha = await sha256(documentBytes);
 if (archive.document_sha256 !== documentSha) throw new Error(INVALID_DIGEST);
 return {document: archive.document, documentSha, bytes};
}

export async function makeDraftArchive(document) {
 const documentSha = await sha256(ENCODER.encode(canonical(document)));
 const bytes = archiveBytes({format: FORMAT, version: 1, document, document_sha256: documentSha});
 if (bytes.length > MAX_BYTES) throw new Error(INVALID_FORMAT);
 return bytes;
}

export function downloadDraftArchive(bytes) {
 const url = URL.createObjectURL(new Blob([bytes], {type: 'application/json'}));
 try {
  const link = document.createElement('a');
  link.href = url;
  link.download = 'devflower-workflow.json';
  link.click();
 } finally {
  setTimeout(() => URL.revokeObjectURL(url), 0);
 }
}
