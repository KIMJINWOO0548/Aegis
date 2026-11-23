import {useEffect,useRef,useState,type FormEvent} from 'react';
import {Button} from '@/components/ui/button';
import {Card,CardContent,CardHeader,CardTitle} from '@/components/ui/card';
import {isArchiveSessionId,parseArchiveCatalog,type ArchiveSession} from './archive-sessions';
import HistoricalFlowAnalysis from './HistoricalFlowAnalysis';

const availableModel='ewma-flow-dev-1';
const descriptionLimit=300;
type ModelId=typeof availableModel|null;
type SelectionMode='MANUAL'|'AUTO';
type Profile={
 mode:SelectionMode;modelId:ModelId;description:string;historySessionId:string|null;version:number;
};
type ProfileResult={
 schemaVersion:'analysis-profile-1';profile:Profile&{deviceId:string};
 active:{modelId:ModelId;state:'DEVELOPMENT_RULE'|'UNAVAILABLE'};
 recommendation:{modelId:ModelId;state:'RULE_AVAILABLE'|'NO_QUALIFIED_MODEL';reasonCode:string};
};

function parseProfile(value:unknown,deviceId:string):ProfileResult {
 if(value===null||typeof value!=='object')throw Error();
 const data=value as Partial<ProfileResult>;
 const profile=data.profile;
 if(data.schemaVersion!=='analysis-profile-1'||!profile||profile.deviceId!==deviceId||
  (profile.mode!=='MANUAL'&&profile.mode!=='AUTO')||(profile.modelId!==null&&profile.modelId!==availableModel)||
  typeof profile.description!=='string'||(profile.historySessionId!==null&&!isArchiveSessionId(profile.historySessionId))||
  !Number.isSafeInteger(profile.version)||profile.version<0||!data.active||!data.recommendation)throw Error();
 return data as ProfileResult;
}

function draftOf(profile:Profile):Profile {
 return {mode:profile.mode,modelId:profile.modelId,description:profile.description,historySessionId:profile.historySessionId,version:profile.version};
}

