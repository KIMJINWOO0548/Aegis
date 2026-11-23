const modelNames={
 'setpoint-flow-baseline':'설정값 기준',
 'learned-flow-bias':'학습한 편향 보정',
 'learned-flow-ridge':'학습한 릿지 회귀',
};
type ModelId=keyof typeof modelNames;
type ForecastModel={modelId:ModelId;maeLpm:number;improvementLpm:number;improvementPercent:number|null};
type Evaluation={
 modeId:string;runId:string;trainRows:number;testRows:number;trainFromUtc:string;trainToUtc:string;testFromUtc:string;testToUtc:string;
 featureKeys:string[];models:ForecastModel[];preferredModelId:ModelId;reasonCode:'LOWEST_HELD_OUT_MAE'|'SIMPLEST_WITHIN_TOLERANCE';
};
type ForecastReport={
 schemaVersion:'learned-flow-forecast-1';scope:'ARCHIVE_FORECAST_ERROR_ONLY';labelEvidence:'NOT_PROVIDED';
 status:'HOLDOUT_EVALUATED'|'NO_COMPARABLE_HOLDOUT';
 sampling:{intervalSeconds:number;lookbackSeconds:number;rawRowsExamined:number;selectedRows:number};
 embargoSeconds:number;evaluations:Evaluation[];
};
const finite=(value:unknown):value is number=>typeof value==='number'&&Number.isFinite(value);
const validCount=(value:unknown)=>finite(value)&&Number.isSafeInteger(value)&&value>=0;
const validStamp=(value:unknown)=>typeof value==='string'&&Number.isFinite(Date.parse(value));
const display=new Intl.NumberFormat('ko-KR',{maximumFractionDigits:3});

function isReport(value:unknown):value is ForecastReport{
 if(typeof value!=='object'||value===null)return false;
 const report=value as Partial<ForecastReport>;
 if(report.schemaVersion!=='learned-flow-forecast-1'||report.scope!=='ARCHIVE_FORECAST_ERROR_ONLY'||
  report.labelEvidence!=='NOT_PROVIDED'||(report.status!=='HOLDOUT_EVALUATED'&&report.status!=='NO_COMPARABLE_HOLDOUT')||
  !report.sampling||!Array.isArray(report.evaluations)||!validCount(report.embargoSeconds))return false;
 const {sampling}=report;
 if(!validCount(sampling.intervalSeconds)||!validCount(sampling.lookbackSeconds)||
  !validCount(sampling.rawRowsExamined)||!validCount(sampling.selectedRows)||sampling.selectedRows>sampling.rawRowsExamined)return false;
 return report.evaluations.every(row=>row&&typeof row.modeId==='string'&&typeof row.runId==='string'&&
  validCount(row.trainRows)&&validCount(row.testRows)&&[row.trainFromUtc,row.trainToUtc,row.testFromUtc,row.testToUtc].every(validStamp)&&
  Array.isArray(row.featureKeys)&&row.featureKeys.every(key=>typeof key==='string')&&
  Array.isArray(row.models)&&row.models.every(model=>model&&Object.hasOwn(modelNames,model.modelId)&&
   finite(model.maeLpm)&&model.maeLpm>=0&&finite(model.improvementLpm)&&(model.improvementPercent===null||finite(model.improvementPercent)))&&
  row.models.some(model=>model.modelId===row.preferredModelId)&&
  (row.reasonCode==='LOWEST_HELD_OUT_MAE'||row.reasonCode==='SIMPLEST_WITHIN_TOLERANCE'));
}

export default function LearnedFlowForecast({data}:{data:unknown}){
 if(!isReport(data))return <p className="chart-note">학습 예측 비교의 응답 형식을 확인할 수 없습니다.</p>;
 return <section className="learned-flow-comparison" aria-labelledby="learned-forecast-title">
  <h4 id="learned-forecast-title">학습 유량 예측 비교</h4>
  <p className="chart-note">{display.format(data.sampling.intervalSeconds)}초 간격 · 최근 {display.format(data.sampling.lookbackSeconds)}초 · 후보 {display.format(data.sampling.rawRowsExamined)}행 중 {display.format(data.sampling.selectedRows)}행 사용 · 학습과 시험 사이 {display.format(data.embargoSeconds)}초 제외</p>
  <p className="chart-note">비교 지표는 별도 시험 구간의 유량 예측 오차입니다. 실시간 적용과 경보 성능은 검증 범위에 포함되지 않습니다.</p>
  {data.status==='NO_COMPARABLE_HOLDOUT'||!data.evaluations.length
   ?<p className="chart-note">비교할 학습·시험 구간이 부족합니다.</p>
   :data.evaluations.map((row,index)=><section key={JSON.stringify([row.modeId,row.runId,index])} aria-label={row.modeId+' 예측 비교'}>
    <h5>{row.modeId} · {modelNames[row.preferredModelId]}</h5>
    <p className="chart-note">{row.reasonCode==='SIMPLEST_WITHIN_TOLERANCE'?'허용 오차 안에서 더 단순한 모델을 선택했습니다.':'별도 시험 구간에서 오차가 가장 작았습니다.'}</p>
    <div className="table-scroll" role="region" aria-label={row.modeId+' 모델별 평균 절대 오차'} tabIndex={0}>
     <table className="signal-table">
      <thead><tr><th scope="col">유량 예측 방식</th><th scope="col">MAE (L/min)</th><th scope="col">기준 대비 개선</th></tr></thead>
      <tbody>{row.models.map(model=><tr key={model.modelId}>
       <th scope="row">{modelNames[model.modelId]}</th>
       <td>{display.format(model.maeLpm)}</td>
       <td>{display.format(model.improvementLpm)} L/min{model.improvementPercent===null?'':' · '+display.format(model.improvementPercent)+'%'}</td>
      </tr>)}</tbody>
     </table>
    </div>
    <details>
     <summary>학습·시험 구간과 입력</summary>
     <dl className="incident-evidence">
      <dt>수집 실행</dt><dd>{row.runId}</dd>
      <dt>학습 {display.format(row.trainRows)}행</dt><dd>{row.trainFromUtc} – {row.trainToUtc}</dd>
      <dt>시험 {display.format(row.testRows)}행</dt><dd>{row.testFromUtc} – {row.testToUtc}</dd>
      <dt>입력 열</dt><dd>{row.featureKeys.join(', ')||'없음'}</dd>
     </dl>
    </details>
   </section>)}
 </section>;
}
