import {lazy,Suspense,useEffect,useRef,useState,type FormEvent} from 'react';
import {Archive,ArrowRight,ChartNoAxesCombined,LockKeyhole,RefreshCw,ShieldCheck,Unplug} from 'lucide-react';
import {Button} from '@/components/ui/button';
import {Card,CardHeader,CardTitle,CardContent} from '@/components/ui/card';
import {Input} from '@/components/ui/input';
import {useDashboard} from './use-dashboard';
import {collectionEvidence,filterDevices,isCurrent,observationCurrent,signalLabels,formatValue,time} from './data';
import StoredHistory from './StoredHistory';
import SignalWorkspace from './SignalWorkspace';
const SignalChart=lazy(()=>import('./SignalChart'));
function Unlock({unlock,recheckSession,sessionUnavailable,error,checking}:{unlock:(token:string)=>Promise<void>;recheckSession:()=>Promise<void>;sessionUnavailable:boolean;error:string|null;checking:boolean}){
 const [token,setToken]=useState('');const [failure,setFailure]=useState<string|null>(null);const [pending,setPending]=useState(false);
 async function submit(e:FormEvent){e.preventDefault();setPending(true);setFailure(null);try{await unlock(token);}catch{setFailure('접속하지 못했습니다. 게이트웨이와 로컬 토큰을 확인하세요.');}finally{setToken('');setPending(false);}}
 function retry(){setFailure(null);void recheckSession();}
 return <main className="unlock"><Card className="unlock-card"><div className="brand"><ShieldCheck size={31}/>AEGIS</div><div><h1 className="text-xl font-semibold">로컬 대시보드 접속</h1><p className="mt-3">수집 데이터와 검토 근거를 읽기 전용으로 확인합니다. 게이트웨이의 로컬 접속 토큰을 입력하세요.</p></div>
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
 const [archive,setArchive]=useState(false);
 const [archiveTag,setArchiveTag]=useState<string>();
 const focusLive=useRef(false);
 const archiveButton=useRef<HTMLButtonElement|null>(null);
 const moduleArchiveButton=useRef<HTMLButtonElement|null>(null);
 const [returnToTag,setReturnToTag]=useState(true);
 useEffect(()=>{document.title='Aegis · '+(dashboard.auth!=='ready'?'로컬 대시보드 접속':archive?'태그 보관 이력':'실시간 신호');},[dashboard.auth,archive]);
 useEffect(()=>{if(!archive&&focusLive.current){focusLive.current=false;(returnToTag?archiveButton:moduleArchiveButton).current?.focus();}},[archive,returnToTag]);
 if(dashboard.auth!=='ready')return <Unlock unlock={dashboard.unlock} recheckSession={dashboard.recheckSession} sessionUnavailable={dashboard.sessionUnavailable} error={dashboard.error} checking={dashboard.auth==='checking'}/>;
 const devices=filterDevices(snapshot?.devices??[],'all',query);
 const device=devices.find(d=>d.deviceId===selected)??devices[0];
 const current=isCurrent(snapshot,device,now,available);
 const observation=device?.observations.find(o=>o.signalKey===signal);
 const historical=!observation||!observationCurrent(snapshot,device,observation,now,available);
 const points=history[device?.deviceId??'']?.[signal]??[];
 const evidence=collectionEvidence(snapshot,device,now,available);
 function closeArchive(){focusLive.current=true;setArchive(false);}
 function openArchive(tag?:string){setArchiveTag(tag);setReturnToTag(!!tag);setArchive(true);}
 const chart=<Card className="panel signal-panel"><CardHeader className="panel-heading"><CardTitle><h2>신호 추이</h2></CardTitle><Button type="button" size="sm" variant="outline" aria-label="선택 태그 보관 이력" ref={archiveButton} disabled={!device} onClick={()=>openArchive(signal)}><Archive aria-hidden="true"/>선택 태그 이력</Button></CardHeader>
 <CardContent className="panel-body"><div className="inspector-reading"><div><p>{signalLabels[signal]??signal}</p><strong>{formatValue(observation?.value)} <small>{observation?.unit??''}</small></strong></div><span>{historical?'이력 / 수신 대기':'현재 관측'}<br/>요약 품질 {observation?.quality??'ABSENT'}</span></div>
 <p className="tag-scope">{device?.equipmentId??'장비 미선택'} / {device?.moduleId??device?.deviceId??'모듈 미선택'} / {signal}</p>
 <p className="chart-note">원본 관측 {observation?.observedAtUtc??'미제공'}</p>
 {points.length?<Suspense fallback={<div className="empty-chart">차트 로딩 중</div>}><SignalChart key={`${snapshot?.sourceMode}/${device?.deviceId}/${signal}`} context={`${device?.deviceId??'장치 없음'} · ${snapshot?.sourceMode??'UNKNOWN'}`} points={points} label={signalLabels[signal]??signal} unit={observation?.unit??''} historical={historical}/></Suspense>:<div className="empty-chart"><ChartNoAxesCombined size={33}/><p>선택한 태그의 수신 요약이 없습니다.</p><small>연결된 소스의 실제 관측만 표시합니다</small></div>}
 <p className="chart-note">1Hz UI 요약 · 최근 최대 120개 · 원시 100Hz 파형이 아닙니다. 누락·유효하지 않은 품질과 긴 수신 간격은 선을 연결하지 않습니다.</p></CardContent></Card>;
 return <div className="worksheet-shell"><div className="workspace telemetry-workspace"><header className="topbar"><div className="worksheet-brand"><ShieldCheck aria-hidden="true"/><strong>AEGIS</strong><h1>태그 워크시트</h1></div><div className="topbar-tools"><span className="read-only"><LockKeyhole size={14}/>읽기 전용</span><Button variant="outline" size="icon" aria-label="데이터 및 실시간 연결 새로고침" onClick={dashboard.refresh}><RefreshCw/></Button></div></header>
 <main className="page"><div className="worksheet-toolbar"><div><label htmlFor="module-search">장비 · 모듈 검색</label><Input id="module-search" placeholder="장비, 모듈 또는 태그" value={query} onChange={e=>{setQuery(e.target.value);setArchive(false);}}/></div><p>{snapshot?.sourceMode==='EMULATORS'?'에뮬레이터 · 합성':snapshot?.sourceMode==='FIXTURES'?'고정 검증 자료':'소스 확인 중'}<br/><span>게이트웨이 갱신 {time(snapshot?.generatedAtUtc)}</span></p></div>
 {(dashboard.error||snapshot?.upstreamState!=='CONNECTED')&&<div className="alert-line" role="status"><Unplug size={17}/><span>{dashboard.error??snapshot?.message??'게이트웨이 데이터 수신을 기다리고 있습니다.'}</span></div>}
 <SignalWorkspace devices={devices} device={device} snapshot={snapshot} now={now} available={available} signal={signal}
 selectDevice={id=>{setSelected(id);setArchive(false);}} selectSignal={key=>{setSignal(key);setArchive(false);}}
 openHistory={()=>openArchive()} historyButtonRef={moduleArchiveButton}
 chart={archive?<StoredHistory key={JSON.stringify([device?.deviceId,archiveTag])} deviceId={device?.deviceId} initialSignal={archiveTag} returnToSignal={closeArchive}/>:chart}/>
 <footer className="footer"><span>{current?'현재 수신':'수신 대기 / 이력'} · {device?.deviceId??'모듈 미선택'} · 원시 데이터 보존</span><span>마지막 수신 {time(evidence?.lastReceivedAtUtc)} · 보관 완료 {time(evidence?.lastCommittedAtUtc)}</span></footer>
 </main></div></div>;
}
