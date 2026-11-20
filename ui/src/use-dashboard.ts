import {useCallback,useEffect,useRef,useState} from 'react';
import {HubConnectionBuilder,LogLevel} from '@microsoft/signalr';
import {appendHistory,interruptHistory,parseSnapshot,type Snapshot,type History} from './data';
import {readSession} from './session';
export function useDashboard(){
 const [auth,setAuth]=useState<'checking'|'locked'|'ready'>('checking');const [snapshot,setSnapshot]=useState<Snapshot|null>(null);const [history,setHistory]=useState<History>({});const [error,setError]=useState<string|null>(null);const [transport,setTransport]=useState('연결 대기');const [connected,setConnected]=useState(false);const [now,setNow]=useState(Date.now());const [revision,setRevision]=useState(0);
 const latest=useRef(-Infinity);
 const clock=useRef({utc:Date.now(),tick:performance.now()});
 const readNow=useCallback(()=>{
  const tick=performance.now();
  const utc=Math.max(Date.now(),clock.current.utc+tick-clock.current.tick);
  clock.current={utc,tick};
  return utc;
 },[]);
 const [sessionUnavailable,setSessionUnavailable]=useState(false);
 const sessionRequest=useRef<AbortController|null>(null);
 const refresh=useCallback(()=>setRevision(v=>v+1),[]);
 const receive=useCallback((raw:unknown,floor:number)=>{try{const value=parseSnapshot(raw);const stamp=Date.parse(value.generatedAtUtc);if(stamp<latest.current||stamp<=floor)return false;latest.current=stamp;setNow(readNow());setSnapshot(value);setHistory(old=>appendHistory(old,value));setError(null);return true;}catch{setError('데이터 형식을 확인할 수 없습니다. 마지막 수신 이력을 표시합니다.');setTransport('데이터 오류');return false;}},[readNow]);
 const recheckSession=useCallback(async()=>{
  if(sessionRequest.current&&!sessionRequest.current.signal.aborted)return;
  const controller=new AbortController();sessionRequest.current=controller;
  setAuth('checking');setError(null);
  try{
   const status=await readSession(controller.signal);
   if(controller.signal.aborted)return;
   setSessionUnavailable(false);setAuth(status);
   setError(status==='locked'?'로컬 접속 토큰을 입력하세요.':null);
  }catch{
   if(!controller.signal.aborted){setAuth('locked');setSessionUnavailable(true);setError('로컬 게이트웨이에 연결할 수 없습니다.');}
  }finally{if(sessionRequest.current===controller)sessionRequest.current=null;}
 },[]);
 useEffect(()=>{
  void recheckSession();
  return()=>{const pending=sessionRequest.current;sessionRequest.current=null;pending?.abort();};
 },[recheckSession]);
 useEffect(()=>{const interval=setInterval(()=>setNow(readNow()),250);return()=>clearInterval(interval);},[readNow]);
 useEffect(()=>{
  if(auth!=='ready')return;let active=true;setConnected(false);setHistory(old=>interruptHistory(old));const controller=new AbortController();const connection=new HubConnectionBuilder().withUrl('/hubs/dashboard',{withCredentials:true}).withAutomaticReconnect([0,2000,5000,10000]).configureLogging(LogLevel.None).build();
  let generation=0;let streaming=false;let floor=latest.current;
  const apply=(raw:unknown)=>{if(receive(raw,floor)&&streaming){setConnected(true);setTransport('실시간 연결');}};
  const load=async()=>{
   const requestGeneration=generation;const before=latest.current;
   const current=()=>active&&generation===requestGeneration;
   try {
    const response=await fetch('/api/dashboard',{credentials:'same-origin',cache:'no-store',signal:controller.signal});
    if(!current())return;
    if(response.status===401){setAuth('locked');throw Error('세션이 만료되었습니다.');}
    if(!response.ok)throw Error('데이터를 불러올 수 없습니다.');
    const data:unknown=await response.json();
    if(current())apply(data);
   }catch(e){if(current()&&latest.current===before&&e instanceof Error&&e.name!=='AbortError')setError(e.message);}
  };
  const interrupt=(label:string)=>{generation++;streaming=false;floor=latest.current;setConnected(false);setHistory(old=>interruptHistory(old));setTransport(label);};
  const resume=()=>{if(active){generation++;streaming=true;void load();}};
  connection.on('snapshot',(raw:unknown)=>{if(active&&streaming)apply(raw);});
  connection.onreconnecting(()=>{if(active)interrupt('재연결 중 · 이력');});
  connection.onreconnected(resume);
  connection.onclose(()=>{if(active)interrupt('연결 종료 · 이력');});
  void connection.start().then(resume).catch(()=>{if(active){interrupt('실시간 연결 불가 · 재시도 필요');setError('실시간 연결을 시작하지 못했습니다. 새로고침으로 다시 연결하세요.');}});void load();
  return()=>{active=false;controller.abort();void connection.stop();};
 },[auth,revision,receive]);
 const unlock=async(token:string)=>{
  if(sessionRequest.current&&!sessionRequest.current.signal.aborted)throw Error('접속 확인이 진행 중입니다.');
  const controller=new AbortController();sessionRequest.current=controller;
  try{
   const r=await fetch('/api/session',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({token}),signal:controller.signal});
   controller.signal.throwIfAborted();
   if(!r.ok)throw Error(r.status===401?'로컬 접속 토큰을 확인하세요.':'게이트웨이 접속을 확인하세요.');
   setSessionUnavailable(false);setError(null);setAuth('ready');
  }finally{if(sessionRequest.current===controller)sessionRequest.current=null;}
 };
 return {auth,snapshot,history,error,transport,connected,now,refresh,unlock,recheckSession,sessionUnavailable};
}
