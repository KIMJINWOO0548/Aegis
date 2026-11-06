import type {Point} from './data';

/** Only retained UI summaries: never an archive query or raw-signal statistic. */
export function inspectHistory(points:readonly Point[]){
 let valid=0,gaps=0,min:number|null=null,max:number|null=null;
 for(let i=0;i<points.length;i++){
  const point=points[i];
  if(i&&point.t-points[i-1].t>2500)gaps++;
  if(point.v!==null&&Number.isFinite(point.v)){
   valid++;min=min===null?point.v:Math.min(min,point.v);max=max===null?point.v:Math.max(max,point.v);
  }
 }
 return {summaries:points.length,valid,unavailable:points.length-valid,gaps,min,max,start:points[0]?.t??null,end:points.at(-1)?.t??null};
}

export interface FrozenHistory {readonly capturedAt:number;readonly points:readonly Point[]}
export function freezeHistory(points:readonly Point[],capturedAt:number):FrozenHistory {
 return Object.freeze({capturedAt,points:Object.freeze(points.map(point=>Object.freeze({...point})))});
}

export function pointState(point:Point):string {
 if(point.v!==null)return '사용 가능';
 if(point.marker==='transport')return '수신 중단';
 if(point.marker==='gap')return '수신 간격';
 return '값 사용 불가';
}

export function projectHistory(points:readonly Point[]):[number,number|null][] {
 return points.flatMap((point,index)=>index&&point.v!==null&&points[index-1].v!==null&&point.t-points[index-1].t>2500
  ?[[point.t-1,null],[point.t,point.v]] as [number,number|null][]
  :[[point.t,point.v]] as [number,number|null][]);
}

/** Shared actual chart policy, also exercised by the isolated-point regression. */
export function signalSeries(points:readonly Point[]){
 return {type:'line' as const,connectNulls:false,showSymbol:true,showAllSymbol:true,symbol:'circle',symbolSize:4,data:projectHistory(points)};
}
