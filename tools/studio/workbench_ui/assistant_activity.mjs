// A source-tool start event stays in the record after its result arrives.
// Only a start without a later result, inside a turn that has not finished,
// may read as 进行中.
export function activityLabel(item,{resolved=false,turnFinished=false}={}){
 const action=item.name==='search_public_source'?'公开源码搜索':'公开源码读取';
 if(item.state==='started')return action+(resolved?' · 已完成':turnFinished?' · 已结束，未返回结果':' · 进行中');
 if(item.state==='failed')return action+' · 失败：'+(item.error||'未能完成');
 if(item.name==='search_public_source')return action+' · '+(item.count??0)+' 条命中';
 return item.path?`${item.path} · ${item.start??'?'}–${item.end??'?'} 行`:action+' · 已完成';
}
export function activityResolved(items,index){
 const item=items?.[index];
 if(!item)return false;
 return items.slice(index+1).some(next=>next.name===item.name&&next.state!=='started');
}
