import types from '../canvas_types.json';

export const nodeKind = node => node.kind ?? 'agent';
export const nodeType = node => types[nodeKind(node)];
export const defaultTransfer = Object.freeze({mode:'answer',max_chars:16000});
export const transferModes = {answer:'最终回答',summary:'摘要',result:'结构化结果',none:'仅控制顺序'};
export const emptyPredicate = {field:'',op:'equals',value:true};

export function ports(node, direction) {
 const values=Object.entries(nodeType(node)[direction]);
 if(direction==='outputs'&&nodeKind(node)==='router')
  return [...(node.config.rules||[]).map(rule=>[rule.id,rule.label.trim()||'未命名分支']),...values];
 return values;
}

export function connectionIssue(graph, edge, except=null) {
 const from=graph.nodes.find(node=>node.id===edge.source),to=graph.nodes.find(node=>node.id===edge.target);
 const output=edge.source_port??'out',input=edge.target_port??'in';
 if(!from||!to)return '连线端点缺失，请选择画布中的节点。';
 if(from===to)return '节点不能连接到自身。';
 if(!ports(from,'outputs').some(([id])=>id===output))return '源节点没有所选输出端口。';
 if(!ports(to,'inputs').some(([id])=>id===input))return '目标节点没有所选输入端口。';
 const edges=graph.edges.filter(item=>item.id!==except);
 const sameSource=item=>item.source===from.id&&(item.source_port??'out')===output;
 const sameTarget=item=>item.target===to.id&&(item.target_port??'in')===input;
 if(edges.some(item=>sameSource(item)&&sameTarget(item)))return '这些端口之间已有连线。';
 if(nodeKind(to)!=='merge'&&edges.some(sameTarget))return '此输入端口只接收一条连线。需要多个上游时请使用汇合节点。';
 if(!['start','agent'].includes(nodeKind(from))&&edges.some(sameSource))return '此输出端口只允许一条连线，请选择其他分支端口。';
 const pending=[to.id],seen=new Set();
 while(pending.length){
  const id=pending.pop();
  if(id===from.id)return '这条连线会形成循环。请用循环节点配置迭代。';
  if(!seen.has(id)){
   seen.add(id);
   pending.push(...edges.filter(item=>item.source===id).map(item=>item.target));
  }
 }
 return '';
}

export function defaultConfig(kind) {
 return Object.fromEntries(Object.entries(types[kind].fields).map(([key,field])=>[
  key,field.default??(field.type==='predicate'?{...emptyPredicate}:'')
 ]));
}

export function draftConfigIssue(node) {
 const kind=nodeKind(node);
 if(kind==='agent')return node.agent_id||node.provider&&node.model&&node.effort&&node.round_prompt?.trim()?'':'请配置 Agent';
 const config=node.config;
 for(const [key,field] of Object.entries(types[kind].fields)){
  const value=config[key]??field.default;
  if(field.type==='predicate'){
   if(!value?.field.trim())return '请填写'+field.label+'的判定字段';
   if(['greater','less'].includes(value.op)&&typeof value.value!=='number')return '数值比较需要数字比较值';
  }else if(field.required&&!value?.trim())return '请填写'+field.label;
 }
 if(kind==='start'&&config.initial_data?.trim()){
  try{JSON.parse(config.initial_data)}catch{return '初始数据需要有效 JSON'}
 }
 if(kind==='router'){
  const rules=config.rules||[];
  if(!rules.length)return '请添加路由规则';
  if(rules.some(rule=>!rule.label.trim()||!rule.test.field.trim()))return '请填写路由分支名称与字段';
 }
 if(kind==='tool'&&config.tool){
  const permitted=config.tool==='read-file'?['workspace-read','workspace-write']:['workspace-write'];
  if(!permitted.includes(config.permission))return '选择与工具一致的工作区权限';
 }
 return '';
}

export const canvasTypes=types;
