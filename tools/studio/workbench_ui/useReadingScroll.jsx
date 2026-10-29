import {useLayoutEffect,useRef,useState} from 'react';

// Follow output only while the reader is at the bottom. A poll must never
// steal the position of somebody reading an earlier message.
export function useReadingScroll(identity,revision,empty=false){
 const ref=useRef(null),following=useRef(true),previous=useRef(identity);
 const [unread,setUnread]=useState(false);
 const onScroll=()=>{const el=ref.current;if(!el)return;following.current=el.scrollHeight-el.clientHeight-el.scrollTop<64;if(following.current)setUnread(false)};
 const latest=()=>{const el=ref.current;following.current=true;setUnread(false);if(el)el.scrollTop=el.scrollHeight};
 useLayoutEffect(()=>{
  const el=ref.current;if(!el)return;
  if(previous.current!==identity){previous.current=identity;following.current=true;setUnread(false)}
  if(empty){el.scrollTop=0;following.current=true;setUnread(false)}
  else if(following.current)el.scrollTop=el.scrollHeight;
  else setUnread(true);
 },[identity,revision,empty]);
 return {ref,onScroll,unread,latest};
}
