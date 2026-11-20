import {useEffect,useRef,useState,type FormEvent} from 'react';
import {Button} from '@/components/ui/button';
import {Input} from '@/components/ui/input';
import {Card,CardHeader,CardTitle,CardContent} from '@/components/ui/card';
import {signalLabels} from './data';

type RecordRow={identity:{storeId:string;storeKind:string;nativeKey:unknown[];nativeKeyText?:string};deviceId:string;
 firstReceivedAtUtc:string;sourceMetadata:{observedAtUtc:string|null;timestampStatus:string;runId?:string|null};
 provenance:{declaredOrigin:string};signals:{signalKey:string;present:boolean;reading:{valueText:string|null;unit:string;quality:string}|null}[]};
type RunSelection={storeId:string;deviceId:string;runId:string};
type RunEvidence=RunSelection&{ state:'recorded'|'not_found'|'not_applicable'; storedRowsInReceivedWindow:number|null;
 recordedGaps:{firstSequence:string;lastSequence:string;reason:'REPLAY_OVERFLOW'}[]|null };
const issues:Record<string,string>={
 INVALID_QUERY:'UTC 범위와 장비·실행 선택을 확인하세요.',
 QUERY_LIMIT:'조회 한도를 초과했습니다. 수신 시간 범위를 줄이세요.',
 SOURCE_UNAVAILABLE:'저장소를 읽을 수 없습니다. 연결을 확인한 뒤 다시 조회하세요.',
 SOURCE_INVALID:'저장소의 구조 또는 무결성을 확인할 수 없습니다.',
 SOURCE_BINDING_MISMATCH:'저장소 식별이 일치하지 않습니다. 로컬 설정을 확인하세요.',
 DUPLICATE_SOURCE:'같은 저장소가 여러 번 설정됐습니다.',
};

