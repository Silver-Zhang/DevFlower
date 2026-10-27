"""Bounded literal search and excerpts from the shipped public source manifest."""
import json,os,time
from pathlib import Path,PurePosixPath
from .session_sources import opened
from .agent_sessions import safe_text
from .graph import Invalid

ROOT=Path(__file__).resolve().parents[2]
with opened(ROOT/'tools/studio/public_source_manifest.json') as fd:
    _manifest=json.loads(os.read(fd,100001))
_LAUNCHERS=frozenset('tools/'+name for name in ('devflower.py','devflower_attach.py','devflower_copilot.py','devflower_native_terminal.py','devflower_sessions.py','devflower_stable_bridge.py','devflower_studio.py','devflower_usable.py','devflower_workbench.py','review_handoff.py'))
if not isinstance(_manifest,list) or len(_manifest)>512 or len(set(_manifest))!=len(_manifest) or any(not isinstance(p,str) or PurePosixPath(p).is_absolute() or '..' in PurePosixPath(p).parts or not (p.startswith(('tools/studio/','tools/orchestration/')) and p.endswith('.py') or p.startswith('tools/studio/workbench_ui/') and p.endswith(('.jsx','.mjs','.css')) or p.startswith('docs/') and p.endswith('.md') or p in _LAUNCHERS) for p in _manifest):
    raise Invalid('公开源码清单无效')
PUBLIC_FILES=frozenset(_manifest)
class PublicSourceReader:
    def __init__(self,rt,cancelled=lambda:False,deadline=None):
        self.rt=rt;self.calls=0;self.bytes=0;self.cancelled=cancelled;self.deadline=min(time.monotonic()+60,deadline or float('inf'))
    def _budget(self):
        if self.cancelled():raise Invalid('公开源码查询已取消。')
        if self.calls>=3 or time.monotonic()>self.deadline:raise Invalid('本轮公开源码读取预算已用完。')
    def _open(self,path):
        if not isinstance(path,str) or path not in PUBLIC_FILES:raise Invalid('只允许读取公开源码清单中的文件。')
        with opened(ROOT/path) as fd:
            if os.fstat(fd).st_size>250000:raise Invalid('公开文件超过读取预算。')
            raw=os.read(fd,250001)
        if len(raw)>250000:raise Invalid('公开文件超过读取预算。')
        try:return raw.decode('utf-8').splitlines(),len(raw)
        except UnicodeDecodeError:raise Invalid('公开文件不是 UTF-8 文本。') from None
    def read(self,path,start,end):
        self._budget()
        if type(start) is not int or type(end) is not int or not 1<=start<=end<=10000 or end-start>=100:raise Invalid('每次最多读取100行。')
        self.rt.check_operator();lines,_=self._open(path);self.rt.check_operator()
        text=safe_text('\n'.join(f'{i}: {line}' for i,line in enumerate(lines[start-1:end],start)))
        if len(text.encode())>12000 or self.bytes+len(text.encode())>24000:raise Invalid('本轮源码摘录超过字节预算，请缩小行范围。')
        self.calls+=1;self.bytes+=len(text.encode())
        return dict(path=path,start=start,end=min(end,len(lines)),text=text,candidate_sha256=self.rt.candidate_sha256)
    def search(self,query):
        self._budget()
        if not isinstance(query,str) or not 3<=len(query.strip())<=120 or '\n' in query or '\r' in query:raise Invalid('请输入3–120字的单行原文搜索词。')
        self.rt.check_operator();matches=[];scanned=0;end=min(self.deadline,time.monotonic()+3);needle=query.casefold()
        for path in sorted(PUBLIC_FILES):
            if self.cancelled():raise Invalid('公开源码查询已取消。')
            if time.monotonic()>end:raise Invalid('公开源码搜索超时，请缩短搜索范围。')
            lines,size=self._open(path);scanned+=size
            if scanned>8000000:raise Invalid('公开源码搜索超出读取预算。')
            for number,line in enumerate(lines,1):
                if needle in line.casefold():
                    matches.append(dict(path=path,line=number,text=safe_text(line[:400])))
                    if len(matches)>=12:break
            if len(matches)>=12:break
        self.rt.check_operator();encoded=json.dumps(matches,ensure_ascii=False).encode()
        if self.bytes+len(encoded)>24000:raise Invalid('本轮源码摘录超过字节预算。')
        self.calls+=1;self.bytes+=len(encoded)
        return dict(matches=matches,limited=len(matches)>=12,candidate_sha256=self.rt.candidate_sha256)
