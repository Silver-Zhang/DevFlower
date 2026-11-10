import React,{useState,useEffect,useRef} from 'react';
import {Search,X,Plus,Copy,ClipboardPaste,Trash2,Scan,ArrowRight} from 'lucide-react';

export function ConnectedNodePicker({pending,kinds,onChoose,onClose,canChoose}){
 const [query,setQuery]=useState(''),[active,setActive]=useState(0),ref=useRef(null);
 const entries=Object.entries(kinds).filter(([kind,[name,,alias]])=>canChoose(kind)&&(name+' '+kind+' '+(alias||'')).toLowerCase().includes(query.toLowerCase()));
 useEffect(()=>{ref.current?.focus()},[]);
 const choose=kind=>{if(kind)onChoose(kind)};
 return <div className="connected-node-picker" role="dialog" aria-label="在画布添加节点" style={{left:Math.min(Math.max(12,pending.screen.x),innerWidth-272),top:Math.min(Math.max(75,pending.screen.y),Math.max(75,innerHeight-410))}} onPointerDown={e=>e.stopPropagation()} onKeyDown={e=>{if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();setActive(i=>(i+(e.key==='ArrowDown'?1:entries.length-1))%Math.max(1,entries.length))}else if(e.key==='Enter'){e.preventDefault();choose(entries[active]?.[0])}}}>
  <header><strong>{pending.source||pending.target?'添加并连接节点':'添加节点'}</strong><button aria-label="关闭画布节点选择器" onClick={onClose}><X size={14}/></button></header>
  <label><Search size={14}/><input ref={ref} aria-label="搜索节点类型" placeholder="搜索节点…" value={query} onChange={e=>{setQuery(e.target.value);setActive(0)}}/></label>
  {(pending.source||pending.target)&&<p><ArrowRight size={12}/>选择后建立连线 · 未执行</p>}
  <div role="listbox" aria-label="可添加的节点">{entries.map(([kind,[label,Icon,alias]],i)=><button role="option" aria-selected={i===active} key={kind} onMouseEnter={()=>setActive(i)} onClick={()=>choose(kind)}><Icon size={16}/><span>{label}{alias&&alias!==label&&<small>{alias}</small>}</span><Plus size={13}/></button>)}{!entries.length&&<small>没有匹配的节点</small>}</div>
  <footer>↑↓ 选择　Enter 添加　Esc 取消</footer>
 </div>;
}
export function CanvasContextMenu({menu,onAction,onAdd,onFit}){
 const ref=menu.menuRef;
 useEffect(()=>{ref.current?.querySelector('[role=menuitem]')?.focus()},[menu.isBlockMenuOpen,menu.isPaneMenuOpen]);
 const node=menu.isBlockMenuOpen,actions=node?[['copy','复制选中节点',Copy,'Ctrl C'],['duplicate','创建节点副本',Copy,'Ctrl D'],['delete','删除选中节点',Trash2,'Delete']]:[['paste','粘贴节点',ClipboardPaste,'Ctrl V']];
 return <div ref={ref} className="canvas-context-menu" role="menu" aria-label="画布快捷操作" style={{left:Math.min(menu.position.x,innerWidth-230),top:Math.min(menu.position.y,innerHeight-230)}} onKeyDown={e=>{if(['ArrowDown','ArrowUp'].includes(e.key)){e.preventDefault();const items=[...ref.current.querySelectorAll('[role=menuitem]')],index=items.indexOf(document.activeElement);items[(index+(e.key==='ArrowDown'?1:items.length-1))%items.length]?.focus()}}}>
  {!node&&<button role="menuitem" onClick={onAdd}><Plus size={14}/><span>在此添加节点</span></button>}
  {actions.map(([action,label,Icon,key])=><button role="menuitem" aria-label={label} key={action} onClick={()=>onAction(action)}><Icon size={14}/><span>{label}</span><kbd>{key}</kbd></button>)}
  <button role="menuitem" onClick={onFit}><Scan size={14}/><span>适应画布</span></button>
 </div>;
}
