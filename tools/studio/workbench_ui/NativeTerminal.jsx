import React,{useEffect,useLayoutEffect,useRef,useState} from 'react';
import {Terminal as XTerm} from '@xterm/xterm';
import {FitAddon} from '@xterm/addon-fit';
import {Terminal,Play,Square,RefreshCw} from 'lucide-react';
import '@xterm/xterm/css/xterm.css';
import {statusInfo} from './runtime_projection.mjs';
import {ConversationTimeline} from './ConversationView.jsx';
import {checkConnections} from './start_checks.mjs';

// An explicit terminal-tab click may check and open a new PTY; the optional
// preview uses its confirm button. Mount/reload only attaches a known PTY and
// reads output. Failed checks need a separately labelled, deliberate retry.
export function NativeTerminal({api,workspace,session,onChanged,onClose,intent=0}) {
  const host=useRef(null),terminal=useRef(null),fit=useRef(null),current=useRef(null),version=useRef(0);
  const request=useRef(0);
  const sequence=useRef(0),pending=useRef(Promise.resolve()),uncertain=useRef(false),cursor=useRef(0);
  const [history,setHistory]=useState(null);
  const [row,setRow]=useState(null),[preview,setPreview]=useState(null),[error,setError]=useState(''),[connected,setConnected]=useState(false);
  const [loading,setLoading]=useState(false);
  const [revision,setRevision]=useState(0);
  const [progress,setProgress]=useState('');
  const [shared,setShared]=useState(null);
  const [retry,setRetry]=useState(null),[hasOutput,setHasOutput]=useState(false);
  const consumedIntent=useRef(null);
  const scope=workspace.id+':'+session.handle;
  useEffect(()=>setShared(null),[scope]);
  useEffect(()=>{let active=true;setHistory(null);api('/api/history?workspace='+workspace.id+'&handle='+session.handle).then(v=>{if(active)setHistory(v)}).catch(()=>{});return()=>{active=false}},[scope]);
  useLayoutEffect(()=>{
    const term=new XTerm({cursorBlink:true,convertEol:false,fontSize:12,fontFamily:'ui-monospace,monospace',
      scrollback:2000,allowProposedApi:false,disableStdin:true,theme:{background:'#111923',foreground:'#dae7fa'}});
    const addon=new FitAddon();term.loadAddon(addon);term.open(host.current);terminal.current=term;fit.current=addon;
    const observer=new ResizeObserver(()=>{if(terminal.current!==term)return;addon.fit();const active=current.current;if(active?.state==='RUNNING'&&active.input_owner!=='local-terminal'&&!uncertain.current)api('/api/terminal/resize',{workspace:workspace.id,id:active.id,generation:active.input_generation||0,cols:Math.max(20,Math.min(300,term.cols)),rows:Math.max(5,Math.min(100,term.rows))}).catch(()=>{})});observer.observe(host.current);
    const input=term.onData(data=>{
      const active=current.current;if(!active||uncertain.current||active.input_owner&&active.input_owner!=='workbench')return;
      // Serialize small writes; an ambiguous response disables input and is never retried.
      const epoch=version.current;
      const characters=Array.from(data);
      for(let start=0;start<characters.length;start+=512){const text=characters.slice(start,start+512).join('');pending.current=pending.current.then(async()=>{
        if(uncertain.current||epoch!==version.current||current.current?.id!==active.id||current.current?.input_generation!==active.input_generation||current.current?.input_owner&&current.current.input_owner!=='workbench')return;const next=sequence.current+1;
        try{await api('/api/terminal/input',{workspace:workspace.id,id:active.id,sequence:next,text,generation:active.input_generation||0});if(epoch===version.current)sequence.current=next;}
        catch(e){if(epoch!==version.current)return;uncertain.current=true;term.options.disableStdin=true;setConnected(false);setError('输入结果未确认，未重发。请重新连接核验后继续：'+e.message)}
      });}
    });
    return()=>{version.current++;request.current++;current.current=null;observer.disconnect();input.dispose();term.dispose();terminal.current=null;fit.current=null;};
  },[scope]);
  const attach=async()=>{
    const token=++version.current,reply=++request.current;setLoading(true);setError('');setPreview(null);setRetry(null);setRow(null);setConnected(false);setHasOutput(false);
    current.current=null;cursor.current=0;sequence.current=0;uncertain.current=false;pending.current=Promise.resolve();
    if(terminal.current){terminal.current.options.disableStdin=true;terminal.current.reset();}
    try{
      const list=await api('/api/terminals?workspace='+workspace.id);if(token!==version.current||reply!==request.current)return;
      const active=list.terminals.find(t=>t.handle===session.handle&&['STARTING','RUNNING','RECOVERY_REQUIRED'].includes(t.state));
      current.current=active||null;cursor.current=0;terminal.current?.reset();setRow(active||null);sequence.current=active?.input_sequence||0;
      uncertain.current=Boolean(active&&active.state==='RECOVERY_REQUIRED');setConnected(Boolean(active&&!uncertain.current));
      if(terminal.current)terminal.current.options.disableStdin=!active||uncertain.current||Boolean(active.input_owner&&active.input_owner!=='workbench');
      pending.current=Promise.resolve();setRevision(v=>v+1);
      return active||null;
    }catch(e){if(reply===request.current)setError(e.message)}finally{if(reply===request.current)setLoading(false)}
  };
  // Clear the prior scope before paint, including on a same-session reconnect.
  useLayoutEffect(()=>{
    // Only this explicit click intent may open a new PTY. A mount/reload with
    // no intent attaches existing output and never starts or replays anything.
    const token=JSON.stringify([scope,intent]);const mayOpen=intent&&consumedIntent.current!==token;
    consumedIntent.current=token;
    attach().then(active=>{if(mayOpen&&active===null)prepare(true)});
  },[scope,intent]);
  useEffect(()=>{
    if(!row||current.current?.id!==row.id)return;const id=row.id,epoch=version.current;let live=true,busy=false,timer;
    const tick=async()=>{
      if(busy||!live||epoch!==version.current||current.current?.id!==id)return;busy=true;
      try{
        const value=await api(`/api/terminal/output?workspace=${workspace.id}&id=${id}&after=${cursor.current}`);
        if(!live||epoch!==version.current||current.current?.id!==id)return;
        if(value.reset){terminal.current?.reset();setHasOutput(false)}if(value.output){terminal.current?.write(value.output);setHasOutput(true)}cursor.current=value.next;
        current.current=value;setRow(value);
        const running=value.state==='RUNNING'&&value.process_alive;
        // Output stays live without rescanning every native history. The sidebar
        // refreshes on explicit actions and actual lifecycle transitions.
        if(!uncertain.current){setConnected(running);if(terminal.current)terminal.current.options.disableStdin=!running||Boolean(value.input_owner&&value.input_owner!=='workbench');}
        if(!running&&['COMPLETED','FAILED','CANCELLED','RECOVERY_REQUIRED'].includes(value.state)){clearInterval(timer);onChanged?.();}
      }catch(e){if(live&&epoch===version.current){setConnected(false);uncertain.current=true;if(terminal.current)terminal.current.options.disableStdin=true;setError('终端连接未确认，未自动重发输入：'+e.message)}}
      finally{busy=false;}
    };
    tick();timer=setInterval(tick,250);return()=>{live=false;clearInterval(timer)};
  },[row?.id,scope,revision]);
  // Replies own UI state only until the next action, reconnect or scope cleanup.
  // A stale start/close may have executed on the server; never cancel or replay it.
  async function prepare(automatic=false){const reply=++request.current;let prepared;setError('');setPreview(null);setRetry(null);setLoading(true);setProgress('正在核验会话身份与占用…');try{
    const value=await api('/api/terminal/prepare',{workspace:workspace.id,handle:session.handle,...(automatic?{auto_verify:true}:{})});
    if(reply!==request.current)return;
    prepared={...value,request:reply};
    if(automatic){
      await checkConnections(api,prepared,'操作者点击原生终端：授权在原环境独占打开此只读会话，并在需要时完成已披露的只读连接检查，不发送源码。',setProgress,()=>reply===request.current);
      if(reply===request.current)await start(prepared);
    }else setPreview(prepared);
  }catch(e){if(reply===request.current){setError(e.message);if(e.retry&&prepared)setRetry({prepared,check:e.retry})}}finally{if(reply===request.current)setLoading(false)}}
  async function retryOpen(){const chosen=retry;if(!chosen)return;const reply=++request.current,prepared={...chosen.prepared,request:reply};setRetry(null);setError('');setLoading(true);try{
    await checkConnections(api,prepared,'操作者明确点击重试连接并打开：仅重试已显示的失败配置，授权一次可能计费的只读 nonce 检查，成功后打开此原生会话。',setProgress,()=>reply===request.current,chosen.check);
    if(reply===request.current)await start(prepared);
  }catch(e){if(reply===request.current){setError(e.message);if(e.retry)setRetry({prepared,check:e.retry})}}finally{if(reply===request.current)setLoading(false)}}
  async function start(prepared=preview){if(!prepared||prepared.request!==request.current||!terminal.current)return;const reply=++request.current;setPreview(null);setLoading(true);setError('');setProgress('正在打开同一原生会话…');try{
    const value=await api('/api/terminal/start',{id:prepared.id,sha256:prepared.sha256,
      answer:'本地工作台操作者确认打开此既有只读原生会话；仅本次操作，保持原环境和历史。',cols:Math.max(20,Math.min(300,terminal.current.cols)),rows:Math.max(5,Math.min(100,terminal.current.rows))});
    if(reply!==request.current)return;
    version.current++;current.current=value;cursor.current=0;sequence.current=value.input_sequence||0;uncertain.current=false;
    pending.current=Promise.resolve();terminal.current.reset();setHasOutput(false);terminal.current.options.disableStdin=value.state!=='RUNNING';setRow(value);setRevision(v=>v+1);setPreview(null);setConnected(value.state==='RUNNING'&&value.process_alive);fit.current.fit();terminal.current.focus();onChanged?.();
  }catch(e){if(reply===request.current)setError(e.message)}finally{if(reply===request.current)setLoading(false)}}
  async function close(){const active=current.current;if(!active||active.id!==row?.id)return;const reply=++request.current;setLoading(true);setError('');setPreview(null);try{
    await api('/api/terminal/close',{workspace:workspace.id,id:active.id});if(reply!==request.current)return;
    terminal.current.options.disableStdin=true;setConnected(false);onChanged?.();
  }catch(e){if(reply===request.current)setError(e.message)}finally{if(reply===request.current)setLoading(false)}}
  async function share(){const active=current.current;if(!active)return;const reply=++request.current;setError('');try{
    const value=await api('/api/terminal/share',{workspace:workspace.id,id:active.id});
    if(reply===request.current)setShared(value);
  }catch(e){if(reply===request.current)setError(e.message)}}
  async function claim(){const active=current.current;if(!active)return;const reply=++request.current;setError('');try{
    const value=await api('/api/terminal/claim',{workspace:workspace.id,id:active.id,generation:active.input_generation||0});
    if(reply!==request.current)return;
    version.current++;current.current=value;sequence.current=value.input_sequence;uncertain.current=false;pending.current=Promise.resolve();
    setRow(value);setRevision(v=>v+1);setConnected(value.process_alive);terminal.current.options.disableStdin=!value.process_alive;terminal.current.focus();
  }catch(e){if(reply===request.current)setError(e.message)}}
  return <div className="native-terminal"><button type="button" onClick={onClose} aria-label="关闭终端视图">关闭终端视图（保持进程）</button><header><div><Terminal size={17}/><b>{session.title}</b><span>{row?.label|| (row?statusInfo(row.state)[0]:'尚未打开')} · {row?.process_alive?(row.input_owner==='local-terminal'?'本机终端正在接管':row.input_owner==='unclaimed'?'等待接管':'工作台正在接管'):'接管前核验占用'} · 原生只读</span></div><div>
    {row?.process_alive&&<><button onClick={share}>在本机终端连接</button>{row.input_owner&&row.input_owner!=='workbench'&&<button onClick={claim}>接管到工作台</button>}</>}
    <button onClick={attach} disabled={loading} aria-label="重新连接终端"><RefreshCw size={14}/>重新连接</button>
    {row?.process_alive?<button onClick={close} disabled={loading}><Square size={14}/>关闭此终端</button>:retry?<button onClick={retryOpen} disabled={loading}><Play size={14}/>重试连接并打开</button>:<button onClick={()=>prepare(Boolean(intent))} disabled={loading||session.choices?.resume===false}><Play size={14}/>{intent?'重新检查并打开':'打开同一原生会话'}</button>}
  </div></header><p className="hint">原CLI、目录与会话身份；与工作流共用写入锁。工作区和认证文件由外层强制只读。必要时自动进行一次只读连接检查，不发送源码。关闭视图只断开显示，关闭终端才停止进程；原生历史保留。</p>
    {session.provider==='claude'&&history?.conversation_turns?.some(t=>t.answer&&t.answer_source==='saved-runtime-result')&&<section className="native-saved-replies" aria-label="此前保存的回答"><h4>此前的 Agent 回答</h4><small>此会话的结构化回答由工作台保存，原生 CLI 可能不回放它。以下来自真实完成回执，未改写原生记录。</small><ConversationTimeline turns={history.conversation_turns.filter(t=>t.answer&&t.answer_source==='saved-runtime-result').slice(-2)}/></section>}
    {['theme','trust','authentication','permissions','update'].includes(row?.phase)&&<section className="terminal-onboarding" role="status"><b>{row.label}</b><p>{row.phase==='theme'?'仅首次需要选择显示样式；已有完成的原生显示设置会安全复用。':row.phase==='update'?'当前使用固定CLI版本；请选择 Skip，升级应在工作台外由操作者处理。':row.phase==='permissions'?'原生工具正在显示权限设置；该终端强制只读，请核对后继续，不会代为启用自动批准。':row.phase==='trust'?'请核对目录后在原生工具中亲自确认；工作台不会代为信任。':'请在原生工具中完成认证；不会复制或覆盖全局认证。'}</p><button onClick={()=>terminal.current?.focus()}>继续原生设置</button></section>}
    <details className="terminal-settings"><summary>高级连接设置</summary><label><input type="checkbox" defaultChecked={localStorage.getItem('devflower-terminal-auto-open')!=='false'} onChange={e=>localStorage.setItem('devflower-terminal-auto-open',String(e.target.checked))}/>点击终端标签时自动检查并打开（仅本浏览器）</label></details>
    {shared&&<section className="terminal-confirm" aria-label="本机原生会话连接"><b>共用当前原生进程</b><p>在本机终端执行下列命令即可实时观察；添加 --takeover 可明确接管键盘。Ctrl-] 断开显示，原生进程保持运行。两端始终共用一个 CLI 和会话。</p><code style={{overflowWrap:'anywhere'}}>{shared.command}</code><p>接管命令：<code>{shared.command} --takeover</code></p>{shared.desktop_reason&&<p role="status">{shared.desktop_reason}</p>}<button onClick={()=>setShared(null)}>收起连接说明</button></section>}
    {error&&<p role="alert" className="terminal-error">{error}</p>}
    {retry&&<p className="connection-notice">仅重试已失败的精确配置；将实际发起一次只读 nonce 检查（可能计费），成功后打开此会话。</p>}
    {preview&&<div className="terminal-confirm"><h3>打开原生只读会话</h3><p>{preview.data.provider} · {preview.data.model} / {preview.data.effort}</p><p>{preview.data.cwd}</p><p>将占用同一会话的原生写入锁；不复制历史、不启动shell、不允许修改工作区。</p><button onClick={()=>setPreview(null)}>取消</button><button onClick={()=>start()} disabled={loading}>确认打开原生终端</button></div>}
    {(!connected||!hasOutput)&&<div className="terminal-connection-state" role="status">{connected&&!hasOutput?'原生进程已连接，正在等待 CLI 输出…':loading?progress:error?'终端未连接，请查看上方原因；未自动重试启动。':row?.state==='RECOVERY_REQUIRED'?'原生进程状态待确认，请重新连接读取现状。':intent?'选择“重新检查并打开”可再次发起连接。':'选择“打开同一原生会话”进入 CLI。'}</div>}
    <div className="terminal-host" ref={host} aria-label="原生CLI终端" data-connected={connected} data-local-escape/>
    <footer>{connected?(hasOutput?(row?.input_owner&&row.input_owner!=='workbench'?'同一原生进程实时可见；此端只读，可明确接管':'原生进程已连接；键盘输入发送到此唯一PTY'):'PTY 已连接，正在等待 CLI 输出'):'输入已关闭或连接未确认，不会自动重放'}{row?.reason&&' · '+row.reason}</footer>
  </div>;
}
