import React from 'react';
import {ModelSelector} from './ModelSelector.jsx';

const OPERATORS=[['equals','等于'],['not_equals','不等于'],['contains','包含'],['greater','大于'],['less','小于'],['exists','存在']];
function PredicateField({label,children}){return <label>{label}{children}</label>}

export function PredicateEditor({value,onChange,labels={},limits={},nullable=false,Field=PredicateField}){
 const edit=(key,next)=>onChange({...value,[key]:next});
 const type=value.value===null?'null':typeof value.value;
 return <>
  <Field label={labels.field||'判定字段'}><input aria-label={labels.field||'判定字段'} value={value.field} placeholder="result.task_type" maxLength={limits.field} onChange={e=>edit('field',e.target.value)}/></Field>
  <Field label={labels.op||'运算符'}><select aria-label={labels.op||'运算符'} value={value.op} onChange={e=>edit('op',e.target.value)}>{OPERATORS.map(([id,label])=><option value={id} key={id}>{label}</option>)}</select></Field>
  <Field label="比较值类型"><select value={type} onChange={e=>edit('value',e.target.value==='number'?0:e.target.value==='boolean'?true:e.target.value==='null'?null:'')}><option value="string">文本</option><option value="number">数字</option><option value="boolean">布尔</option>{nullable&&<option value="null">空值（null）</option>}</select></Field>
  <Field label="比较值">{type==='null'?<span>null</span>:type==='boolean'?<select value={String(value.value)} onChange={e=>edit('value',e.target.value==='true')}><option value="true">是</option><option value="false">否</option></select>:<input value={value.value} type={type==='number'?'number':'text'} maxLength={limits.value} onChange={e=>edit('value',type==='number'?Number(e.target.value):e.target.value)}/>}</Field>
 </>;
}

export function RouterEditor({node,onChange,profiles,api,onCatalog,workspace}){
 const rules=node.routes||[],setRules=routes=>onChange({routes,input_contract:routes.map(r=>({field:r.test.field,type:'any',required:false})).filter((r,i,a)=>a.findIndex(x=>x.field===r.field)===i)});
 const edit=(i,value)=>setRules(rules.map((r,j)=>i===j?{...r,...value}:r));
 const cfg=node.classifier,profile=profiles.find(p=>p.id===cfg?.profile);
 return <section className="router-editor"><p className="hint">从上到下匹配一次。分支名称连接已配置目标，不授予权限。缺字段不会命中“不等于”。</p>
  {rules.map((r,i)=><fieldset key={i}><legend>规则 {i+1}</legend><label>分支名称<input aria-label={'分支名称 '+(i+1)} value={r.label} onChange={e=>edit(i,{label:e.target.value})}/></label><PredicateEditor value={r.test} onChange={test=>edit(i,{test})} labels={{field:'路由字段 '+(i+1),op:'路由运算符 '+(i+1)}}/><button disabled={rules.length<=1} onClick={()=>setRules(rules.filter((_,j)=>j!==i))}>移除规则 {i+1}</button></fieldset>)}
  <button disabled={rules.length>=16} onClick={()=>setRules([...rules,{label:'branch'+(rules.length+1),test:{field:'result.task_type',op:'equals',value:''}}])}>添加分支规则</button>
  <label className="field">默认分支<input aria-label="默认分支" value={node.default_route||''} onChange={e=>onChange({default_route:e.target.value||null})}/><small>无匹配、类型错误或分类失败时进入此分支；留空则明确失败。</small></label>
  <label><input type="checkbox" aria-label="启用语义分类" checked={Boolean(cfg)} onChange={e=>{const p=profiles.find(p=>p.available);onChange({classifier:e.target.checked?{profile:p.id,provider:p.provider,model:p.model,effort:p.effort,threshold:.75,timeout:60,retries:0}:null})}}/>仅当规则未命中时启用只读分类器</label>
  {cfg&&<><label className="field">分类工具<select value={cfg.profile} onChange={e=>{const p=profiles.find(p=>p.id===e.target.value);onChange({classifier:{...cfg,profile:p.id,provider:p.provider,model:p.model,effort:p.effort}})}}>{profiles.filter(p=>p.available).map(p=><option key={p.id} value={p.id}>{p.provider}</option>)}</select></label><ModelSelector api={api} workspace={workspace} mode="Ask" permission="read" timeout={cfg.timeout||60} profile={profile} model={cfg.model} effort={cfg.effort} onCatalog={onCatalog} onChange={v=>onChange({classifier:{...cfg,...v}})}/><label>置信度阈值<input type="number" min="0" max="1" step=".05" value={cfg.threshold} onChange={e=>onChange({classifier:{...cfg,threshold:Number(e.target.value)}})}/></label><label>分类超时（秒）<input type="number" min="10" max="120" value={cfg.timeout} onChange={e=>onChange({classifier:{...cfg,timeout:Number(e.target.value)}})}/></label><p className="hint">使用严格类别 Schema。无效 JSON、低置信度及已确认结束的失败调用进入默认分支；进程状态不明时停止。实际模型调用计入工作流预算。</p></>}
 </section>
}

export function ContractEditor({rows=[],onChange,label,maxRows,maxFieldLength}){
 return <details className="contract-editor"><summary>{label}字段合同 · {rows.length} 项</summary><p className="hint">字段路径使用点号；类型严格区分数字和布尔。必需字段缺失时停止，不执行下游。</p>{rows.map((r,i)=><div key={i}><input aria-label={label+'字段 '+(i+1)} value={r.field} placeholder="result.task_type" maxLength={maxFieldLength} onChange={e=>onChange(rows.map((x,j)=>j===i?{...x,field:e.target.value}:x))}/><select aria-label={label+'类型 '+(i+1)} value={r.type} onChange={e=>onChange(rows.map((x,j)=>j===i?{...x,type:e.target.value}:x))}>{['any','string','number','boolean','object','array','null'].map(t=><option key={t}>{t}</option>)}</select><label><input type="checkbox" checked={r.required} onChange={e=>onChange(rows.map((x,j)=>j===i?{...x,required:e.target.checked}:x))}/>必需</label><button onClick={()=>onChange(rows.filter((_,j)=>j!==i))}>移除</button></div>)}<button disabled={maxRows!=null&&rows.length>=maxRows} onClick={()=>onChange([...rows,{field:'result.field'+(rows.length+1),type:'any',required:false}])}>声明{label}字段</button></details>
}
