"""Editable executable v3 examples populated from actual approved native profiles."""
from .node_defaults import profile_timeout


def examples(profiles):
    names=list(profiles);first=next((k for k in names if profiles[k]['provider']=='codex'),names[0])
    second=next((k for k in names if profiles[k]['provider']=='claude'),names[-1])
    def node(key,kind,name,x,y,**data):return dict(id=key,kind=kind,name=name,position={'x':x,'y':y},**data)
    def agent(key,name,profile,x,y):
        p=profiles[profile]
        return node(key,'agent',name,x,y,provider=p['provider'],profile=profile,model=p['model'],effort=p['effort'],
            role='通用 Agent',responsibility=name,mode='Ask',session='new',subagents='disabled',timeout=profile_timeout(p),task_context=True,
            initial_prompt='理解本次工作流的目标和已授权边界。',round_prompt='完成当前任务，参考上游提供的结果。',
            handoff_prompt='提供简明回答。摘要只写要点，产物、依据和未解决问题仅在适用时填写；避免重复长文，不虚构工具执行。')
    def edges(pairs):return [dict(id='edge'+str(i),source=s,target=t,transfer='result') for i,(s,t) in enumerate(pairs)]
    def graph(name,nodes,pairs):return dict(version=3,name=name,nodes=nodes,edges=edges(pairs),budget={'calls':24,'seconds':3600})
    sequential=graph('两 Agent 顺序分析',[node('start','start','任务输入',50,180),agent('analyze','分析任务',first,320,180),
        agent('review','复核与补充',second,620,180),node('result','result','结果',920,180)],
        [('start','analyze'),('analyze','review'),('review','result')])
    parallel=graph('并发分析与汇总',[node('start','start','任务输入',40,220),agent('branchA','可行性分析',first,310,90),
        agent('branchB','风险分析',second,310,360),node('merge','merge','等待两路结果',610,220),
        agent('summary','汇总建议',first,880,220),node('result','result','结果',1160,220)],
        [('start','branchA'),('start','branchB'),('branchA','merge'),('branchB','merge'),('merge','summary'),('summary','result')])
    body=graph('提案—审核循环体',[node('start','start','本轮输入',40,200),agent('proposal','提出或修订方案',first,300,200),
        agent('review','独立检查方案',second,600,200),node('result','result','本轮结果',900,200)],
        [('start','proposal'),('proposal','review'),('review','result')])
    body['nodes'][2]['handoff_prompt']='独立检查上游方案。满足任务标准时 accepted=true，否则 accepted=false 并说明必须修正的问题。不要为结束循环而改判。'
    loop=graph('提案—审核（最多五轮）',[node('start','start','任务输入',40,200),
        node('cycle','loop','提案与审核',340,200,body=body,max_rounds=5,until={'field':'result.accepted','op':'equals','value':True}),
        node('result','result','已通过的方案',710,200)],[('start','cycle'),('cycle','result')])
    router=node('route','router','选择任务分支',300,260,routes=[
        dict(label=label,test=dict(field='result.task',op='contains',value=text))
        for label,text in [('code','代码'),('research','研究'),('writing','写作')]],default_route='default')
    routing=graph('按任务分派 Agent',[node('start','start','任务输入',30,260),router,
        agent('code','代码分析',first,610,20),agent('research','研究分析',second,610,240),
        agent('writing','写作分析',second,610,460),agent('default','默认分析',first,610,680),
        node('result','result','已选择分支的结果',930,300)], [('start','route'),
        *[('route',k) for k in ('code','research','writing','default')],*[ (k,'result') for k in ('code','research','writing','default')]])
    for edge in routing['edges']:
        if edge['source']=='route':edge['route']=edge['target']
    single=graph('单 Agent 快速任务',[node('start','start','任务输入',50,180),agent('agent','处理任务',first,350,180),node('result','result','结果',650,180)],[('start','agent'),('agent','result')])
    complex_graph=graph('综合协作：并行、审核与有界修订',[
        node('start','start','任务输入',40,220),agent('feasibility','方案分析',first,280,80),agent('risks','风险检查',second,280,360),
        node('merge','merge','汇合两路意见',550,220),agent('judge','独立判断是否完整',first,820,220),
        node('check','condition','是否满足标准',1090,220,test={'field':'result.accepted','op':'equals','value':True}),
        node('revise','loop','修订与复审（最多两轮）',1370,380,body=body,max_rounds=2,until={'field':'result.accepted','op':'equals','value':True}),
        node('result','result','本轮结果',1690,220)],
        [('start','feasibility'),('start','risks'),('feasibility','merge'),('risks','merge'),('merge','judge'),('judge','check'),('check','result'),('check','revise'),('revise','result')])
    complex_graph['nodes'][4]['handoff_prompt']='检查两路意见是否回答了用户任务并有可检查的标准。完整时 accepted=true，否则 accepted=false 并列出需修正的问题；不为结束流程而改判。'
    for e in complex_graph['edges']:
        if e['source']=='check':e['when']='true' if e['target']=='result' else 'false'
    complex_graph['budget']={'calls':16,'seconds':1800}
    return [sequential,parallel,loop,routing,single,complex_graph]
