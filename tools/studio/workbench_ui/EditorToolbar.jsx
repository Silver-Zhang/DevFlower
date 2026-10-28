import React, {useState} from 'react';
import {MousePointer2, Hand, Undo2, Redo2, Copy, ClipboardPaste, Trash2, AlignLeft, ChevronDown, Map,Minus,Plus,Scan,Maximize2,Minimize2} from 'lucide-react';

export function EditorToolbar({focused,onFocus,mode, setMode, count, disabled, undo, redo, canUndo, canRedo, command, minimap, setMinimap,onFit,zoomIn,zoomOut,menuOpen,setMenuOpen}) {
  const [localOpen,setLocalOpen]=useState(false);const open=menuOpen??localOpen,setOpen=setMenuOpen||setLocalOpen;
  return <div className="canvas-edit-tools" role="toolbar" aria-label="画布编辑工具">
    <button title="选择 / 框选（Shift 多选）" aria-label="选择与框选" aria-pressed={mode === 'select'} onClick={() => setMode('select')}><MousePointer2 size={16}/></button>
    <button title="平移（也可按住空格）" aria-label="平移画布" aria-pressed={mode === 'pan'} onClick={() => setMode('pan')}><Hand size={16}/></button>
    <button title={focused?'退出专注，恢复两侧面板':'专注画布，收起两侧面板'} aria-label={focused?'退出专注画布':'专注画布'} aria-pressed={focused} onClick={onFocus}>{focused?<Minimize2 size={16}/>:<Maximize2 size={16}/>}</button>
    <i/>
    <button title="缩小画布" aria-label="缩小画布" onClick={zoomOut}><Minus size={15}/></button>
    <button title="放大画布" aria-label="放大画布" onClick={zoomIn}><Plus size={15}/></button>
    <button title="适应画布" aria-label="适应画布" onClick={onFit}><Scan size={15}/></button>
    <i/>
    <button title="撤销 Ctrl/⌘ Z" aria-label="撤销" disabled={disabled || !canUndo} onClick={undo}><Undo2 size={16}/></button>
    <button title="重做 Ctrl/⌘ Shift Z" aria-label="重做" disabled={disabled || !canRedo} onClick={redo}><Redo2 size={16}/></button>
    <i/>
    <button title="复制选中节点 Ctrl/⌘ C" aria-label="复制选中节点" disabled={disabled || !count} onClick={() => command('copy')}><Copy size={16}/></button>
    <button title="粘贴节点 Ctrl/⌘ V" aria-label="粘贴节点" disabled={disabled} onClick={() => command('paste')}><ClipboardPaste size={16}/></button>
    <button title="删除选中节点 Delete" aria-label="删除选中节点" disabled={disabled || !count} onClick={() => command('delete')}><Trash2 size={16}/></button>
    <div className="arrange-menu"><button aria-label="对齐与分布" disabled={disabled || count < 2} aria-expanded={open} onClick={() => setOpen(!open)}><AlignLeft size={16}/><ChevronDown size={12}/></button>
      {open && <div role="menu">{[['left', '左对齐'], ['center-x', '水平居中'], ['right', '右对齐'], ['top', '顶部对齐'], ['center-y', '垂直居中'], ['bottom', '底部对齐'], ['horizontal', '水平分布'], ['vertical', '垂直分布']].map(([key, text]) => <button role="menuitem" key={key} onClick={() => {command(key); setOpen(false);}}>{text}</button>)}</div>}
    </div>
    <button title="缩略导航" aria-label="缩略导航" aria-pressed={minimap} onClick={() => setMinimap(!minimap)}><Map size={16}/></button>
    {count > 0 && <span aria-live="polite">已选 {count}</span>}
  </div>;
}
