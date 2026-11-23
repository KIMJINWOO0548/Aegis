import {useEffect,useRef,useState} from 'react';
import {Archive,RefreshCw} from 'lucide-react';
import {Button} from '@/components/ui/button';
import {Card,CardContent,CardHeader,CardTitle} from '@/components/ui/card';
import {isArchiveSessionId} from './archive-sessions';
import {signalLabels} from './data';

type IncidentDetails={
 reasonCode?:string;recommendationId?:string;evidenceSignalKeys?:string[];
 windowStartUtc?:string;windowEndUtc?:string;
};
type Incident={
 id:string;deviceId:string;state:string;createdAtUtc:string;updatedAtUtc:string;
 modelVersion:string|null;reviewedAtUtc:string|null;sessionId:string|null;details?:IncidentDetails|null;
};
type IncidentList={incidents:Incident[];currentSessionId:string|null;hasMore:boolean;captureUnavailable:boolean};
export type IncidentArchiveTarget={incidentId:string;deviceId:string;sessionId?:string;signalKey?:string};

const incidentTime = new Intl.DateTimeFormat('ko-KR',{dateStyle:'short',timeStyle:'medium'});
const reasonLabels:Record<string,string> = {
 FLOW_ERROR_PERSISTENT:'설정 유량과 실제 유량의 차이가 지속됨',
 FLOW_ERROR_RECOVERED:'유량 편차가 복귀 조건을 충족함',
};

function stamp(value:string){
 return Number.isFinite(Date.parse(value))?incidentTime.format(new Date(value)):value;
}

function incidentState(state:string){
 if(state==='OPEN')return '열림';
 if(state==='RESOLVED')return '복귀 관측';
 return state;
}

function parseIncidents(value:unknown):IncidentList {
 if(typeof value!=='object'||value===null)throw Error();
 const list=value as {schemaVersion?:unknown;currentSessionId?:unknown;incidents?:unknown;hasMore?:unknown;captureState?:unknown};
 if(list.schemaVersion!=='incidents-1'||!Array.isArray(list.incidents))throw Error();
 return {
  currentSessionId:isArchiveSessionId(list.currentSessionId)?list.currentSessionId:null,
  hasMore:list.hasMore===true,
  captureUnavailable:list.captureState==='INCIDENT_STORAGE_UNAVAILABLE',
  incidents:list.incidents.filter((row):row is Incident=>row&&typeof row.id==='string'&&typeof row.deviceId==='string'&&
     typeof row.state==='string'&&typeof row.createdAtUtc==='string'&&typeof row.updatedAtUtc==='string'),
 };
}