export default function AnalysisProfile({deviceId,close}:{deviceId:string;close:()=>void}){
 const [current,setCurrent]=useState<ProfileResult|null>(null);
 const [draft,setDraft]=useState<Profile|null>(null);
 const [sessions,setSessions]=useState<ArchiveSession[]>([]);
 const [sessionStatus,setSessionStatus]=useState('이전 세션 확인 중…');
 const [status,setStatus]=useState('저장된 분석 설정을 확인하는 중…');
 const [loading,setLoading]=useState(true);
 const [saving,setSaving]=useState(false);
 const [loadNumber,setLoadNumber]=useState(0);
 const [conflict,setConflict]=useState(false);
 const title=useRef<HTMLHeadingElement>(null);
 const saveRequest=useRef<AbortController|null>(null);
 const path='/api/analysis-profile/'+encodeURIComponent(deviceId);
 const unchanged=!!current&&!!draft&&JSON.stringify(draftOf(current.profile))===JSON.stringify(draft);
 useEffect(()=>{title.current?.focus();return()=>saveRequest.current?.abort();},[]);
 useEffect(()=>{
  const request=new AbortController();
  setLoading(true);setStatus('저장된 분석 설정을 확인하는 중…');
  fetch(path,{credentials:'same-origin',signal:request.signal})
   .then(async response=>{
    if(!response.ok)throw Error();
    const data=parseProfile(await response.json(),deviceId);
    if(request.signal.aborted)return;
    setCurrent(data);setDraft(draftOf(data.profile));setConflict(false);setStatus('');
   })
   .catch(()=>{if(!request.signal.aborted)setStatus('분석 설정을 읽지 못했습니다. 연결을 확인한 뒤 다시 불러오세요.');})
   .finally(()=>{if(!request.signal.aborted)setLoading(false);});
  return()=>request.abort();
 },[deviceId,loadNumber,path]);
 useEffect(()=>{
  const request=new AbortController();
  let pending=false;
  const refresh=async()=>{
   if(pending||saveRequest.current||document.hidden)return;
   pending=true;
   try{
    const response=await fetch(path,{credentials:'same-origin',signal:request.signal});
    if(!response.ok)return;
    const runtime=parseProfile(await response.json(),deviceId);
    if(!request.signal.aborted)setCurrent(previous=>previous?{
     ...previous,active:runtime.active,recommendation:runtime.recommendation,
    }:previous);
   }catch{/* Keep the last successful response when an individual refresh fails. */}
   finally{pending=false;}
  };
  const interval=window.setInterval(()=>{void refresh();},2500);
  return()=>{clearInterval(interval);request.abort();};
 },[deviceId,path]);
 useEffect(()=>{
  const request=new AbortController();
  fetch('/api/archive-sessions',{credentials:'same-origin',signal:request.signal})
   .then(async response=>{
    if(!response.ok)throw Error();
    const data=parseArchiveCatalog(await response.json());
    if(request.signal.aborted)return;
    setSessions(data.sessions);
    setSessionStatus(data.sessions.length?'참조할 과거 기록을 선택할 수 있습니다.':'저장된 이전 세션이 없습니다.');
   })
   .catch(()=>{if(!request.signal.aborted)setSessionStatus('이전 세션을 확인하지 못했습니다.');});
  return()=>request.abort();
 },[]);

 function update(fields:Partial<Profile>){setDraft(previous=>previous?{...previous,...fields}:previous);if(!conflict)setStatus('');}
 async function save(event:FormEvent){
  event.preventDefault();
  if(!draft||saveRequest.current||conflict)return;
  const request=new AbortController();
  saveRequest.current=request;
  setStatus('저장 중…');setSaving(true);
  try{
   const response=await fetch(path,{
    method:'PUT',credentials:'same-origin',signal:request.signal,
    headers:{'Content-Type':'application/json'},body:JSON.stringify(draft),
   });
   if(request.signal.aborted)return;
   if(response.status===409){
    setConflict(true);setStatus('다른 작업에서 설정이 바뀌었습니다. 다시 불러온 뒤 변경할 내용을 확인하세요.');return;
   }
   if(!response.ok)throw Error();
   const data=parseProfile(await response.json(),deviceId);
   if(request.signal.aborted)return;
   setCurrent(data);setDraft(draftOf(data.profile));setStatus('분석 설정을 저장했습니다.');
  }catch{if(!request.signal.aborted)setStatus('저장 결과를 확인하지 못했습니다. 연결을 확인하거나 설정을 다시 불러오세요.');}
  finally{if(!request.signal.aborted){saveRequest.current=null;setSaving(false);}}
 }

 return <Card className="panel analysis-panel" role="region" aria-labelledby="analysis-title" onKeyDown={event=>{
  if(event.key==='Escape'&&unchanged){event.preventDefault();close();}
 }}>
  <CardHeader className="panel-heading product-panel-heading">
   <CardTitle><h2 id="analysis-title" ref={title} tabIndex={-1}>분석 설정</h2></CardTitle>
   <p className="chart-note">{deviceId}</p>
  </CardHeader>
  <CardContent className="panel-body">
   {current&&<dl className="incident-evidence analysis-summary">
    <dt>현재 적용</dt><dd>{current.active.state==='DEVELOPMENT_RULE'?'개발용 유량 EWMA':'적용할 수 있는 모델 없음'}{current.active.modelId&&' · '+current.active.modelId}</dd>
    <dt>추천 후보</dt><dd>{current.recommendation.modelId===availableModel?'개발용 유량 EWMA':'검증된 후보 없음'}</dd>
    <dt>선정 근거</dt><dd>{current.recommendation.reasonCode==='REGISTERED_DEVELOPMENT_RULE_ONLY'?'현재 수집기에서 개발용 유량 규칙 확인':'현재 연결에서 적용할 수 있는 규칙을 확인하지 못했습니다.'}</dd>
    <dt>학습 모델</dt><dd>등록 안 됨</dd>
    <dt>설정 버전</dt><dd>{current.profile.version||'저장 전'}</dd>
   </dl>}
   {draft&&<form className="analysis-form" aria-busy={saving||loading} onSubmit={save}>
    <fieldset disabled={saving||loading}>
     <legend>모델 선택 방법</legend>
     <label><input type="radio" name="analysis-mode" checked={draft.mode==='MANUAL'} onChange={()=>update({mode:'MANUAL',modelId:availableModel})}/>수동 고정</label>
     <label><input type="radio" name="analysis-mode" checked={draft.mode==='AUTO'} onChange={()=>update({mode:'AUTO',modelId:null})}/>자동 추천</label>
    </fieldset>
    {draft.mode==='MANUAL'&&<label>수동 모델
     <select className="instrument-form-select" value={draft.modelId??availableModel} onChange={()=>update({modelId:availableModel})} disabled={saving||loading}>
      <option value={availableModel}>개발용 유량 EWMA</option>
     </select>
    </label>}
    <label>데이터와 설비 동작 설명
     <textarea value={draft.description} onChange={event=>update({description:event.target.value})} disabled={saving||loading} maxLength={descriptionLimit} rows={3} aria-describedby="analysis-description-length"/>
    </label>
    <p id="analysis-description-length" className="chart-note">{draft.description.length} / {descriptionLimit}자</p>
    <label>참조할 이전 세션
     <select className="instrument-form-select" value={draft.historySessionId??''} onChange={event=>update({historySessionId:event.target.value||null})} disabled={saving||loading} aria-describedby="analysis-session-status">
      <option value="">선택 안 함</option>
      {draft.historySessionId&&!sessions.some(session=>session.sessionId===draft.historySessionId)&&<option value={draft.historySessionId}>{draft.historySessionId}</option>}
      {sessions.map(session=><option key={session.sessionId} value={session.sessionId}>{session.sessionId}</option>)}
     </select>
    </label>
    <p id="analysis-session-status" className="chart-note">{sessionStatus}</p>
    <p className="chart-note">설명과 참조 이력을 프로필에 보존합니다. 과거 기록으로 학습하거나 보정한 모델은 아직 등록되지 않았습니다.</p>
    <div className="product-panel-actions"><Button type="submit" size="sm" variant="outline" disabled={saving||loading||conflict||unchanged}>{saving?'저장 중…':'설정 저장'}</Button></div>
   </form>}
   <p role="status" className="chart-note">{status||(!unchanged&&draft?'저장하지 않은 변경이 있습니다.':'')}</p>
   {(conflict||!current&&!loading)&&<Button size="sm" variant="outline" onClick={()=>setLoadNumber(number=>number+1)} disabled={loading||saving}>설정 다시 불러오기</Button>}
   {draft?.historySessionId&&<HistoricalFlowAnalysis key={draft.historySessionId} sessionId={draft.historySessionId} deviceId={deviceId}/>}
  </CardContent>
 </Card>;
}