export default function StoredHistory({deviceId,initialSignal,returnToSignal}:{
 deviceId?:string;initialSignal?:string;returnToSignal?:()=>void;
}){
 const [from,setFrom]=useState(new Date(Date.now()-3600000).toISOString());
 const [to,setTo]=useState(new Date().toISOString());
 const [signals,setSignals]=useState(initialSignal??'');const [rows,setRows]=useState<RecordRow[]|null>(null);
 const [status,setStatus]=useState('로컬 보관 소스가 설정된 경우 조회할 수 있습니다.');const [busy,setBusy]=useState(false);
 const [run,setRun]=useState<RunSelection|null>(null),[evidence,setEvidence]=useState<RunEvidence|null>(null);
 const request=useRef<AbortController|null>(null);
 const title=useRef<HTMLHeadingElement|null>(null);
 const resultHeading=useRef<HTMLHeadingElement|null>(null);
 useEffect(()=>()=>{request.current?.abort();request.current=null;},[]);
 useEffect(()=>{if(initialSignal)title.current?.focus();},[initialSignal]);
 useEffect(()=>{if(evidence)resultHeading.current?.focus();},[evidence]);
 function resetSelection(){setRun(null);setEvidence(null);setRows(null);setStatus('조건을 적용하려면 보관 이력을 조회하세요.');}
 async function query(selection:RunSelection|null){
  request.current?.abort();
  const controller=new AbortController();request.current=controller;
  const timeout=window.setTimeout(()=>controller.abort(),10000);
  setRun(selection);setBusy(true);setRows(null);setEvidence(null);setStatus('보관 이력 조회 중…');
  try{
   const query=new URLSearchParams({receivedFromUtc:from,receivedToUtc:to});if(signals)query.set('signals',signals);
   const selectedDevice=selection?.deviceId??deviceId;
   if(selectedDevice)query.set('deviceId',selectedDevice);
   if(selection){query.set('storeId',selection.storeId);query.set('runId',selection.runId);}
   const response=await fetch('/api/stored-records?'+query,{credentials:'same-origin',signal:controller.signal});
   if(!response.ok){
    const failure=await response.json().catch(()=>null);
    if(request.current!==controller)return;
    const code=typeof failure?.code==='string'&&Object.hasOwn(issues,failure.code)?issues[failure.code]:null;
    setStatus(code??(response.status===404?'로컬 보관 소스가 설정되지 않았습니다.':response.status===413?issues.QUERY_LIMIT:response.status===400||response.status===422?issues.INVALID_QUERY:'이력을 읽을 수 없습니다. 연결을 확인하세요.'));
    return;
   }
   const result=await response.json();
   if(request.current!==controller)return;
   if(result.schemaVersion!=='stored-record-query-1'||result.selectionComplete!==true||!Array.isArray(result.records))throw new Error();
   if(selection){
    const actual:RunEvidence|undefined=result.runEvidence;
    if(!actual||actual.storeId!==selection.storeId||actual.deviceId!==selection.deviceId||actual.runId!==selection.runId
       ||!['recorded','not_found','not_applicable'].includes(actual.state))throw new Error();
    setEvidence(actual);
   }
   setRows(result.records);setStatus(`${result.records.length}개 보관 기록 · SQLite에서 복원한 이력 · 현재 연결 및 수신 지연 판단 불가`);
  }catch{if(request.current===controller)setStatus('이력을 읽을 수 없습니다. 게이트웨이 연결을 확인하세요.');}
  finally{clearTimeout(timeout);if(request.current===controller){request.current=null;setBusy(false);}}
 }
 function submit(event:FormEvent){event.preventDefault();void query(run);}
 return <Card className="panel" role="region" aria-labelledby="stored-history-title"><CardHeader className="panel-heading"><CardTitle><h2 id="stored-history-title" tabIndex={-1} ref={title}>로컬 보관 수신 이력</h2></CardTitle></CardHeader><CardContent className="panel-body">
  {initialSignal&&<div className="archive-context">
   <p>실시간에서 선택한 태그 <strong>{signalLabels[initialSignal]??initialSignal}</strong></p>
   <Button type="button" size="sm" variant="outline" onClick={returnToSignal}>선택 태그로 돌아가기</Button>
  </div>}
  <p className="section-description">원본 소스별 읽기 전용 이력입니다. 수신 시각 기준 최대 24시간 / 64개 기록. 현재 신호로 취급하지 않으며 원시 계약으로 변환하지 않습니다.</p>
  <p>장비 {run?.deviceId??deviceId??'전체'}</p>
  <form onSubmit={submit} className={initialSignal?'archive-form':'info-list'} aria-busy={busy}>
   <label>수신 시작 (UTC)<Input value={from} onChange={e=>{setFrom(e.target.value);resetSelection();}} required disabled={busy}/></label>
   <label>수신 종료 (UTC, 미포함)<Input value={to} onChange={e=>{setTo(e.target.value);resetSelection();}} required disabled={busy}/></label>
   <label>신호 키 (쉼표 구분, 비우면 기록된 신호)<Input value={signals} onChange={e=>{setSignals(e.target.value);resetSelection();}} disabled={busy}/></label>
   <Button type="submit" disabled={busy}>{busy?'조회 중…':'보관 이력 조회'}</Button>
   {run&&<Button type="button" variant="outline" disabled={busy} onClick={()=>{void query(null);}}>실행 선택 해제</Button>}
  </form><p role="status" className="chart-note">{status}</p>
  {rows&&<p className="chart-note">조회 범위: {run?.deviceId??deviceId??'모든 장비'} · {signals||'기록된 모든 신호'}<br/>수신 시각 {from} – {to} (종료 미포함)</p>}
  {evidence&&<section className="evidence">
   <h3 tabIndex={-1} ref={resultHeading}>실행 전체에 기록된 재생 한도 초과</h3>
   <p>{evidence.storeId} · {evidence.runId}</p>
   {evidence.state==='recorded'?<>
    <p>수신 창의 저장 행 {evidence.storedRowsInReceivedWindow}개 · 재수신 건수 확인 불가</p>
    {evidence.recordedGaps?.length?<ul>{evidence.recordedGaps.map(gap=>
     <li key={`${gap.firstSequence}-${gap.lastSequence}`}>순번 {gap.firstSequence===gap.lastSequence?gap.firstSequence:`${gap.firstSequence}–${gap.lastSequence}`}</li>
    )}</ul>:<p>이 실행에 기록된 재생 한도 초과가 없습니다.</p>}
    <p>순번의 연속성과 전체 수집 여부는 확인할 수 없습니다.</p>
   </>:<p>{evidence.state==='not_found'?'선택한 실행의 근거를 찾지 못했습니다.':'이 저장소에는 원시 실행 공백 근거가 없습니다.'}</p>}
  </section>}
  {rows?.map((row,index)=><article className="evidence" key={JSON.stringify([row.identity.storeId,row.identity.nativeKeyText??index])}><div>
   <h3>{row.deviceId} · {row.identity.storeId} · {row.identity.storeKind} · 선언 {row.provenance.declaredOrigin}</h3>
   <p>최초 수신 {row.firstReceivedAtUtc}<br/>원본 관측 {row.sourceMetadata.observedAtUtc??'미제공'} · {row.sourceMetadata.timestampStatus}<br/>원본 식별자 {row.identity.nativeKeyText??'확인 불가'}</p>
   {!run&&row.identity.storeKind==='RAW_V1'&&row.sourceMetadata.runId&&
    <Button type="button" variant="outline" onClick={()=>{void query({storeId:row.identity.storeId,deviceId:row.deviceId,runId:row.sourceMetadata.runId!});}}>이 실행의 공백 보기</Button>}
   <div className="table-scroll" role="region" aria-label="보관된 신호" tabIndex={0}>
    <table className="signal-table"><thead><tr><th scope="col">신호</th><th scope="col">보관값</th><th scope="col">단위</th><th scope="col">원본 품질 / 존재</th></tr></thead><tbody>{row.signals.map(signal=><tr key={signal.signalKey}><td>{signal.signalKey}</td><td>{!signal.present?'미포함':signal.reading?.valueText??'값 없음'}</td><td>{signal.reading?.unit??'—'}</td><td>{signal.present?signal.reading?.quality:'ABSENT'}</td></tr>)}</tbody></table>
   </div>
  </div></article>)}
 </CardContent></Card>;
}
