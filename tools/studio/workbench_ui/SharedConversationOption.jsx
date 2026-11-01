import React from 'react';

export function SharedConversationOption({value,onChange,disabled=false}){
 return <div className="field shared-conversation-option"><label><input type="checkbox" checked={value} disabled={disabled} onChange={e=>onChange(e.target.checked)}/>共享 Codex 纯对话</label><small>沿用同一原生会话和历史；目前仅支持 Ask/Plan，不读取工作区文件，不启用 Skill 或子 Agent。原生询问请求会停止本轮，不自动确认。已共享的会话需保持此模式。</small></div>;
}
