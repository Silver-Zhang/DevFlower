"""Exercise one CLI binary with a local synthetic provider before native exec.

The probe uses a throwaway HOME and never contacts a real model or account.
It checks that ignored config cannot introduce MCP, and that native session
writers are mutually exclusive. Failure means the capability is unavailable.
"""
from __future__ import annotations
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json,os,shlex,signal,subprocess,tempfile,threading,time,uuid
from pathlib import Path
from orchestration.common import Blocked,clean_env
from .native_codex_exec import exec_args,provider_overrides,permission_profile_args

_AUTOMATIC_DISABLED=('in_app_browser','hooks','plugins','apps','skill_mcp_dependency_install','multi_agent')

def _verify_disabled_features(executable,work,env,deadline,cancelled):
    args=[executable]
    for key in _AUTOMATIC_DISABLED:args+=['-c','features.'+key+'=false']
    result=_run_command([*args,'features','list'],cwd=work,env=env,deadline=deadline,cancelled=cancelled)
    if result.returncode:raise ValueError('CLI feature 检查失败: '+_stderr(result.stderr))
    rows={parts[0]:parts[-1] for line in result.stdout.decode('utf-8','replace').splitlines() if (parts:=line.split()) and len(parts)>=3}
    missing=[key for key in _AUTOMATIC_DISABLED if rows.get(key)!='false']
    if missing:raise ValueError('CLI 未确认关闭功能: '+', '.join(missing))

def _stderr(raw):
    # CLI diagnostics can include lengthy model-catalog warnings before the
    # actual sandbox error; retain the useful end of this synthetic probe.
    return raw.decode('utf-8','replace')[-2000:]

def _remaining(deadline,cancelled):
    if cancelled():raise Blocked('原生调用已取消；未自动重发')
    value=deadline-time.monotonic()
    if value<=0:raise Blocked('原生调用达到本次等待预算；未自动重发')
    return value

def _kill(process):
    if process.poll() is None:
        try:os.killpg(process.pid,signal.SIGKILL)
        except ProcessLookupError:pass

def _collect(process,deadline,cancelled,input_data=None):
    try:
        while True:
            try:return process.communicate(input=input_data,timeout=min(.1,_remaining(deadline,cancelled)))
            except subprocess.TimeoutExpired:input_data=None
    except BaseException:
        _kill(process)
        process.communicate()
        raise

