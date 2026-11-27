import {useEffect,useRef,useState} from 'react';
import {Download,RefreshCw} from 'lucide-react';
import {Button} from '@/components/ui/button';
import {Card,CardContent,CardHeader,CardTitle} from '@/components/ui/card';
import {localDiagnosticsJson,parseLocalDiagnostics,type LocalDiagnosticsReport} from './local-diagnostics';
import {formatValue,type NativeFlowEvidence} from './data';

type NativeBridge={
 postMessage(message:unknown):void;
 addEventListener(name:'message',listener:(event:MessageEvent<unknown>)=>void):void;
 removeEventListener(name:'message',listener:(event:MessageEvent<unknown>)=>void):void;
};
type PendingSave={requestId:string;timeout:number};
type ModuleRow=LocalDiagnosticsReport['modules'][number];

const dateTime=new Intl.DateTimeFormat('ko-KR',{dateStyle:'short',timeStyle:'medium'});
const sourceLabels={EMULATORS:'합성 수집기',FIXTURES:'고정 검증 자료',UNKNOWN:'소스 확인 불가'};
const upstreamLabels={CONNECTED:'연결됨',UNAVAILABLE:'연결 불가',CONFIGURATION_REQUIRED:'설정 필요',UNKNOWN:'확인 불가'};
const workspaceLabels={READY:'사용 가능',NOT_CONFIGURED:'설정 안 됨',UNAVAILABLE:'사용 불가'};
const freshnessLabels={CURRENT:'수신 중',STALE:'수신 지연',RESTORED:'복원 이력',NO_RECEIPT:'수신 전',UNAVAILABLE:'확인 불가',UNKNOWN:'확인 불가'};
const connectionLabels={CONNECTING:'연결 중',CONNECTED:'연결됨',DISCONNECTED:'연결 끊김',REJECTED:'연결 거절',STALE:'연결 지연',UNKNOWN:'확인 불가'};
const engineLabels={RUNNING:'가동',UNAVAILABLE:'사용 불가',ARCHIVE_NOT_CONFIGURED:'보관 설정 안 됨',UNKNOWN:'확인 불가'};
const workerLabels={RUNNING:'가동',RESTARTING:'재시작 중',FAILED:'중단',UNAVAILABLE:'사용 불가',NOT_APPLICABLE:'해당 없음',UNKNOWN:'확인 불가'};
const nativeFlowLabels={READY:'준비 완료',WARMING:'준비 중',RECOVERING:'복구 중',UNAVAILABLE:'사용 불가'};
const nativeFlowReasons={ARCHIVE_UNAVAILABLE:'보관 데이터를 읽을 수 없습니다.',NATIVE_HOST_UNAVAILABLE:'네이티브 처리 호스트에 연결할 수 없습니다.',REPLAY_WINDOW_INCOMPLETE:'보관 이력 동기화를 완료하지 못했습니다.',SOURCE_RUN_CHANGED:'원본 실행 구간을 다시 동기화하고 있습니다.',SOURCE_STALE:'새 관측이 없어 상태가 오래되었습니다.',UPSTREAM_UNAVAILABLE:'게이트웨이 데이터 연결을 사용할 수 없습니다.'};
const saveStatuses={saved:'진단 JSON을 저장했습니다.',cancelled:'진단 파일 저장을 취소했습니다.',error:'진단 파일을 저장하지 못했습니다. 저장 위치와 파일 이름을 확인하세요.'};

function nativeBridge():NativeBridge|undefined {
 const bridge=(window as Window&{chrome?:{webview?:NativeBridge}}).chrome?.webview;
 return typeof bridge?.postMessage==='function'&&typeof bridge.addEventListener==='function'&&typeof bridge.removeEventListener==='function'?bridge:undefined;
}

function Timestamp({value}:{value:string|null}){
 return value?<time dateTime={value} title={value}>{dateTime.format(new Date(value))}</time>:<span>기록 없음</span>;
}

