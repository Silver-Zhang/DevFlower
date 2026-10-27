import React,{useState,useEffect,useLayoutEffect,useRef} from 'react';
import {Sun,Moon,Monitor,PanelLeftClose,PanelLeftOpen} from 'lucide-react';
import {useDragResize} from './vendor/sim/use-drag-resize.ts';

const clamp=(value,min,max)=>Math.max(min,Math.min(value,Math.max(min,max)));
const defaults=()=>({sidebar:innerWidth<720?156:innerWidth<950?180:innerWidth<1200?200:224,tools:innerWidth<950?264:innerWidth<1200?288:320,activity:180});
function stored(){try{return JSON.parse(localStorage.getItem('devflower-workspace-layout')||'{}')}catch{return {}}}
export function useWorkspaceLayout(){
 const [layout,setLayout]=useState(()=>{const saved=stored(),base=defaults();for(const k of ['sidebar','tools','activity'])if(Number.isFinite(saved[k]))base[k]=clamp(saved[k],k==='sidebar'?156:k==='tools'?264:120,k==='sidebar'?420:k==='tools'?520:innerHeight*.6);return base});
 const [collapsed,setCollapsed]=useState(()=>stored().collapsed===true),explicit=useRef(stored()),active=useRef(false),priorBody=useRef({});
 const target=()=>document.querySelector('.workbench');
 function commit(key,value){explicit.current={...explicit.current,[key]:value};localStorage.setItem('devflower-workspace-layout',JSON.stringify({...explicit.current,collapsed}));setLayout(old=>({...old,[key]:value}))}
 useEffect(()=>{const resize=()=>{const base=defaults();setLayout(old=>({...old,...Object.fromEntries(Object.entries(base).filter(([key])=>!Number.isFinite(explicit.current[key])))}))};window.addEventListener('resize',resize);return()=>window.removeEventListener('resize',resize)},[]);
 const start=()=>{if(active.current)return false;active.current=true;priorBody.current={cursor:document.body.style.cursor,userSelect:document.body.style.userSelect};target()?.setAttribute('data-resizing','');return true};
 const end=()=>{active.current=false;target()?.removeAttribute('data-resizing');Object.assign(document.body.style,priorBody.current)};
 const sidebar=useDragResize({cursor:'ew-resize',cssVar:'--sidebar-width',getTarget:target,compute:e=>clamp(e.clientX,156,Math.min(420,innerWidth-(innerWidth>720?layout.tools:0)-300)),commit:value=>commit('sidebar',value),onStart:()=>collapsed?false:start(),onEnd:end});
 const tools=useDragResize({cursor:'ew-resize',cssVar:'--tools-width',getTarget:target,compute:e=>clamp(innerWidth-e.clientX,264,Math.min(520,innerWidth-(collapsed?52:layout.sidebar)-300)),commit:value=>commit('tools',value),onStart:start,onEnd:end});
 const activity=useDragResize({cursor:'ns-resize',cssVar:'--activity-height',getTarget:target,compute:e=>clamp(innerHeight-e.clientY-33,120,innerHeight*.6),commit:value=>commit('activity',value),onStart:start,onEnd:end});
 const keyResize=(key,e)=>{const delta=['ArrowRight','ArrowUp'].includes(e.key)?10:['ArrowLeft','ArrowDown'].includes(e.key)?-10:0;if(!delta&&e.key!=='Home')return;e.preventDefault();const min=key==='sidebar'?156:key==='tools'?264:120,max=key==='sidebar'?Math.min(420,innerWidth-(innerWidth>720?layout.tools:0)-300):key==='tools'?Math.min(520,innerWidth-(collapsed?52:layout.sidebar)-300):innerHeight*.6;commit(key,e.key==='Home'?defaults()[key]:clamp(layout[key]+delta,min,max))};
 return {layout,collapsed,style:{'--sidebar-width':(collapsed?52:layout.sidebar)+'px','--tools-width':layout.tools+'px','--activity-height':layout.activity+'px'},toggle:()=>{setCollapsed(value=>{const next=!value;explicit.current={...explicit.current,collapsed:next};localStorage.setItem('devflower-workspace-layout',JSON.stringify(explicit.current));return next})},sidebar,tools,activity,keyResize};
}
export function ResizeHandle({name,axis='vertical',value,onPointerDown,onKeyDown,className=''}){return <div role="separator" aria-label={name} aria-orientation={axis} aria-valuenow={Math.round(value)} tabIndex={0} title="拖动调整大小，方向键微调，Home 恢复默认" className={'resize-handle '+axis+' '+className} onPointerDown={onPointerDown} onKeyDown={onKeyDown}/>}
export function SidebarToggle({collapsed,onClick}){const Icon=collapsed?PanelLeftOpen:PanelLeftClose;return <button className="sidebar-toggle" aria-label={collapsed?'展开导航栏':'收起导航栏'} title={collapsed?'展开导航栏':'收起导航栏'} onClick={onClick}><Icon size={15}/></button>}
export function ThemePicker({onResolvedTheme}){
 const [theme,setTheme]=useState(()=>{const value=localStorage.getItem('devflower-theme');return ['light','dark','system'].includes(value)?value:'system'}),[dark,setDark]=useState(matchMedia('(prefers-color-scheme: dark)').matches);
 useEffect(()=>{const media=matchMedia('(prefers-color-scheme: dark)'),change=()=>setDark(media.matches);media.addEventListener('change',change);return()=>media.removeEventListener('change',change)},[]);
 const resolved=theme==='system'?(dark?'dark':'light'):theme;
 useLayoutEffect(()=>{document.documentElement.dataset.theme=resolved;document.documentElement.style.colorScheme=resolved;localStorage.setItem('devflower-theme',theme);onResolvedTheme?.(resolved)},[theme,resolved,onResolvedTheme]);
 const Icon=theme==='system'?Monitor:resolved==='dark'?Moon:Sun;
 return <label className="theme-picker" title="外观不影响工作流或 Agent 配置"><Icon size={14}/><select aria-label="外观主题" value={theme} onChange={e=>setTheme(e.target.value)}><option value="system">跟随系统</option><option value="light">浅色</option><option value="dark">深色</option></select></label>;
}
