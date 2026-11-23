import {useEffect,useRef,useState} from 'react';
import {Button} from '@/components/ui/button';
import {isArchiveSessionId} from './archive-sessions';
import LearnedFlowForecast from './LearnedFlowForecast';

type ModeSummary={modeId:string;samples:number;validPairs:number;medianResidualLpm:number|null;medianAbsoluteDeviationLpm:number|null};
type ForecastSummary={modeId:string;trainRows:number;testRows:number;setpointMaeLpm:number;biasAdjustedMaeLpm:number};
type ModelSummary={modelId:string;status:string;reasonCode:string};
type FlowAnalysis={
 schemaVersion:'flow-analysis-1';sessionId:string;deviceId:string;source:'ARCHIVED_SYNTHETIC';
 sampleLimit:number;scannedRows:number;validPairs:number;invalidPairs:number;zeroFlowRows:number;truncated:boolean;
 firstReceivedAtUtc:string|null;lastReceivedAtUtc:string|null;labelEvidence:'NOT_PROVIDED';
 modeSummaries:ModeSummary[];candidates:ModelSummary[];recommendation:Omit<ModelSummary,'modelId'>&{modelId:string|null};
 forecastComparison:ForecastSummary[];
 learnedForecastReport?:unknown;
};

const modelLabels:Record<string,string>={'rule-ewma-flow':'유량 EWMA','research-cusum-flow':'연구용 유량 CUSUM'};
const inputReasons:Record<string,string>={
 SUPPORTED_INPUT:'필요한 유량 입력이 있습니다.',
 TOO_FEW_VALID_PAIRS:'유효한 실측·설정 쌍이 부족합니다.',
 VALID_PAIR_RATIO_LOW:'유효한 입력 비율이 낮습니다.',
 STABLE_MODE_REQUIRED:'운전 모드가 안정된 구간이 필요합니다.',
 INSUFFICIENT_MODE_DURATION:'같은 운전 모드의 기록 기간이 짧습니다.',
};
const metric=new Intl.NumberFormat('ko-KR',{maximumFractionDigits:3});
function measure(value:unknown){
 return typeof value==='number'&&Number.isFinite(value)?metric.format(value):'—';
}
function decodeFlowAnalysis(value:unknown,sessionId:string,deviceId:string):FlowAnalysis {
 if(typeof value!=='object'||value===null)throw Error();
 const result=value as Partial<FlowAnalysis>;
 if(result.schemaVersion!=='flow-analysis-1'||result.sessionId!==sessionId||result.deviceId!==deviceId||
  result.source!=='ARCHIVED_SYNTHETIC'||result.labelEvidence!=='NOT_PROVIDED'||!result.recommendation||
  !Array.isArray(result.modeSummaries)||!Array.isArray(result.candidates)||!Array.isArray(result.forecastComparison))throw Error();
 return result as FlowAnalysis;
}

