import React from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

const SAFE_TAGS = 'p span br hr h1 h2 h3 h4 h5 h6 strong em del blockquote ul ol li pre code table thead tbody tr th td sup section div'.split(' ');
const SAFE_ELEMENTS = new Set(SAFE_TAGS);
const TRUNCATED = '可能截断，显示纯文本。';
const INCOMPLETE = '此消息跨页或片段不完整，显示纯文本。';
const UNSAFE = '无法安全格式化，显示纯文本。';

function safeMarkdownTree() {
 return tree => {
  const pending = [...tree.children];
  while (pending.length) {
   const node = pending.pop();
   if (node.type === 'text') continue;
   // A raw HTML node can span several lines. Reject the message before
   // ReactMarkdown can discard or reinterpret any of the original text.
   if (node.type !== 'element') throw new Error('Unsafe transcript Markdown tree');
   const original = node.properties;
   node.properties = {};
   if (node.tagName === 'a') {
    node.tagName = 'span';
    node.properties.className = ['native-md-link'];
   } else if (node.tagName === 'img') {
    node.tagName = 'span';
    node.properties.className = ['native-md-image'];
    node.children = [{type: 'text', value: typeof original?.alt === 'string' ? original.alt : ''}];
   } else if (node.tagName === 'input' && original?.type === 'checkbox') {
    node.tagName = 'span';
    node.properties.className = ['native-md-task'];
    node.children = [{type: 'text', value: original.checked === true ? '☑' : '☐'}];
   } else if (node.tagName === 'ol' && Number.isInteger(original?.start)
       && original.start >= 0 && original.start <= 999_999_999) {
    node.properties.start = original.start;
   }
   if (!SAFE_ELEMENTS.has(node.tagName)) throw new Error('Unsafe transcript Markdown element');
   for (const child of node.children) pending.push(child);
  }
 };
}

function ScrollTable({children}) {
 return <div className="native-markdown-table" role="region" aria-label="Markdown 表格" tabIndex={0}>
  <table>{children}</table>
 </div>;
}

const MARKDOWN_COMPONENTS = {table: ScrollTable};
const REMARK_PLUGINS = [remarkGfm];
const REHYPE_PLUGINS = [safeMarkdownTree];
const discardUrl = () => undefined;

function PlainMessage({text, note}) {
 return <>
  {note && <p className="native-message-note muted small">{note}</p>}
  <pre>{text}</pre>
 </>;
}

class MarkdownBoundary extends React.Component {
 state = {failed: false};

 static getDerivedStateFromError() {
  return {failed: true};
 }

 render() {
  if (this.state.failed) return <PlainMessage text={this.props.text} note={UNSAFE}/>;
  return <div className="native-markdown">
   <ReactMarkdown allowedElements={SAFE_TAGS} skipHtml urlTransform={discardUrl}
    remarkPlugins={REMARK_PLUGINS} rehypePlugins={REHYPE_PLUGINS} components={MARKDOWN_COMPONENTS}>
    {this.props.text}
   </ReactMarkdown>
  </div>;
 }
}

export function isVisibleMessage(message) {
 return typeof message?.text === 'string' && (message.role === 'user' || message.role === 'assistant');
}

function groupPage(parts) {
 const messages = [];
 for (let index = 0; index < parts.length;) {
  const first = parts[index], count = first?.part_count;
  if (first?.part_index === 1 && Number.isSafeInteger(count) && count >= 1 && count <= parts.length - index) {
   const group = parts.slice(index, index + count);
   if (group.every((part, offset) => isVisibleMessage(part) && part.role === first.role
       && part.part_count === count && part.part_index === offset + 1)) {
    messages.push({role: first.role, text: group.map(part => part.text).join('')});
    index += count;
    continue;
   }
  }
  messages.push(isVisibleMessage(first) ? {...first, note: INCOMPLETE} : {note: INCOMPLETE});
  index++;
 }
 return messages;
}

function isEnvironmentMessage(message){
 if(message.role!=='user'||!message.text.startsWith('<environment_context>'))return false;
 return /^<environment_context>\s*<cwd>[^<>]+<\/cwd>\s*<shell>[^<>]+<\/shell>\s*(?:<current_date>\d{4}-\d{2}-\d{2}<\/current_date>\s*)?(?:<timezone>[^<>]+<\/timezone>\s*)?(?:<filesystem>[^]*?<\/filesystem>\s*)?<\/environment_context>\s*$/.test(message.text);
}

function devflowerBody(messages){
 const boundary='\n\n用户本次明确请求\n';
 let requestFound=false;
 return messages.map(message=>{
  if(!isVisibleMessage(message)||message.note){
   requestFound=false;
   return message;
  }
  if(message.role==='user'){
   const cut=message.text.indexOf(boundary);
   requestFound=message.text.startsWith('你是 DevFlower 的独立助手。')&&cut>=0;
   return requestFound?{...message,text:message.text.slice(cut+boundary.length),protocol:message.text}:message;
  }
  if(!requestFound)return message;
  requestFound=false;
  const fence=/^```(?:json)?[ \t]*\r?\n([^]*?)\r?\n```$/i.exec(message.text);
  try{
   const result=JSON.parse(fence?.[1]||message.text);
   if(typeof result?.answer==='string'&&Object.keys(result).length===1){
    return {...message,text:result.answer,protocol:message.text};
   }
   if(Array.isArray(result?.operations)){
    return {...message,text:typeof result.summary==='string'?result.summary:'助手提交了画布修改提案，请在助手中核对。',
     protocol:message.text};
   }
  }catch{}
  return message;
 });
}

export function NativeMessages({label, messages, format, paged = false, truncated, empty}) {
 const markdown = format === 'markdown';
 const missing = !Array.isArray(messages);
 const display = missing ? [] : markdown ? devflowerBody(paged ? groupPage(messages) : messages) : messages;
 return <div aria-label={label} className="native-messages">
  {display.length === 0 && <p className="muted small">{missing ? paged ? INCOMPLETE : TRUNCATED : empty}</p>}
  {display.map((message, index) => {
   if (!isVisibleMessage(message)) return <article key={index}>
    <p className="native-message-note muted small">{paged ? INCOMPLETE : TRUNCATED}</p>
   </article>;
   const text = message.text;
   if((!paged||!message.note)&&isEnvironmentMessage(message))return <details className="native-environment" key={index}>
    <summary>运行环境</summary><pre>{text}</pre>
   </details>;
   const labeledPart = paged && Number.isSafeInteger(message.part_index) && Number.isSafeInteger(message.part_count)
    && message.part_count > 1;
   let note = markdown && paged ? message.note : null;
   if (markdown && !paged && !(truncated === false || truncated === true && [...text].length < 16_000)) note = TRUNCATED;
   return <article key={index}>
    <h3>{message.role === 'user' ? '用户' : '助手'}
     {labeledPart && <small className="muted"> · 片段 {message.part_index}/{message.part_count}</small>}</h3>
    {markdown && !note ? <MarkdownBoundary key={text} text={text}/> : <PlainMessage text={text} note={note}/>}
    {message.protocol&&<details><summary>{message.role==='user'?'展开原始原生请求':'展开原始原生回复'}</summary>
     <pre>{message.protocol}</pre></details>}
   </article>;
  })}
 </div>;
}