def _run_command(args,*,cwd,env,input_data=None,deadline,cancelled):
    process=subprocess.Popen(args,cwd=cwd,env=env,stdin=subprocess.PIPE if input_data is not None else subprocess.DEVNULL,
                             stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
    out,err=_collect(process,deadline,cancelled,input_data)
    return type('ProbeResult',(),dict(returncode=process.returncode,stdout=out,stderr=err))()

def _probe(executable,stamp,root,deadline,cancelled,*,no_tools=False,ordinary_profile=False):
    calls=[];hold=threading.Event();waiting=threading.Event();marker=None
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            try:
                n=int(self.headers.get('Content-Length','0'))
                if n<1 or n>2000000:raise ValueError()
                body=json.loads(self.rfile.read(n));calls.append(body)
                if len(calls)>1:
                    waiting.set()
                    # Keep the first writer alive until the refusal has been
                    # checked. Slow CLI startup must not accidentally release
                    # its lock and turn this into two sequential writers.
                    hold.wait(max(0,deadline-time.monotonic()))
                msg={'id':'msg','type':'message','status':'completed','role':'assistant',
                     'content':[{'type':'output_text','text':'synthetic','annotations':[]}]}
                response={'id':'resp','object':'response','created_at':0,'status':'completed','model':'fixture',
                          'output':[msg],'usage':{'input_tokens':1,'output_tokens':1,'total_tokens':2}}
                self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
                for event in ({'type':'response.created','response':dict(response,status='in_progress',output=[])},
                              {'type':'response.output_item.added','output_index':0,'item':dict(msg,status='in_progress',content=[])},
                              {'type':'response.output_item.done','output_index':0,'item':msg},
                              {'type':'response.completed','response':response}):
                    self.wfile.write(('data: '+json.dumps(event)+'\n\n').encode());self.wfile.flush()
            except (OSError,ValueError,TypeError):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.daemon_threads=True
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();second=None;third=None
    try:
        with tempfile.TemporaryDirectory(prefix='df-codex-probe-',dir=root) as folder:
            base=Path(folder);home=base/'home';home.mkdir();work=base/'workspace';work.mkdir();(work/'.codex').mkdir()
            marker=base/'mcp-started';helper=base/'mcp.py';helper.write_text('from pathlib import Path\nPath('+repr(str(marker))+').write_text("started")\n')
            (home/'config.toml').write_text('[projects.'+json.dumps(str(work))+']\ntrust_level="trusted"\n')
            (work/'.codex/config.toml').write_text('[mcp_servers.unselected]\ncommand="/usr/bin/python3"\nargs=['+json.dumps(str(helper))+']\n')
            provider={'model_provider':'fixture','model_providers':{'fixture':{'name':'fixture','base_url':'http://127.0.0.1:'+str(server.server_port)+'/v1','wire_api':'responses','requires_openai_auth':False}}}
            binding={'executable':executable,'role':'reviewer','model':'fixture','effort':'high','no_tools':no_tools}
            if ordinary_profile:
                fake_home=base/'protected-native-home';fake_home.mkdir();(fake_home/'auth.json').write_text('{}');(base/'authority').mkdir();(base/'authority'/'settings.json').write_text('{}')
                binding.update(native_interaction='ordinary',native_protected=[str(fake_home),str(fake_home/'auth.json'),str(base/'authority'),str(base/'authority'/'settings.json')])
            argv=exec_args(binding,provider_overrides(provider));env=clean_env(base);env.update(CODEX_HOME=str(home),TMPDIR=str(base),NO_PROXY='127.0.0.1,localhost')
            if ordinary_profile:_verify_disabled_features(executable,work,env,deadline,cancelled)
            # Verify the adversarial project is actually trusted and its MCP
            # starts without the isolation flag; otherwise the negative would
            # not establish that --ignore-user-config suppressed that layer.
            control=list(argv);control.remove('--ignore-user-config')
            check=_run_command(control,cwd=work,env=env,input_data=b'synthetic',deadline=deadline,cancelled=cancelled)
            if check.returncode:raise ValueError('CLI 对照探测 exit '+str(check.returncode)+': '+_stderr(check.stderr))
            if not marker.exists() or len(calls)!=1:raise ValueError('未验证受信任项目的工具配置对照')
            marker.unlink();calls.clear()
            first=_run_command(argv,cwd=work,env=env,input_data=b'synthetic',deadline=deadline,cancelled=cancelled)
            if first.returncode:raise ValueError('CLI 配置探测 exit '+str(first.returncode)+': '+_stderr(first.stderr))
            if marker.exists() or len(calls)!=1:raise ValueError('未验证工具配置隔离')
            tools=calls[0].get('tools');allowed={'request_user_input'} if no_tools else {'exec_command','write_stdin','request_user_input'}
            if (not isinstance(tools,list) or any(not isinstance(t,dict) or t.get('type')!='function' or t.get('name') not in allowed for t in tools)
                or not no_tools and not {'exec_command','write_stdin'} <= {t.get('name') for t in tools}):raise ValueError('unexpected tool exposure')
            events=[json.loads(x) for x in first.stdout.splitlines() if x.strip()]
            starts=[x for x in events if x.get('type')=='thread.started'];ends=[x for x in events if x.get('type')=='turn.completed']
            if len(starts)!=1 or len(ends)!=1:raise ValueError('invalid probe transcript')
            sid=starts[0]['thread_id']
            if str(uuid.UUID(sid))!=sid:raise ValueError('invalid thread identity')
            resume=[executable,'exec','resume',*argv[2:-1],sid,'synthetic']
            second=subprocess.Popen(resume,cwd=work,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
            while not waiting.wait(min(.1,_remaining(deadline,cancelled))):
                if second.poll() is not None:raise ValueError('resume did not reach synthetic provider')
            from .native_local import native_busy
            if not native_busy({'provider':'codex','home':str(base),'config_root':str(home)},sid):raise ValueError('native writer lock is not held')
            third=subprocess.Popen(resume,cwd=work,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
            try:_collect(third,min(deadline,time.monotonic()+20),cancelled)
            except Blocked:
                _remaining(deadline,cancelled)
                raise ValueError('concurrent native writer was not refused') from None
            if third.returncode==0 or len(calls)!=2:raise ValueError('concurrent native writer was accepted')
            hold.set();_collect(second,deadline,cancelled)
            if second.returncode or marker.exists():raise ValueError('native resume probe failed')
            # The executable must not have changed while it was being verified.
            st=os.stat(executable)
            if (st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns,st.st_ctime_ns)!=stamp:raise ValueError('native executable changed')
            return True
    except (OSError,ValueError,KeyError,TypeError,subprocess.TimeoutExpired,json.JSONDecodeError) as exc:
        raise Blocked('原生 Codex 配置或同会话写锁能力未通过本机探测；未调用模型：'+str(exc)[-2200:]) from None
    finally:
        hold.set()
        for process in (second,third):
            if process and process.poll() is None:
                _kill(process);process.communicate(timeout=5)
        server.shutdown();server.server_close();thread.join(timeout=2)

def require_exec_capability(executable,root,*,timeout=90,cancelled=lambda:False,no_tools=False,ordinary_profile=False):
    try:
        path=str(Path(executable).resolve(strict=True));st=os.stat(path)
        stamp=(st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns,st.st_ctime_ns)
        return _probe(path,stamp,str(root),time.monotonic()+timeout,cancelled,no_tools=no_tools,ordinary_profile=ordinary_profile)
    except OSError:raise Blocked('无法验证原生 Codex 可执行文件') from None

def require_sandbox_capability(executable,root,write,*,timeout=30,cancelled=lambda:False):
    """Exercise actual native OS policy in an isolated throwaway workspace."""
    return _sandbox_capability(executable,root,write,timeout=timeout,cancelled=cancelled,ordinary=False)

def require_native_write_capability(executable,root,write,*,timeout=30,cancelled=lambda:False):
    """Verify native write boundaries without claiming external reads are hidden."""
    return _sandbox_capability(executable,root,write,timeout=timeout,cancelled=cancelled,ordinary=True)

def _sandbox_result(value,write,ordinary):
    if not isinstance(value,list) or any(type(x) is not bool for x in value):return False
    # Existing aliases may bypass path deny. The separate host-side inode
    # preflight rejects them; the sandbox must prevent creating new aliases.
    if ordinary:return len(value)==17 and value[0] and value[1]==bool(write) and not value[2] and value[3] and value[4]==bool(write) and value[5] and not any(value[6:12]) and not any(value[14:16]) and value[16]==bool(write)
    if len(value)!=6:return False
    return value==[True,bool(write),False,False,False,False]

def _matrix_detail(values,ordinary):
    names=['读取工作区','写入工作区','写入外部','读取外部','写入项目记录','读取项目记录',
           '读取权威','写入权威','读取认证','经软链接读取认证','经软链接读取权威','经软链接写入权威',
           '经硬链接读取认证','经硬链接读取权威','创建认证硬链接','创建权威硬链接','创建普通硬链接'] if ordinary else ['读取工作区','写入工作区','写入外部','读取外部','写入项目记录','读取项目记录']
    return json.dumps(dict(zip(names,values)) if isinstance(values,list) and len(values)==len(names) else values,ensure_ascii=False)

def _sandbox_capability(executable,root,write,*,timeout,cancelled,ordinary):
    deadline=time.monotonic()+timeout
    try:
        with tempfile.TemporaryDirectory(prefix='df-codex-sandbox-',dir=root) as folder:
            base=Path(folder);home=base/'home';home.mkdir();work=base/'work';work.mkdir()
            (work/'input').write_text('fixture');outside=base/'outside';outside.mkdir();(outside/'input').write_text('private-fixture');reserved=work/'.devflower';reserved.mkdir();(reserved/'input').write_text('private-fixture')
            authority=work/'authority';authority.mkdir();(authority/'input').write_text('synthetic');fake_home=base/'protected-native-home';fake_home.mkdir();auth=fake_home/'auth.json';auth.write_text('{}')
            if ordinary:
                (work/'auth-link').symlink_to(auth);(work/'authority-link').symlink_to(authority,target_is_directory=True)
                os.link(auth,work/'auth-hardlink');os.link(authority/'input',work/'authority-hardlink')
            program=('import json,os,sys\n'
                     'r=open("input").read()=="fixture"\n'
                     'def attempt(p):\n'
                     ' try:\n  f=open(p,"w");f.write("x");f.close();return True\n'
                     ' except OSError:return False\n'
                     'def canread(p):\n'
                     ' try:\n  open(p).read();return True\n'
                     ' except OSError:return False\n'
                     'values=[r,attempt("created"),attempt(sys.argv[1]),canread(sys.argv[2]),attempt(".devflower/created"),canread(".devflower/input")]\n')
            if ordinary:program+=('def canlink(src,dest):\n try:\n  os.link(src,dest);return True\n except OSError:return False\n'
                                 'values += [canread(sys.argv[3]),attempt(sys.argv[4]),canread(sys.argv[5]),canread("auth-link"),canread("authority-link/input"),attempt("authority-link/created"),canread("auth-hardlink"),canread("authority-hardlink"),canlink(sys.argv[5],"new-auth-alias"),canlink(sys.argv[3],"new-authority-alias"),canlink("input","new-public-alias")]\n')
            program+='print(json.dumps(values))\n'
            denied=([str(authority),str(authority/'input'),str(fake_home),str(auth)] if ordinary else
                    [str(authority),str(auth),str(outside),str(reserved)])
            args=[executable,*permission_profile_args(denied,write,select_default=False),'sandbox','--permission-profile','devflower',
                  '-C',str(work),'--','/usr/bin/python3','-c',program,str(outside/'created'),str(outside/'input'),str(authority/'input'),str(authority/'created'),str(auth)]
            env=clean_env(base);env.update(CODEX_HOME=str(home),TMPDIR=str(base))
            result=_run_command(args,cwd=work,env=env,deadline=deadline,cancelled=cancelled)
            if result.returncode:raise ValueError('CLI exit '+str(result.returncode)+': '+_stderr(result.stderr))
            values=json.loads(result.stdout)
            if not _sandbox_result(values,write,ordinary):raise ValueError('实际权限矩阵不匹配：'+_matrix_detail(values,ordinary))
            if ordinary:
                (work/'new-public-alias').unlink(missing_ok=True)
                _tool_boundary_probe(executable,base,home,work,outside,program,write,time.monotonic()+min(timeout,_remaining(deadline,cancelled)),cancelled,ordinary=True)
            else:
                _tool_boundary_probe(executable,base,home,work,outside,program,write,time.monotonic()+min(timeout,_remaining(deadline,cancelled)),cancelled)
            return True
    except (OSError,ValueError,TypeError,subprocess.TimeoutExpired) as exc:
        raise Blocked('原生 Codex 文件沙箱能力未通过本机探测；未调用模型：'+str(exc)[-2200:]) from None


def _tool_boundary_probe(executable,base,home,work,outside,program,write,deadline,cancelled,*,ordinary=False):
    # Test the actual model-visible shell tool, not just a separate sandbox CLI.
    # The server is local and synthetic; no user prompt or account is sent.
    calls=[]
    source=program.replace('print(json.dumps(',"print('DFPROBE:'+json.dumps(")
    fake_auth=base/'protected-native-home/auth.json'
    command=shlex.join(['/usr/bin/python3','-c',source,str(outside/'tool-created'),str(outside/'input'),str(work/'authority/input'),str(work/'authority/tool-created'),str(fake_auth)])
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            try:
                size=int(self.headers.get('Content-Length','0'))
                if size<1 or size>2000000:raise ValueError()
                request=json.loads(self.rfile.read(size));calls.append(request)
                if len(calls)>2:raise ValueError()
                if len(calls)==1:
                    item=dict(id='fc',type='function_call',status='completed',name='exec_command',call_id='call',arguments=json.dumps({'cmd':command}))
                else:
                    item=dict(id='msg',type='message',status='completed',role='assistant',content=[dict(type='output_text',text='synthetic',annotations=[])])
                response=dict(id='resp'+str(len(calls)),object='response',created_at=0,status='completed',model='fixture',output=[item],usage=dict(input_tokens=1,output_tokens=1,total_tokens=2))
                self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
                for event in (dict(type='response.created',response=dict(response,status='in_progress',output=[])),
                              dict(type='response.output_item.added',output_index=0,item=item),
                              dict(type='response.output_item.done',output_index=0,item=item),
                              dict(type='response.completed',response=response)):
                    self.wfile.write(('data: '+json.dumps(event)+'\n\n').encode());self.wfile.flush()
            except (OSError,ValueError,TypeError):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.daemon_threads=True
    worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    try:
        config=dict(model_provider='fixture',model_providers={'fixture':dict(name='fixture',base_url='http://127.0.0.1:'+str(server.server_port)+'/v1',wire_api='responses',requires_openai_auth=False)})
        binding=dict(executable=executable,role='implementer' if write else 'reviewer',model='fixture',effort='high')
        if ordinary:binding.update(native_interaction='ordinary',native_protected=[str(work/'authority'),str(work/'authority/input'),str(fake_auth.parent),str(fake_auth)])
        env=clean_env(base);env.update(CODEX_HOME=str(home),TMPDIR=str(base),NO_PROXY='127.0.0.1,localhost')
        result=_run_command(exec_args(binding,provider_overrides(config)),cwd=work,env=env,input_data=b'synthetic',deadline=deadline,cancelled=cancelled)
        if result.returncode:raise ValueError('实际工具探测 CLI exit '+str(result.returncode)+': '+_stderr(result.stderr))
        if len(calls)!=2:raise ValueError('实际工具探测缺少合成 Provider 的两次往返')
        outputs=[v.get('output') for v in calls[1].get('input',[]) if isinstance(v,dict) and v.get('type')=='function_call_output' and v.get('call_id')=='call']
        import re
        match=re.search(r'DFPROBE:(\[[^\n]*\])',outputs[0]) if len(outputs)==1 and isinstance(outputs[0],str) else None
        if not match or 'Process exited with code 0' not in outputs[0]:raise ValueError('实际工具没有返回完整权限探测结果')
        if not _sandbox_result(json.loads(match.group(1)),write,ordinary):raise ValueError('实际工具权限矩阵不匹配：'+_matrix_detail(json.loads(match.group(1)),ordinary))
        expected=match.group(0)
        events=[json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        visible=[e.get('item') for e in events if isinstance(e,dict) and e.get('type')=='item.completed' and isinstance(e.get('item'),dict) and e['item'].get('type')=='command_execution']
        if not any(item.get('exit_code')==0 and expected in str(item.get('aggregated_output','')) for item in visible):raise ValueError('native tool events unavailable')
    finally:
        server.shutdown();server.server_close();worker.join(timeout=2)
