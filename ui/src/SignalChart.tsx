import {useEffect,useRef} from 'react';
import {init,use,type ECharts} from 'echarts/core';
import {LineChart} from 'echarts/charts';
import {GridComponent,TooltipComponent,LegendComponent} from 'echarts/components';
import {CanvasRenderer} from 'echarts/renderers';
import type {Point} from './data';
use([LineChart,GridComponent,TooltipComponent,LegendComponent,CanvasRenderer]);
export default function SignalChart({points,label,unit,historical}:{points:Point[];label:string;unit:string;historical:boolean}){const host=useRef<HTMLDivElement>(null);const chart=useRef<ECharts|null>(null);
useEffect(()=>{if(!host.current)return;const c=init(host.current,undefined,{renderer:'canvas'});chart.current=c;const resize=new ResizeObserver(()=>c.resize());resize.observe(host.current);return()=>{resize.disconnect();c.dispose();chart.current=null;};},[]);
useEffect(()=>{chart.current?.setOption({animation:false,textStyle:{fontFamily:'Pretendard, sans-serif'},grid:{left:54,right:22,top:24,bottom:35},tooltip:{trigger:'axis',renderMode:'richText'},xAxis:{type:'time',axisLine:{lineStyle:{color:'#e4e9f1'}},axisLabel:{color:'#8a98ac',fontSize:10,hideOverlap:true},splitLine:{show:false}},yAxis:{type:'value',scale:true,name:unit,nameTextStyle:{fontSize:10,color:'#8a98ac'},axisLabel:{color:'#8a98ac',fontSize:10},splitLine:{lineStyle:{color:'#edf1f7',type:'dashed'}}},series:[{name:label,type:'line',connectNulls:false,showSymbol:points.length<3,symbolSize:5,lineStyle:{width:2,color:historical?'#9aa8bc':'#3973e6'},itemStyle:{color:historical?'#9aa8bc':'#3973e6'},areaStyle:{color:historical?'#f0f3f8':'#eef4ff'},data:points.flatMap((p,i)=>i&&p.t-points[i-1].t>2500?[[p.t-1,null],[p.t,p.v]]:[[p.t,p.v]])}]},true);},[points,label,unit,historical]);
return <div ref={host} className="chart" role="img" aria-label={`${label} ${unit}, ${points.length}개 1Hz UI 요약${historical?', 과거 이력':''}`}/>;}
