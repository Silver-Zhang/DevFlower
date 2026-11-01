import React,{createContext,useContext,useState} from 'react';
import RawMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
const Context=createContext({});
export function DocumentLinkProvider({api,workspace,children}){return <Context.Provider value={{api,workspace}}>{children}</Context.Provider>}
function Link({href,children}){
 const {api,workspace}=useContext(Context),[value,setValue]=useState(null),[error,setError]=useState(''),[copied,setCopied]=useState(false);
 if(/^https?:\/\//i.test(href||''))return <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>;
 async function resolve(e){e.preventDefault();if(!api||!workspace){setError('先打开对应工作区');return}try{setValue(await api('/api/file-reference?workspace='+workspace.id+'&reference='+encodeURIComponent(href)));setError('')}catch(e){setError(e.message)}}
 return <span className="document-reference"><a href="#" onClick={resolve}>{children}</a>{value&&<span className="document-open"><a href={'vscode://file'+value.path.split('/').map(encodeURIComponent).join('/')+':'+value.line} onClick={()=>setValue(null)}>在 VS Code 打开</a><button onClick={async()=>{try{await navigator.clipboard.writeText(value.path);setCopied(true)}catch{setError(value.path)}}}>{copied?'已复制':'复制路径'}</button><button aria-label="关闭文件打开选项" onClick={()=>setValue(null)}>×</button></span>}{error&&<small role="alert">{error}</small>}</span>
}
export default function Markdown(props){return <RawMarkdown remarkPlugins={[remarkGfm]} {...props} skipHtml urlTransform={v=>v} components={{...props.components,a:Link}}/>}