function nextModuleAction(row:ModuleRow):string|null {
 if(row.connection==='REJECTED')return '수집기 설정과 연결 권한을 확인하세요.';
 if(row.workerState==='FAILED'||row.connection==='DISCONNECTED')return '해당 수집기의 실행 상태를 확인한 뒤 다시 연결하세요.';
 if(row.workerState==='RESTARTING')return '수집기 재시작이 끝난 뒤 다시 확인하세요.';
 if(row.freshness==='STALE'||row.connection==='STALE')return '마지막 수신 시각과 수집기 지연을 확인하세요.';
 if(row.engine==='ARCHIVE_NOT_CONFIGURED')return '보관 설정을 확인하세요.';
 if(row.engine==='UNAVAILABLE')return '보관 위치와 로컬 쓰기 권한을 확인하세요.';
 if(row.freshness==='RESTORED'||row.freshness==='NO_RECEIPT')return '새 수신 시각이 기록될 때까지 기다리세요.';
 return null;
}

function observationAge(value:string|null,now:number):string {
 if(!value)return '기록 없음';
 const elapsed=now-Date.parse(value);
 if(elapsed<0)return '시각 확인 필요';
 const seconds=Math.floor(elapsed/1000);
 if(seconds<1)return '1초 미만 전';
 if(seconds<60)return `${seconds}초 전`;
 return `${Math.floor(seconds/60)}분 ${seconds%60}초 전`;
}

function NativeFlowSummary({selectedModule,nativeFlow,nativeSequence,now}:{selectedModule:boolean;nativeFlow:NativeFlowEvidence|null|undefined;nativeSequence:number|null;now:number}) {
 const label=nativeFlow?nativeFlowLabels[nativeFlow.state]:selectedModule?'기본 관리형':'모듈 미선택';
 return <section className="native-flow-summary" aria-labelledby="native-flow-title">
  <h3 id="native-flow-title">선택 모듈 처리 상태</h3>
  <p className="chart-note">대시보드에 캐시된 상태와 관측 요약입니다.</p>
  <dl className="incident-evidence">
   <dt>처리 상태</dt><dd><span className="native-flow-state" data-native-state={nativeFlow?.state??(selectedModule?'MANAGED':'NONE')} aria-label={`처리 상태: ${label}`}>{label}</span></dd>
   <dt>상태 사유</dt><dd>{nativeFlow?.reason?nativeFlowReasons[nativeFlow.reason]:'기록 없음'}</dd>
   <dt>표본 수</dt><dd>{nativeFlow?nativeFlow.sampleCount:'—'}</dd>
   <dt>평균 절대 편차</dt><dd>{nativeFlow?.meanAbsoluteDeviationLpm===null||!nativeFlow?'—':`${formatValue(nativeFlow.meanAbsoluteDeviationLpm)} L/min`}</dd>
   <dt>마지막 관측 경과</dt><dd>{nativeFlow?observationAge(nativeFlow.observedAtUtc,now):'기록 없음'}</dd>
   <dt>처리 순번</dt><dd>{nativeSequence??'기록 없음'}</dd>
  </dl>
 </section>;
}