export default function HistoricalFlowAnalysis({sessionId,deviceId}:{sessionId:string;deviceId:string}){
 const [data,setData]=useState<FlowAnalysis|null>(null);
 const [busy,setBusy]=useState(false);
 const [status,setStatus]=useState('');
 const activeRequest=useRef<AbortController|null>(null);
 useEffect(()=>()=>activeRequest.current?.abort(),[]);

 async function analyze(){
  if(!isArchiveSessionId(sessionId)||activeRequest.current)return;
  const request=new AbortController();
  activeRequest.current=request;
  setBusy(true);setData(null);setStatus('선택한 보관 기록을 읽는 중…');
  const timeout=window.setTimeout(()=>{
   request.abort();
   if(activeRequest.current===request){activeRequest.current=null;setBusy(false);setStatus('분석 응답 시간이 초과되었습니다. 다시 시도하세요.');}
  },20000);
  try{
   const response=await fetch('/api/archive-sessions/'+sessionId+'/flow-analysis?'+new URLSearchParams({deviceId}),
    {credentials:'same-origin',signal:request.signal});
   if(response.status===409){if(!request.signal.aborted)setStatus('다른 보관 분석이 진행 중입니다. 완료 후 다시 시도하세요.');return;}
   if(!response.ok)throw Error();
   const outcome=decodeFlowAnalysis(await response.json(),sessionId,deviceId);
   if(request.signal.aborted)return;
   setData(outcome);setStatus('보관 기록 분석을 완료했습니다.');
  }catch{if(!request.signal.aborted)setStatus('이전 기록을 분석하지 못했습니다. 선택한 세션과 장비를 확인하세요.');}
  finally{
   clearTimeout(timeout);
   if(activeRequest.current===request){activeRequest.current=null;setBusy(false);}
  }
 }
 function cancel(){
  activeRequest.current?.abort();
  activeRequest.current=null;
  setBusy(false);setStatus('결과 요청을 취소했습니다.');
 }

 return <section className="history-analysis" aria-labelledby="flow-analysis-title" aria-busy={busy}>
  <h3 id="flow-analysis-title">이전 유량 기록 분석</h3>
  <div className="product-panel-actions">
   <Button size="sm" variant="outline" disabled={busy} onClick={()=>{void analyze();}}>{busy?'분석 중…':'보관 기록 분석'}</Button>
   {busy&&<Button size="sm" variant="outline" onClick={cancel}>취소</Button>}
  </div>
  {status&&<p role="status" className="chart-note">{status}</p>}
  {data&&<>
   <dl className="incident-evidence">
    <dt>읽은 원본 행</dt><dd>{measure(data.scannedRows)}개 / 한도 {measure(data.sampleLimit)}개{data.truncated?' · 일부만 분석':''}</dd>
    <dt>유효 유량 쌍</dt><dd>{measure(data.validPairs)}개 · 제외 {measure(data.invalidPairs)}개</dd>
    <dt>실제 유량 0</dt><dd>{measure(data.zeroFlowRows)}개</dd>
    {data.firstReceivedAtUtc&&data.lastReceivedAtUtc&&<><dt>최초 수신 범위</dt><dd>{data.firstReceivedAtUtc} – {data.lastReceivedAtUtc}</dd></>}
    <dt>추천 결과</dt><dd>{data.recommendation.modelId?modelLabels[data.recommendation.modelId]??data.recommendation.modelId:'데이터 부족'}</dd>
   </dl>
   <p className="chart-note">합성 보관 기록입니다. 확인된 정상·고장 사건 라벨이 없어 오경보와 고장 검출 성능은 미검증입니다.</p>
   {!!data.candidates.length&&<ul className="model-candidates" aria-label="비교 후보">{data.candidates.map(candidate=>
    <li key={candidate.modelId}><strong>{modelLabels[candidate.modelId]??candidate.modelId}</strong> · {inputReasons[candidate.reasonCode]??(candidate.status==='INPUT_SUFFICIENT'?'입력 조건 충족':'입력 조건 부족')}</li>
   )}</ul>}
   {!!data.modeSummaries.length&&<div className="table-scroll" role="region" aria-label="운전 모드별 유량 편차" tabIndex={0}>
    <table className="signal-table">
     <thead><tr><th scope="col">운전 모드</th><th scope="col">유효 쌍 / 원본</th><th scope="col">실측−설정 중앙값</th><th scope="col">절대편차 중앙값</th></tr></thead>
     <tbody>{data.modeSummaries.map(row=><tr key={row.modeId}>
      <th scope="row">{row.modeId}</th><td>{measure(row.validPairs)} / {measure(row.samples)}</td><td>{measure(row.medianResidualLpm)}</td><td>{measure(row.medianAbsoluteDeviationLpm)}</td>
     </tr>)}</tbody>
    </table>
   </div>}
   {!!data.forecastComparison.length&&<div className="table-scroll" role="region" aria-label="보정 전후 유량 예측 오차" tabIndex={0}>
    <table className="signal-table">
     <thead><tr><th scope="col">운전 모드</th><th scope="col">학습 / 시험</th><th scope="col">설정값 MAE</th><th scope="col">편향 보정 MAE</th></tr></thead>
     <tbody>{data.forecastComparison.map(row=><tr key={row.modeId}>
      <th scope="row">{row.modeId}</th><td>{measure(row.trainRows)} / {measure(row.testRows)}</td><td>{measure(row.setpointMaeLpm)}</td><td>{measure(row.biasAdjustedMaeLpm)}</td>
     </tr>)}</tbody>
    </table>
   </div>}
   {data.learnedForecastReport!==undefined&&<LearnedFlowForecast data={data.learnedForecastReport}/>}
  </>}
 </section>;
}
