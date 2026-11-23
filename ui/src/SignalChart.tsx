import {memo,useEffect,useRef,useState,useId,useMemo,type KeyboardEvent} from 'react';
import {init,use,type ECharts} from 'echarts/core';
import {LineChart} from 'echarts/charts';
import {GridComponent,TooltipComponent,LegendComponent,MarkLineComponent} from 'echarts/components';
import {CanvasRenderer} from 'echarts/renderers';
import {formatValue,historyWindow,type Point,type HistoryRange} from './data';
import {freezeHistory,inspectHistory,movePointCursor,pointKey,pointState,signalSeries,type FrozenHistory} from './investigation';
use([LineChart,GridComponent,TooltipComponent,LegendComponent,MarkLineComponent,CanvasRenderer]);

const cursorKeys=['ArrowLeft','ArrowRight','Home','End'];
const markerStyles={
 transport:{color:'#a03e21',type:'dashed' as const},
 gap:{color:'#765526',type:'dotted' as const},
};
const timestamp=(t:number)=>new Date(t).toISOString();

function SignalChart({points,label,unit,historical,context}:{points:Point[];label:string;unit:string;historical:boolean;context:string}){
 const host=useRef<HTMLDivElement>(null);const chart=useRef<ECharts|null>(null);
 const [range,setRange]=useState<HistoryRange>('all');const [frozen,setFrozen]=useState<FrozenHistory|null>(null);const rangeId=useId();
 const [cursor,setCursor]=useState<string|null>(null);const cursorHintId=useId();
 const inspectionPoints=frozen?.points??points;
 const visiblePoints=useMemo(()=>historyWindow(inspectionPoints,range),[inspectionPoints,range]);
 const summary=useMemo(()=>inspectHistory(visiblePoints),[visiblePoints]);
 const canPlot=summary.valid>0;
 const chartSeries=useMemo(()=>signalSeries(visiblePoints),[visiblePoints]);
 const rows=useMemo(()=>visiblePoints.slice().reverse(),[visiblePoints]);
 const markers=useMemo(()=>visiblePoints.filter(point=>point.marker!==undefined),[visiblePoints]);
 const interruptionCount=markers.filter(point=>point.marker==='transport').length;
 const gapCount=markers.length-interruptionCount;
 const selected=cursor===null?undefined:visiblePoints.find(point=>pointKey(point)===cursor);
 const selectedIndex=selected===undefined?-1:visiblePoints.indexOf(selected);
 const moveCursor=(key:string)=>setCursor(previous=>movePointCursor(visiblePoints,previous,key));
 const handleCursorKey=(event:KeyboardEvent<HTMLElement>)=>{
  if(event.altKey||event.ctrlKey||event.metaKey||event.shiftKey||!cursorKeys.includes(event.key))return;
  event.preventDefault();
  moveCursor(event.key);
 };
 const muted=historical||frozen!==null;
 useEffect(()=>{
  if(!canPlot||!host.current)return;
  const instance=init(host.current,undefined,{renderer:'canvas'});chart.current=instance;
  const resize=new ResizeObserver(()=>instance.resize());resize.observe(host.current);
  return()=>{resize.disconnect();instance.dispose();chart.current=null;};
 },[canPlot]);
 useEffect(()=>{
  const marks=markers.flatMap(point=>point.marker?[{xAxis:point.t,lineStyle:markerStyles[point.marker]}]:[]);
  if(selected)marks.push({xAxis:selected.t,lineStyle:{color:'#324d70',type:'dashed'}});
  chart.current?.setOption({animation:false,textStyle:{fontFamily:'Pretendard, sans-serif'},grid:{left:54,right:22,top:24,bottom:35},tooltip:{trigger:'axis',renderMode:'richText'},xAxis:{type:'time',axisLine:{lineStyle:{color:'#e4e9f1'}},axisLabel:{color:'#5d6d83',fontSize:10,hideOverlap:true},splitLine:{show:false}},yAxis:{type:'value',scale:true,name:unit,nameTextStyle:{fontSize:10,color:'#5d6d83'},axisLabel:{color:'#5d6d83',fontSize:10},splitLine:{lineStyle:{color:'#edf1f7',type:'dashed'}}},series:[{
   ...chartSeries,name:label,lineStyle:{width:2,color:muted?'#71839d':'#3973e6'},itemStyle:{color:muted?'#71839d':'#3973e6'},areaStyle:{color:muted?'#f0f3f8':'#eef4ff'},
   markLine:{silent:true,symbol:'none',label:{show:false},data:marks}
  }]},true);
 },[chartSeries,label,unit,muted,markers,selected]);
 return <div className="signal-investigation">
  <div className="chart-window">
   <label htmlFor={rangeId}>표시 범위</label><select id={rangeId} value={range} onChange={event=>setRange(event.target.value==='all'?'all':event.target.value==='30'?30:60)}><option value="all">보관 중인 전체</option><option value="30">마지막 보관 시각 기준 30초</option><option value="60">마지막 보관 시각 기준 60초</option></select>
   <button className="inspection-toggle" type="button" aria-pressed={frozen!==null} onClick={()=>setFrozen(frozen?null:freezeHistory(points,Date.now()))}>{frozen?'실시간 요약 따라가기':'조사 화면 고정'}</button>
  </div>
  <p className={`inspection-state${frozen?' is-frozen':''}`} role="status">{frozen?`고정된 조사 화면 · ${new Date(frozen.capturedAt).toISOString()} 캡처 · 수집은 계속됩니다.`:historical?'과거 UI 요약 · 현재 상태로 해석하지 마세요.':'수신 UI 요약을 따라가는 중'}<span>{context} · {label} ({unit})</span></p>
  <p className="chart-window-time">{summary.start!==null&&summary.end!==null?`${new Date(summary.start).toISOString()} – ${new Date(summary.end).toISOString()}`:'보관된 요약 없음'} · UTC</p>
  {summary.valid===0&&<div className="empty-chart" role="status"><p>표시 범위에 사용 가능한 표본이 없습니다.</p><small>누락·지연·수신 단절은 0으로 그리지 않습니다. 원본 품질과 보관 이력을 확인하세요.</small></div>}
  <div className="chart-cursor" role="group" aria-label={`${label} 요약 커서`} aria-describedby={cursorHintId}
   aria-keyshortcuts="ArrowLeft ArrowRight Home End" tabIndex={visiblePoints.length?0:undefined} onKeyDown={handleCursorKey}>
   <div ref={host} className="chart" hidden={summary.valid===0} role="img" aria-label={`${label} ${unit}, ${summary.summaries}개 1Hz UI 요약${muted?', 과거 조사 화면':''}. 정확한 값은 아래 UI 요약 표에 있습니다.`}/>
   <p className="chart-cursor-hint" id={cursorHintId}>← / → 관측 이동 · Home / End 처음과 끝</p>
   {(interruptionCount>0||gapCount>0)&&<div className="chart-markers" aria-label="차트 공백 구분">
    {interruptionCount>0&&<span data-marker="transport">연결 중단 {interruptionCount}건</span>}
    {gapCount>0&&<span data-marker="gap">긴 관측 간격 {gapCount}건</span>}
   </div>}
   <div className="chart-cursor-detail">
    <div className="chart-cursor-actions">
     <button type="button" disabled={!visiblePoints.length||selectedIndex===0} onClick={()=>moveCursor('ArrowLeft')} aria-label="이전 관측">이전</button>
     <button type="button" disabled={!visiblePoints.length||selectedIndex===visiblePoints.length-1} onClick={()=>moveCursor('ArrowRight')} aria-label="다음 관측">다음</button>
    </div>
    <div role="status" className="chart-cursor-result">
     {selected?<><strong>{pointState(selected)}{!selected.marker&&selected.quality?` · 수신 품질 ${selected.quality}`:''}</strong>
      {selected.marker==='gap'?<span>두 관측 사이에 표시된 공백</span>:<span><time dateTime={timestamp(selected.detectedAt??selected.t)}>{timestamp(selected.detectedAt??selected.t)}</time> · {selected.marker?'중단 감지 시각':'원본 관측 시각'}</span>}
      {selected.receivedAtUtc&&<span><time dateTime={selected.receivedAtUtc}>{selected.receivedAtUtc}</time> · 수집기 수신 시각</span>}
      {!selected.marker&&<span>{selected.v===null?'표시값 없음':`${selected.v} ${unit}`}{selected.v===null&&selected.receivedValue!=null?` · 받은 값 ${selected.receivedValue} ${unit} (차트에서 제외)`:''}</span>}
     </>:<span>{cursor?'선택한 항목이 표시 범위에 없습니다.':visiblePoints.length?'표시 범위의 관측을 선택하세요.':'보관된 UI 요약 없음'}</span>}
    </div>
   </div>
  </div>
  <dl className="inspection-summary"><div><dt>표시 / 보관 요약</dt><dd>{summary.summaries} / {inspectionPoints.filter(point=>!point.marker).length}</dd></div><div><dt>사용 가능 / 사용 불가</dt><dd>{summary.valid} / {summary.unavailable}</dd></div><div><dt>2.5초 초과 간격</dt><dd>{summary.gaps}</dd></div><div><dt>요약 최솟값 / 최댓값</dt><dd>{formatValue(summary.min)} / {formatValue(summary.max)} <small>{unit}</small></dd></div></dl>
  <details className="inspection-details"><summary>UI 요약 표로 조사 ({summary.summaries}개{markers.length?` · 구간 표식 ${markers.length}개`:''})</summary><p className="chart-note">최근 시각부터 표시합니다. 값 없음과 단절 표시는 0이 아닙니다. 원시 100Hz 신호·DB 보관 이력이 아니며, 요약 사이의 짧은 변화는 복원할 수 없습니다.</p>
   <div className="inspection-table-scroll" tabIndex={0} role="region" aria-label={`${context} ${label} UI 요약 표`}><table className="inspection-table"><caption>{context} · {label} · {frozen?'고정된 조사 화면':'수신 UI 요약'} · UTC</caption><thead><tr><th scope="col">관측·중단 감지 시각 (UTC)</th><th scope="col">값 ({unit})</th><th scope="col">요약 상태</th></tr></thead><tbody>{rows.map(point=>{
    const time=timestamp(point.detectedAt??point.t);
    const key=pointKey(point);
    return <tr key={key} data-selected={cursor===key}><th scope="row">{point.marker==='gap'?'관측 사이':<time dateTime={time}>{time}</time>}</th><td>{point.v===null?'값 없음':formatValue(point.v)}</td><td>{pointState(point)}{point.quality&&` · ${point.quality}`}</td></tr>;
   })}</tbody></table></div>
  </details>
 </div>;
}

export default memo(SignalChart);
