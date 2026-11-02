import {useCallback,useEffect,useRef,useState} from 'react';
import {HubConnectionBuilder,LogLevel} from '@microsoft/signalr';
import {appendHistory,interruptHistory,parseSnapshot,type Snapshot,type History} from './data';
import {readSession} from './session';
export function useDashboard(){
 const [auth,setAuth]=useState<'checking'|'locked'|'ready'>('checking');const [snapshot,setSnapshot]=useState<Snapshot|null>(null);const [history,setHistory]=useState<History>({});const [error,setError]=useState<string|null>(null);const [transport,setTransport]=useState('연결 대기');const [connected,setConnected]=useState(false);const [now,setNow]=useState(Date.now());const [revision,setRevision]=useState(0);
 const latest=useRef(-Infinity);
 const [sessionUnavailable,setSessionUnavailable]=useState(false);
 const sessionRequest=useRef<AbortController|null>(null);
 const refresh=useCallback(()=>setRevision(v=>v+1),[]);
 const receive=useCallback((raw:unknown)=>{try{const value=parseSnapshot(raw);const stamp=Date.parse(value.generatedAtUtc);if(stamp<latest.current)return;latest.current=stamp;setSnapshot(value);setHistory(old=>appendHistory(old,value));setError(null);}catch{setError('데이터 형식을 확인할 수 없습니다. 마지막 수신 이력을 표시합니다.');setTransport('데이터 오류');}},[]);
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
 useEffect(()=>{const interval=setInterval(()=>setNow(Date.now()),1000);return()=>clearInterval(interval);},[]);
 useEffect(()=>{
  if(auth!=='ready')return;let active=true;setConnected(false);setHistory(old=>interruptHistory(old));const controller=new AbortController();const connection=new HubConnectionBuilder().withUrl('/hubs/dashboard',{withCredentials:true}).withAutomaticReconnect([0,2000,5000,10000]).configureLogging(LogLevel.None).build();
  const load=()=>fetch('/api/dashboard',{credentials:'same-origin',cache:'no-store',signal:controller.signal}).then(async r=>{if(r.status===401){setAuth('locked');throw Error('세션이 만료되었습니다.');}if(!r.ok)throw Error('데이터를 불러올 수 없습니다.');const data:unknown=await r.json();if(active)receive(data);}).catch(e=>{if(active&&e.name!=='AbortError')setError(e.message);});
  connection.on('snapshot',(raw:unknown)=>{if(active)receive(raw);});connection.onreconnecting(()=>{if(active){setConnected(false);setHistory(old=>interruptHistory(old));setTransport('재연결 중 · 이력');}});connection.onreconnected(()=>{if(active){setConnected(true);setTransport('실시간 연결');void load();}});connection.onclose(()=>{if(active){setConnected(false);setHistory(old=>interruptHistory(old));setTransport('연결 종료 · 이력');}});
  void connection.start().then(()=>{if(active){setConnected(true);setTransport('실시간 연결');void load();}}).catch(()=>{if(active){setConnected(false);setHistory(old=>interruptHistory(old));setTransport('실시간 연결 불가 · 재시도 필요');setError('실시간 연결을 시작하지 못했습니다. 새로고침으로 다시 연결하세요.');}});void load();
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
