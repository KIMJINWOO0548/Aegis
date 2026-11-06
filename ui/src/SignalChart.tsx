import {memo,useEffect,useRef,useState,useId,useMemo} from 'react';
import {init,use,type ECharts} from 'echarts/core';
import {LineChart} from 'echarts/charts';
import {GridComponent,TooltipComponent,LegendComponent} from 'echarts/components';
import {CanvasRenderer} from 'echarts/renderers';
import {formatValue,historyWindow,type Point,type HistoryRange} from './data';
import {freezeHistory,inspectHistory,pointState,signalSeries,type FrozenHistory} from './investigation';
use([LineChart,GridComponent,TooltipComponent,LegendComponent,CanvasRenderer]);

function SignalChart({points,label,unit,historical,context}:{points:Point[];label:string;unit:string;historical:boolean;context:string}){
 const host=useRef<HTMLDivElement>(null);const chart=useRef<ECharts|null>(null);
 const [range,setRange]=useState<HistoryRange>('all');const [frozen,setFrozen]=useState<FrozenHistory|null>(null);const rangeId=useId();
 const inspectionPoints=frozen?.points??points;
 const visiblePoints=useMemo(()=>historyWindow(inspectionPoints,range),[inspectionPoints,range]);
 const summary=useMemo(()=>inspectHistory(visiblePoints),[visiblePoints]);
 const chartSeries=useMemo(()=>signalSeries(visiblePoints),[visiblePoints]);
 const rows=useMemo(()=>visiblePoints.slice().reverse(),[visiblePoints]);
 const muted=historical||frozen!==null;
 useEffect(()=>{
  if(!host.current)return;
  const instance=init(host.current,undefined,{renderer:'canvas'});chart.current=instance;
  const resize=new ResizeObserver(()=>instance.resize());resize.observe(host.current);
  return()=>{resize.disconnect();instance.dispose();chart.current=null;};
 },[]);
 useEffect(()=>{
  chart.current?.setOption({animation:false,textStyle:{fontFamily:'Pretendard, sans-serif'},grid:{left:54,right:22,top:24,bottom:35},tooltip:{trigger:'axis',renderMode:'richText'},xAxis:{type:'time',axisLine:{lineStyle:{color:'#e4e9f1'}},axisLabel:{color:'#5d6d83',fontSize:10,hideOverlap:true},splitLine:{show:false}},yAxis:{type:'value',scale:true,name:unit,nameTextStyle:{fontSize:10,color:'#5d6d83'},axisLabel:{color:'#5d6d83',fontSize:10},splitLine:{lineStyle:{color:'#edf1f7',type:'dashed'}}},series:[{...chartSeries,name:label,lineStyle:{width:2,color:muted?'#71839d':'#3973e6'},itemStyle:{color:muted?'#71839d':'#3973e6'},areaStyle:{color:muted?'#f0f3f8':'#eef4ff'}}]},true);
 },[chartSeries,label,unit,muted]);
 return <div className="signal-investigation">
  <div className="chart-window">
   <label htmlFor={rangeId}>표시 범위</label><select id={rangeId} value={range} onChange={event=>setRange(event.target.value==='all'?'all':event.target.value==='30'?30:60)}><option value="all">보관 중인 전체</option><option value="30">마지막 보관 시각 기준 30초</option><option value="60">마지막 보관 시각 기준 60초</option></select>
   <button className="inspection-toggle" type="button" aria-pressed={frozen!==null} onClick={()=>setFrozen(frozen?null:freezeHistory(points,Date.now()))}>{frozen?'실시간 요약 따라가기':'조사 화면 고정'}</button>
  </div>
  <p className={`inspection-state${frozen?' is-frozen':''}`} role="status">{frozen?`고정된 조사 화면 · ${new Date(frozen.capturedAt).toISOString()} 캡처 · 수집은 계속됩니다.`:historical?'과거 UI 요약 · 현재 상태로 해석하지 마세요.':'수신 UI 요약을 따라가는 중'}<span>{context} · {label} ({unit})</span></p>
  <p className="chart-window-time">{summary.start!==null&&summary.end!==null?`${new Date(summary.start).toISOString()} – ${new Date(summary.end).toISOString()}`:'보관된 요약 없음'} · UTC</p>
  <div ref={host} className="chart" role="img" aria-label={`${label} ${unit}, ${summary.summaries}개 1Hz UI 요약${muted?', 과거 조사 화면':''}. 정확한 값은 아래 UI 요약 표에 있습니다.`}/>
  <dl className="inspection-summary"><div><dt>표시 / 보관 요약</dt><dd>{summary.summaries} / {inspectionPoints.length}</dd></div><div><dt>사용 가능 / 값 없음·단절</dt><dd>{summary.valid} / {summary.unavailable}</dd></div><div><dt>2.5초 초과 간격</dt><dd>{summary.gaps}</dd></div><div><dt>요약 최솟값 / 최댓값</dt><dd>{formatValue(summary.min)} / {formatValue(summary.max)} <small>{unit}</small></dd></div></dl>
  <details className="inspection-details"><summary>UI 요약 표로 조사 ({summary.summaries}개)</summary><p className="chart-note">최근 시각부터 표시합니다. 값 없음과 단절 표시는 0이 아닙니다. 원시 100Hz 신호·DB 보관 이력이 아니며, 요약 사이의 짧은 변화는 복원할 수 없습니다.</p>
   <div className="inspection-table-scroll" tabIndex={0} role="region" aria-label={`${context} ${label} UI 요약 표`}><table className="inspection-table"><caption>{context} · {label} · {frozen?'고정된 조사 화면':'수신 UI 요약'} · UTC</caption><thead><tr><th scope="col">보관 시각 (UTC)</th><th scope="col">값 ({unit})</th><th scope="col">요약 상태</th></tr></thead><tbody>{rows.map(point=><tr key={point.id}><th scope="row"><time dateTime={new Date(point.t).toISOString()}>{new Date(point.t).toISOString()}</time></th><td>{point.v===null?'값 없음':formatValue(point.v)}</td><td>{pointState(point)}</td></tr>)}</tbody></table></div>
  </details>
 </div>;
}

export default memo(SignalChart);
