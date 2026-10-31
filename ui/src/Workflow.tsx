import {useEffect,useRef,useState} from 'react';
import {ReactFlow,Background,Position,type Node,type Edge} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
const edges:Edge[]=[{id:'a-b',source:'a',target:'b',style:{stroke:'#a8bbd9'}},{id:'b-c',source:'b',target:'c',style:{stroke:'#a8bbd9'}}];
export default function Workflow({archive,engine}:{archive:boolean;engine:string}){
 const host=useRef<HTMLDivElement>(null);const [vertical,setVertical]=useState(true);
 useEffect(()=>{if(!host.current)return;const resize=new ResizeObserver(([entry])=>setVertical(entry.contentRect.width<560));resize.observe(host.current);return()=>resize.disconnect();},[]);
 const labels=['01  독립 모듈 수집',`02  SQLite 원시 보관${archive?'':' · 미설정'}`,`03  C# 근거 검토${engine==='RUNNING'?'':' · 대기'}`];
 const nodes:Node[]=labels.map((label,index)=>({id:['a','b','c'][index],position:vertical?{x:0,y:index*75}:{x:index*235,y:25},data:{label},sourcePosition:vertical?Position.Bottom:Position.Right,targetPosition:vertical?Position.Top:Position.Left,style:{width:180,padding:17,borderRadius:7}}));
 return <div ref={host} className="workflow" style={{height:vertical?260:173}} aria-label="수집, 원시 보관, C# 검토 읽기 전용 흐름"><ReactFlow key={vertical?'vertical':'horizontal'} nodes={nodes} edges={edges} minZoom={.1} fitView fitViewOptions={{padding:.15}} nodesDraggable={false} nodesConnectable={false} elementsSelectable={false} panOnDrag={false} zoomOnScroll={false} zoomOnPinch={false} zoomOnDoubleClick={false} preventScrolling={false} proOptions={{hideAttribution:false}}><Background color="#dce5f2" gap={19}/></ReactFlow></div>;
}
