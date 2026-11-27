import {lazy,Suspense,useEffect,useRef,useState,type FormEvent} from 'react';
import {Archive,ArrowRight,ChartNoAxesCombined,LockKeyhole,PanelLeftClose,PanelLeftOpen,RefreshCw,Search,ShieldCheck,Unplug} from 'lucide-react';
import {Button} from '@/components/ui/button';
import {Card,CardHeader,CardTitle,CardContent} from '@/components/ui/card';
import {Input} from '@/components/ui/input';
import {useDashboard} from './use-dashboard';
import {collectionEvidence,filterDevices,isCurrent,tagState,signalLabels,formatValue,type Snapshot} from './data';
import StoredHistory from './StoredHistory';
import SignalWorkspace from './SignalWorkspace';
import Incidents,{type IncidentArchiveTarget} from './Incidents';
import AnalysisProfile from './AnalysisProfile';
import LocalDiagnostics from './LocalDiagnostics';
import './instrument-shell.css';
const SignalChart=lazy(()=>import('./SignalChart'));
const compactLists='(max-width: 850px)';
const localClock=new Intl.DateTimeFormat('en-GB',{hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'});
const readingStates={CURRENT:'실시간',RESTORED:'복원 이력',HISTORY:'과거 관측',FIXTURE:'고정 자료',ABSENT:'미수신',MISSING:'값 없음',BAD:'품질 불량'};
const collectorStates={CURRENT:'수집 중',STALE:'수신 지연',RESTORED:'복원 이력',NO_RECEIPT:'수신 대기',UNAVAILABLE:'확인 불가'};
const viewNames={signals:'실시간 신호',archive:'태그 보관 이력',incidents:'이상 사건',analysis:'분석 설정',diagnostics:'로컬 진단'};
type View=keyof typeof viewNames;

function Clock({stamp,fallback='미제공'}:{stamp:string|undefined|null;fallback?:string}) {
 if(!stamp)return <span>{fallback}</span>;
 return <time dateTime={stamp} title={stamp}>{localClock.format(new Date(stamp))}</time>;
}

function CollectorStrip({snapshot,now,available}:{snapshot:Snapshot|null;now:number;available:boolean}) {
 const devices=snapshot?.devices??[];
 return <section className="collector-strip" aria-label="수집기 상태">
  <div className="collector-items">
   {devices.map(device=>{
    const evidence=collectionEvidence(snapshot,device,now,available);
    const freshness=evidence?.freshness??(isCurrent(snapshot,device,now,available)?'CURRENT':'UNAVAILABLE');
    return <div className="collector-item" data-freshness={freshness} key={device.deviceId}>
     <i aria-hidden="true"/><strong title={device.deviceId}>{device.moduleId??device.deviceId}</strong><span>{collectorStates[freshness]}</span>
     <Clock stamp={evidence?.lastReceivedAtUtc??device.connection?.lastReceivedAtUtc} fallback="수신 전"/>
    </div>;
   })}
   {!devices.length&&<p className="collector-empty">수집기 상태 확인 중</p>}
  </div>
  <div className="collector-clock"><span>게이트웨이 갱신 · 로컬</span> <Clock stamp={snapshot?.generatedAtUtc} fallback="수신 전"/></div>
 </section>;
}
function Unlock({unlock,recheckSession,sessionUnavailable,error,checking}:{unlock:(token:string)=>Promise<void>;recheckSession:()=>Promise<void>;sessionUnavailable:boolean;error:string|null;checking:boolean}){
 const [token,setToken]=useState('');const [failure,setFailure]=useState<string|null>(null);const [pending,setPending]=useState(false);
 async function submit(e:FormEvent){e.preventDefault();setPending(true);setFailure(null);try{await unlock(token);}catch{setFailure('접속하지 못했습니다. 게이트웨이와 로컬 토큰을 확인하세요.');}finally{setToken('');setPending(false);}}
 function retry(){setFailure(null);void recheckSession();}
 return <main className="unlock"><Card className="unlock-card"><div className="brand"><ShieldCheck size={31}/>AEGIS</div><div><h1 className="text-xl font-semibold">로컬 대시보드 접속</h1><p className="mt-3">설비 원본을 읽기 전용으로 확인합니다. 게이트웨이의 로컬 접속 토큰을 입력하세요.</p></div>
 <form onSubmit={submit} aria-busy={checking||pending}>
 <label htmlFor="local-token">로컬 접속 토큰</label><Input id="local-token" type="password" autoComplete="off" value={token} onChange={e=>setToken(e.target.value)} required disabled={checking||pending} aria-describedby="unlock-error"/>
 <Button type="submit" disabled={checking||pending||!token}>{checking?'게이트웨이 확인 중…':pending?'접속 확인 중…':'대시보드 열기'}<ArrowRight data-icon="inline-end"/></Button>
 <p id="unlock-error" role="status" className="error-text">{checking?'로컬 게이트웨이를 확인하고 있습니다.':failure??error}</p>
 {sessionUnavailable&&<Button type="button" variant="outline" onClick={retry} disabled={checking||pending}>{checking?'다시 확인 중…':'게이트웨이 다시 확인'}<RefreshCw data-icon="inline-end"/></Button>}
 </form><p><LockKeyhole size={12} className="inline mr-1"/>토큰은 URL 또는 브라우저 저장소에 보관하지 않습니다.</p></Card></main>;
}
export default function App(){
 const dashboard=useDashboard();
 const {snapshot,history,now}=dashboard;
 const available=dashboard.connected&&!dashboard.error;
 const [selected,setSelected]=useState('');
 const [query,setQuery]=useState('');
 const [signal,setSignal]=useState('actualFlowLpm');
 const [view,setView]=useState<View>('signals');
 const [incidentArchive,setIncidentArchive]=useState<IncidentArchiveTarget>();
 const [lastIncidentId,setLastIncidentId]=useState<string>();
 const [archiveTag,setArchiveTag]=useState<string>();
 const [listsCollapsed,setListsCollapsed]=useState(()=>window.matchMedia(compactLists).matches);
 const focusLive=useRef(false);
 const archiveButton=useRef<HTMLButtonElement|null>(null);
 const moduleArchiveButton=useRef<HTMLButtonElement|null>(null);
 const incidentButton=useRef<HTMLButtonElement|null>(null);
 const analysisButton=useRef<HTMLButtonElement|null>(null);
 const diagnosticsButton=useRef<HTMLButtonElement|null>(null);
 const [returnToTag,setReturnToTag]=useState(true);
 useEffect(()=>{
  const media=window.matchMedia(compactLists);
  const sync=(event:MediaQueryListEvent)=>setListsCollapsed(event.matches);
  media.addEventListener('change',sync);
  return()=>media.removeEventListener('change',sync);
 },[]);
 useEffect(()=>{document.title='Aegis · '+(dashboard.auth!=='ready'?'로컬 대시보드 접속':viewNames[view]);},[dashboard.auth,view]);
 useEffect(()=>{if(view==='signals'&&focusLive.current){focusLive.current=false;(returnToTag?archiveButton:moduleArchiveButton).current?.focus();}},[view,returnToTag]);
 if(dashboard.auth!=='ready')return <Unlock unlock={dashboard.unlock} recheckSession={dashboard.recheckSession} sessionUnavailable={dashboard.sessionUnavailable} error={dashboard.error} checking={dashboard.auth==='checking'}/>;
 const devices=filterDevices(snapshot?.devices??[],'all',query);
 const device=devices.find(d=>d.deviceId===selected)??devices[0];
 const current=isCurrent(snapshot,device,now,available);
 const observation=device?.observations.find(o=>o.signalKey===signal);
 const state=tagState(snapshot,device,observation,now,available);
 const historical=state!=='CURRENT';
 const points=history[device?.deviceId??'']?.[signal]??[];
 const evidence=collectionEvidence(snapshot,device,now,available);
 function closeArchive(){
  if(incidentArchive){setIncidentArchive(undefined);setView('incidents');return;}
  focusLive.current=true;setView('signals');
 }
 function openArchive(tag?:string){setIncidentArchive(undefined);setArchiveTag(tag);setReturnToTag(!!tag);setView('archive');}
 function openIncidentArchive(target:IncidentArchiveTarget){
  setLastIncidentId(target.incidentId);setArchiveTag(target.signalKey);setIncidentArchive(target);setView('archive');
 }
 function closeProductView(button:HTMLButtonElement|null){setView('signals');button?.focus();}
 const chart=<section className="instrument-inspector" aria-labelledby="instrument-signal-title">
 <div className="inspector-heading">
  <div className="inspector-identity"><p className="inspector-path">{device?.equipmentId??'장비 미선택'} / {device?.moduleId??device?.deviceId??'모듈 미선택'}</p>
   <h2 id="instrument-signal-title">{signalLabels[signal]??signal}</h2><p className="inspector-signal-key">{signal}</p></div>
  <div className="inspector-reading" data-state={state}><span>최근 관측값</span>
   <strong>{formatValue(observation?.value)}{observation?.unit&&<small>{observation.unit}</small>}</strong></div>
 </div>
 <div className="inspector-evidence"><span className="inspector-state" data-state={state}>{readingStates[state]}</span>
  <span className="inspector-quality" data-quality={observation?.quality}>{observation?'수신 품질 '+observation.quality:'관측 없음'}</span>
  <span>원본 관측 <Clock stamp={observation?.observedAtUtc}/></span><span>수집기 수신 <Clock stamp={observation?.receivedAtUtc}/></span>
  <Button className="inspector-archive" type="button" size="sm" variant="outline" aria-label="선택 태그 보관 이력" ref={archiveButton} disabled={!device} onClick={()=>openArchive(signal)}><Archive size={14} aria-hidden="true"/>선택 태그 이력</Button>
 </div>
 {points.length?<Suspense fallback={<div className="empty-chart">차트 로딩 중</div>}><SignalChart key={`${snapshot?.sourceMode}/${device?.deviceId}/${signal}`} context={`${device?.deviceId??'장치 없음'} · ${snapshot?.sourceMode??'UNKNOWN'}`} points={points} label={signalLabels[signal]??signal} unit={observation?.unit??''} historical={historical}/></Suspense>:<div className="empty-chart"><ChartNoAxesCombined size={33}/><p>선택한 태그의 수신 요약이 없습니다.</p><small>연결된 소스의 실제 관측만 표시합니다</small></div>}
 <p className="chart-note">1Hz UI 요약 · 최근 최대 120개 · 원시 기록은 보관 이력에서 확인</p></section>;
 return <div className="worksheet-shell" lang="ko">
 <header className="instrument-command">
  <div className="instrument-command-lead">
   <button className="instrument-command-button" type="button" aria-expanded={!listsCollapsed} aria-controls="instrument-lists" onClick={()=>setListsCollapsed(value=>!value)}>
    {listsCollapsed?<PanelLeftOpen size={15} aria-hidden="true"/>:<PanelLeftClose size={15} aria-hidden="true"/>}{listsCollapsed?'목록 열기':'목록 접기'}
   </button>
   <h1>계측 작업대</h1>
  </div>
  <div className="instrument-module-search"><label className="sr-only" htmlFor="module-search">장비 · 모듈 검색</label>
   <Search size={14} aria-hidden="true"/><Input id="module-search" placeholder="장비 · 모듈 검색" value={query} onFocus={()=>setListsCollapsed(false)} onChange={e=>{setQuery(e.target.value);setView('signals');}}/>
  </div>
  <nav className="instrument-views" aria-label="작업대 보기">
   <button type="button" aria-pressed={view==='signals'} onClick={()=>{if(view==='archive'&&!incidentArchive)closeArchive();else setView('signals');}}>태그 검사</button>
   <button type="button" aria-pressed={view==='archive'} onClick={()=>openArchive(device?signal:undefined)}>보관 이력</button>
   <button type="button" ref={incidentButton} aria-pressed={view==='incidents'} onClick={()=>setView('incidents')}>이상 사건</button>
   <button type="button" ref={analysisButton} aria-pressed={view==='analysis'} disabled={!device} onClick={()=>setView('analysis')}>분석 설정</button>
   <button type="button" ref={diagnosticsButton} aria-pressed={view==='diagnostics'} aria-controls="signal-chart-panel" onClick={()=>setView('diagnostics')}>진단</button>
  </nav>
  <div className="instrument-command-end"><span className="instrument-source-mode">{snapshot?.sourceMode==='EMULATORS'?'합성 데이터':snapshot?.sourceMode==='FIXTURES'?'고정 검증 자료':'소스 확인 중'}</span>
   <Button variant="outline" size="icon" aria-label="데이터 및 실시간 연결 새로고침" onClick={dashboard.refresh}><RefreshCw size={15} aria-hidden="true"/></Button></div>
 </header>
 <CollectorStrip snapshot={snapshot} now={now} available={available}/>
 <main className="instrument-page">
 {(dashboard.error||snapshot?.upstreamState!=='CONNECTED')&&<div className="alert-line" role="status"><Unplug size={17}/><span>{dashboard.error??snapshot?.message??'게이트웨이 데이터 수신을 기다리고 있습니다.'}</span></div>}
 <SignalWorkspace devices={devices} device={device} snapshot={snapshot} now={now} available={available} signal={signal} listsCollapsed={listsCollapsed}
 selectDevice={id=>{setSelected(id);if(view==='archive')setView('signals');}} selectSignal={key=>{setSignal(key);setView('signals');}}
 openHistory={()=>openArchive()} historyButtonRef={moduleArchiveButton}
 chart={view==='archive'?<div className="instrument-history"><StoredHistory
  key={JSON.stringify([incidentArchive?.incidentId,incidentArchive?.sessionId,incidentArchive?.deviceId??device?.deviceId,archiveTag])}
  deviceId={incidentArchive?.deviceId??device?.deviceId} initialSignal={archiveTag} initialSessionId={incidentArchive?.sessionId}
  fromIncident={!!incidentArchive} returnToSignal={closeArchive}/></div>
  :view==='incidents'?<div className="instrument-history"><Incidents deviceId={device?.deviceId} initialIncidentId={lastIncidentId}
   openArchive={openIncidentArchive} close={()=>closeProductView(incidentButton.current)}/></div>
  :view==='analysis'&&device?<div className="instrument-history"><AnalysisProfile key={device.deviceId} deviceId={device.deviceId}
   close={()=>closeProductView(analysisButton.current)}/></div>
  :view==='diagnostics'?<div className="instrument-history"><LocalDiagnostics close={()=>closeProductView(diagnosticsButton.current)} selectedModule={!!device} nativeFlow={device?.nativeFlow} nativeSequence={device?.nativeSequence??null} now={now}/></div>:chart}/>
 <footer className="instrument-footer"><span><LockKeyhole size={12} aria-hidden="true"/>원본 읽기 전용 · {current?'현재 수신':'수신 대기 / 이력'} · {device?.deviceId??'모듈 미선택'}</span>
  <span>마지막 수신 <Clock stamp={evidence?.lastReceivedAtUtc} fallback="수신 전"/> · 보관 완료 <Clock stamp={evidence?.lastCommittedAtUtc}/></span></footer>
 </main></div>;
}