export default function LocalDiagnostics({close,nativeFlow,nativeSequence,now,selectedModule}:{close:()=>void;nativeFlow:NativeFlowEvidence|null|undefined;nativeSequence:number|null;now:number;selectedModule:boolean}) {
 const [report,setReport]=useState<LocalDiagnosticsReport|null>(null);
 const [refresh,setRefresh]=useState(0);
 const [loading,setLoading]=useState(true);
 const [failure,setFailure]=useState<string|null>(null);
 const [saving,setSaving]=useState(false);
 const [saveStatus,setSaveStatus]=useState<string|null>(null);
 const heading=useRef<HTMLHeadingElement>(null);
 const pendingSave=useRef<PendingSave|null>(null);
 const preview=report?localDiagnosticsJson(report):'';
 useEffect(()=>{heading.current?.focus();},[]);
 useEffect(()=>{
  const request=new AbortController();
  setReport(null);setLoading(true);setFailure(null);setSaveStatus(null);
  const timeout=window.setTimeout(()=>{
   request.abort();setLoading(false);setFailure('진단 응답이 지연되고 있습니다. 계측 작업대의 게이트웨이 연결을 확인한 뒤 다시 시도하세요.');
  },10000);
  fetch('/api/local-diagnostics',{credentials:'same-origin',cache:'no-store',signal:request.signal})
   .then(async response=>{
    if(!response.ok)throw Error();
    const next=parseLocalDiagnostics(await response.json());
    if(!request.signal.aborted)setReport(next);
   })
   .catch(()=>{if(!request.signal.aborted)setFailure('진단 정보를 읽을 수 없습니다. 로컬 게이트웨이와 접속 상태를 확인한 뒤 다시 시도하세요.');})
   .finally(()=>{clearTimeout(timeout);if(!request.signal.aborted)setLoading(false);});
  return()=>{clearTimeout(timeout);request.abort();};
 },[refresh]);
 useEffect(()=>{
  const bridge=nativeBridge();
  if(!bridge)return;
  function finish(event:MessageEvent<unknown>){
   if(typeof event.data!=='object'||event.data===null)return;
   const result=event.data as {type?:unknown;requestId?:unknown;status?:unknown};
   if(!pendingSave.current||result.type!=='aegis-diagnostics-save-result'||result.requestId!==pendingSave.current.requestId||
       typeof result.status!=='string'||!Object.hasOwn(saveStatuses,result.status))return;
   clearTimeout(pendingSave.current.timeout);pendingSave.current=null;setSaving(false);
   setSaveStatus(saveStatuses[result.status as keyof typeof saveStatuses]);
  }
  bridge.addEventListener('message',finish);
  return()=>{
   bridge.removeEventListener('message',finish);
   if(pendingSave.current)clearTimeout(pendingSave.current.timeout);
   pendingSave.current=null;
  };
 },[]);

 function save(){
  if(!report||pendingSave.current)return;
  setSaveStatus(null);
  const bridge=nativeBridge();
  if(bridge){
   try{
    const requestId=crypto.randomUUID();
    const timeout=window.setTimeout(()=>{
     pendingSave.current=null;setSaving(false);setSaveStatus('저장 결과를 확인하지 못했습니다. 저장 창을 확인하세요.');
    },120000);
    pendingSave.current={requestId,timeout};setSaving(true);
    bridge.postMessage({type:'aegis-save-diagnostics',requestId,report});
   }catch{
    if(pendingSave.current)clearTimeout(pendingSave.current.timeout);
    pendingSave.current=null;setSaving(false);setSaveStatus(saveStatuses.error);
   }
   return;
  }
  const url=URL.createObjectURL(new Blob([preview],{type:'application/json'}));
  try{
   const anchor=document.createElement('a');anchor.href=url;anchor.download='Aegis-diagnostics.json';anchor.click();
   setSaveStatus('브라우저에 JSON 다운로드를 요청했습니다.');
  }catch{setSaveStatus(saveStatuses.error);}
  finally{window.setTimeout(()=>URL.revokeObjectURL(url),1000);}
 }

 return <Card className="panel diagnostics-panel" role="region" aria-labelledby="diagnostics-title" onKeyDown={event=>{
  if(event.key==='Escape'){event.preventDefault();close();}
 }}>
  <CardHeader className="panel-heading product-panel-heading">
   <CardTitle><h2 id="diagnostics-title" ref={heading} tabIndex={-1}>로컬 진단</h2></CardTitle>
   <Button size="sm" variant="outline" disabled={loading||saving} onClick={()=>setRefresh(attempt=>attempt+1)}><RefreshCw size={14} aria-hidden="true"/>진단 새로고침</Button>
  </CardHeader>
  <CardContent className="panel-body" aria-busy={loading}>
   {loading&&<p role="status" className="chart-note">로컬 상태를 확인하는 중…</p>}
   {failure&&<p role="alert" className="chart-note">{failure}</p>}
   <NativeFlowSummary selectedModule={selectedModule} nativeFlow={nativeFlow} nativeSequence={nativeSequence} now={now}/>
   {report&&<>
    <dl className="incident-evidence">
     <dt>작성 시각</dt><dd><Timestamp value={report.generatedAtUtc}/></dd>
     <dt>데이터 출처</dt><dd>{sourceLabels[report.sourceMode]}</dd>
     <dt>게이트웨이</dt><dd>{upstreamLabels[report.upstreamState]}</dd>
     <dt>작업 영역</dt><dd>{workspaceLabels[report.workspaceState]}</dd>
     <dt>학습 모델</dt><dd>등록 안 됨</dd>
    </dl>
    {report.upstreamState==='CONFIGURATION_REQUIRED'&&<p className="chart-note">수집기와 게이트웨이 설정을 확인하세요.</p>}
    {report.upstreamState==='UNAVAILABLE'&&<p className="chart-note">계측 작업대에서 실시간 연결을 새로고침하세요.</p>}
    {report.workspaceState==='NOT_CONFIGURED'&&<p className="chart-note">작업 폴더를 사용하도록 Aegis 실행 설정을 확인하세요.</p>}
    {report.workspaceState==='UNAVAILABLE'&&<p className="chart-note">작업 폴더의 쓰기 권한과 디스크 여유를 확인하세요.</p>}
    <section className="diagnostic-modules" aria-labelledby="diagnostic-modules-title">
     <h3 id="diagnostic-modules-title">수집 모듈</h3>
     {report.modules.length?<ul>{report.modules.map(row=>{
      const action=nextModuleAction(row);
      return <li key={row.slot}>
       <details>
        <summary><strong>모듈 슬롯 {row.slot}</strong><span>{connectionLabels[row.connection]} · {freshnessLabels[row.freshness]}</span></summary>
        <dl className="incident-evidence">
         <dt>마지막 수신</dt><dd><Timestamp value={row.lastReceivedAtUtc}/></dd>
         <dt>마지막 관측</dt><dd><Timestamp value={row.lastObservedAtUtc}/></dd>
         <dt>수집 프로세스</dt><dd>{workerLabels[row.workerState]}</dd>
         <dt>보관 엔진</dt><dd>{engineLabels[row.engine]}</dd>
         <dt>원본 보관</dt><dd>{row.archiveEnabled===null?'확인 불가':row.archiveEnabled?'사용':'사용 안 함'}</dd>
         <dt>저장 순번</dt><dd>{row.lastCommittedSequence??'기록 없음'}</dd>
         <dt>대기 배치</dt><dd>{row.pendingBatches??'확인 불가'}</dd>
         <dt>품질 상태</dt><dd>정상 {row.quality.good} · 누락 {row.quality.missing} · 오래됨 {row.quality.stale} · 기타 {row.quality.other}</dd>
        </dl>
       </details>
       {action&&<p className="chart-note">{action}</p>}
      </li>;
     })}</ul>:<p className="chart-note">확인한 수집 모듈이 없습니다.</p>}
    </section>
    <details className="diagnostic-preview">
     <summary>JSON 미리보기</summary>
     <p className="chart-note">장비 식별자, 경로, 인증 정보, 원시 값은 포함하지 않습니다. 진단 파일은 로컬에 저장합니다.</p>
     <label>저장할 JSON<textarea className="diagnostic-json" rows={10} value={preview} readOnly spellCheck={false} wrap="off"/></label>
     <div className="product-panel-actions">
      <Button size="sm" variant="outline" disabled={saving} onClick={save}><Download size={14} aria-hidden="true"/>{saving?'저장 확인 중…':'진단 JSON 저장'}</Button>
     </div>
     {saveStatus&&<p role="status" className="chart-note">{saveStatus}</p>}
    </details>
   </>}
  </CardContent>
 </Card>;
}