export default function Incidents({deviceId,initialIncidentId,openArchive,close}:{
 deviceId?:string;initialIncidentId?:string;openArchive:(target:IncidentArchiveTarget)=>void;close:()=>void;
}){
 const [data,setData]=useState<IncidentList|null>(null);
 const [showAll,setShowAll]=useState(false);
 const [selectedId,setSelectedId]=useState(initialIncidentId??'');
 const [reload,setReload]=useState(0);
 const [loading,setLoading]=useState(true);
 const [failure,setFailure]=useState<string|null>(null);
 const [reviewing,setReviewing]=useState(false);
 const [reviewMessage,setReviewMessage]=useState<string|null>(null);
 const reviewRequest=useRef<AbortController|null>(null);
 const title=useRef<HTMLHeadingElement>(null);
 const detailHeading=useRef<HTMLHeadingElement>(null);
 useEffect(()=>{title.current?.focus();return()=>reviewRequest.current?.abort();},[]);
 useEffect(()=>{
  const request=new AbortController();
  const query=new URLSearchParams();
  if(!showAll&&deviceId)query.set('deviceId',deviceId);
  setLoading(true);setFailure(null);
  fetch('/api/incidents?'+query,{credentials:'same-origin',signal:request.signal})
   .then(async response=>{
    if(!response.ok)throw Error();
    const incoming=parseIncidents(await response.json());
    if(request.signal.aborted)return;
    setData(incoming);
    setSelectedId(previous=>incoming.incidents.some(incident=>incident.id===previous)?previous:incoming.incidents[0]?.id??'');
   })
   .catch(()=>{if(!request.signal.aborted){setData(null);setFailure('사건 목록을 읽지 못했습니다. 연결을 확인한 뒤 다시 시도하세요.');}})
   .finally(()=>{if(!request.signal.aborted)setLoading(false);});
  return()=>request.abort();
 },[deviceId,reload,showAll]);
 const selected=data?.incidents.find(incident=>incident.id===selectedId);
 const evidenceSignals=selected?.details?.evidenceSignalKeys;
 const signals=Array.isArray(evidenceSignals)?evidenceSignals.filter(key=>typeof key==='string'):[];
 const archiveTarget=selected&&isArchiveSessionId(selected.sessionId)
  ?{incidentId:selected.id,deviceId:selected.deviceId,sessionId:selected.sessionId===data?.currentSessionId?undefined:selected.sessionId,signalKey:signals[0]}
  :null;

 async function markReviewed(incident:Incident){
  if(reviewRequest.current)return;
  const request=new AbortController();
  reviewRequest.current=request;
  setReviewing(true);setReviewMessage(null);
  try{
   const response=await fetch('/api/incidents/'+encodeURIComponent(incident.id)+'/review',{
    method:'POST',credentials:'same-origin',signal:request.signal,
   });
   if(!response.ok)throw Error();
   const result=await response.json();
   if(result.schemaVersion!=='incident-review-1'||typeof result.reviewedAtUtc!=='string')throw Error();
   if(request.signal.aborted)return;
   setData(previous=>previous?{...previous,incidents:previous.incidents.map(row=>row.id===incident.id?{...row,reviewedAtUtc:result.reviewedAtUtc}:row)}:previous);
   setReviewMessage('운영자 확인을 저장했습니다.');
  }catch{
   if(!request.signal.aborted)setReviewMessage('확인을 저장하지 못했습니다. 목록을 새로 읽고 저장 상태를 확인하세요.');
  }finally{
   if(!request.signal.aborted){reviewRequest.current=null;setReviewing(false);}
  }
 }
 return <Card className="panel incident-panel" role="region" aria-labelledby="incidents-title" onKeyDown={event=>{
  if(event.key==='Escape'){event.preventDefault();close();}
 }}>
  <CardHeader className="panel-heading product-panel-heading">
   <CardTitle><h2 id="incidents-title" ref={title} tabIndex={-1}>이상 사건</h2></CardTitle>
   <Button size="sm" variant="outline" onClick={()=>setReload(count=>count+1)} disabled={loading||reviewing}><RefreshCw size={14} aria-hidden="true"/>새로고침</Button>
  </CardHeader>
  <CardContent className="panel-body">
   <label className="incident-filter"><input type="checkbox" checked={showAll} onChange={event=>{setShowAll(event.target.checked);setReviewMessage(null);}} disabled={!deviceId||loading||reviewing}/>모든 장비 표시</label>
   {data?.captureUnavailable&&<p role="alert" className="chart-note">새 사건을 저장하지 못했습니다. 저장 장치 상태를 확인하세요.</p>}
   {data?.hasMore&&<p className="chart-note">최근 사건 일부를 표시합니다. 장비를 선택하면 범위를 좁힐 수 있습니다.</p>}
   {loading?<p className="chart-note" role="status">사건 목록을 읽는 중…</p>
    :failure?<p className="chart-note" role="status">{failure}</p>
    :!data?.incidents.length?<p className="chart-note" role="status">이 범위에서 저장된 사건이 없습니다.</p>
    :<div className="incident-layout">
     <ul className="incident-list" aria-label="저장된 사건">
      {data.incidents.map(incident=><li key={incident.id}><button type="button" aria-pressed={incident.id===selectedId} disabled={reviewing} onClick={()=>{
       setSelectedId(incident.id);setReviewMessage(null);detailHeading.current?.focus();
      }}>
       <strong>{incident.deviceId}</strong><span>{incidentState(incident.state)} · {incident.reviewedAtUtc?'확인 완료':'미확인'}</span>
       <time dateTime={incident.createdAtUtc} title={incident.createdAtUtc}>{stamp(incident.createdAtUtc)}</time>
      </button></li>)}
     </ul>
     {selected&&<section className="incident-detail" aria-labelledby="incident-detail-title">
      <h3 id="incident-detail-title" ref={detailHeading} tabIndex={-1}>
       {selected.details?.reasonCode?reasonLabels[selected.details.reasonCode]??selected.details.reasonCode:'계측 경고'}
      </h3>
      <dl className="incident-evidence">
       <dt>사건 ID</dt><dd>{selected.id}</dd>
       <dt>최초 관측</dt><dd><time dateTime={selected.createdAtUtc} title={selected.createdAtUtc}>{stamp(selected.createdAtUtc)}</time></dd>
       <dt>상태</dt><dd>{incidentState(selected.state)}</dd>
       <dt>판정 버전</dt><dd>{selected.modelVersion??'기록 없음'}</dd>
       <dt>근거 태그</dt><dd>{signals.length?signals.map(key=>signalLabels[key]??key).join(', '):'기록 없음'}</dd>
       {selected.details?.windowStartUtc&&selected.details.windowEndUtc&&<><dt>판정 관측 구간</dt><dd>{stamp(selected.details.windowStartUtc)} – {stamp(selected.details.windowEndUtc)}</dd></>}
       <dt>운영자 확인</dt><dd>{selected.reviewedAtUtc?<time dateTime={selected.reviewedAtUtc} title={selected.reviewedAtUtc}>{stamp(selected.reviewedAtUtc)}</time>:'미확인'}</dd>
      </dl>
      <div className="product-panel-actions">
       {!selected.reviewedAtUtc&&<Button size="sm" variant="outline" disabled={reviewing} onClick={()=>{void markReviewed(selected);}}>{reviewing?'저장 중…':'사건 확인 기록'}</Button>}
       {archiveTarget&&<Button size="sm" variant="outline" onClick={()=>openArchive(archiveTarget)}><Archive size={14} aria-hidden="true"/>보관 세션 열기</Button>}
      </div>
      {reviewMessage&&<p role="status" className="chart-note">{reviewMessage}</p>}
      <p className="chart-note">운영자 확인과 장비의 관측 복귀는 별도로 기록합니다.</p>
     </section>}
    </div>}
  </CardContent>
 </Card>;
}
