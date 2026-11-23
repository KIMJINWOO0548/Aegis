import {describe,it,expect} from 'vitest';
import {appendHistory,interruptHistory,parseSnapshot,type Snapshot,type Point} from './data';
import {freezeHistory,inspectHistory,movePointCursor,pointKey,pointState,projectHistory,signalSeries} from './investigation';

const origin=Date.parse('2026-11-06T00:00:00Z');
function received(second:number,value:number|null,quality='GOOD',observedSecond=second):Snapshot {
 return parseSnapshot({schemaVersion:'dashboard-1',generatedAtUtc:new Date(origin+second*1000).toISOString(),upstreamState:'CONNECTED',sourceMode:'FIXTURES',message:null,inference:{state:'NOT_REGISTERED',message:'Synthetic investigation test'},devices:[{deviceId:'synthetic-1',equipmentId:null,moduleId:null,connection:{state:'CONNECTED'},ingestion:null,observations:[{observationId:`sample-${observedSecond}`,signalKey:'actualFlowLpm',value,unit:'L/min',quality,observedAtUtc:new Date(origin+observedSecond*1000).toISOString()}],diagnostics:null,warnings:[],review:null,engineState:'ARCHIVE_NOT_CONFIGURED',nativeSequence:null}]});
}
describe('bounded signal investigation',()=>{
 it('keeps an already received brief pulse, marks a dropped interval and rejects delayed replacement',()=>{
  let history=appendHistory({},received(0,10));
  history=appendHistory(history,received(1,80));
  history=appendHistory(history,received(2,10));
  history=appendHistory(history,received(6,11));
  history=appendHistory(history,received(7,999,'GOOD',1));
  const points=history['synthetic-1'].actualFlowLpm;
  expect(points.map(p=>p.v)).toEqual([10,80,10,null,11]);
  expect(inspectHistory(points)).toEqual({summaries:4,valid:4,unavailable:0,gaps:1,min:10,max:80,start:origin,end:origin+6000});
  expect(points.map(p=>p.id)).not.toContain('sample-7');
 });
 it('shows an interruption and stale or bad values as unavailable, never as zero',()=>{
  let history=appendHistory({},received(0,0));
  history=interruptHistory(history,origin+500);
  history=appendHistory(history,received(1,900,'BAD'));
  history=appendHistory(history,received(5,900,'GOOD',2));
  const points=history['synthetic-1'].actualFlowLpm;
  expect(points.map(p=>p.v)).toEqual([0,null,null,null]);
  expect(inspectHistory(points)).toMatchObject({valid:1,unavailable:2,min:0,max:0});
  expect(pointState(points[1])).toBe('수신 중단');
  expect(pointState(points[2])).toBe('값 사용 불가');
   expect(points[0]).toMatchObject({v:0,quality:'GOOD',receivedValue:0});
   expect(points[2]).toMatchObject({v:null,quality:'BAD',receivedValue:900});
 });
 it('marks a disconnect after an unusable value and accepts the first resumed observation with an earlier source clock',()=>{
  const before=appendHistory({},received(0,null,'MISSING'));
  const disconnected=interruptHistory(before,origin+1200);
  const repeated=interruptHistory(disconnected,origin+1300);
  expect(repeated).toEqual(disconnected);
  const resumed=appendHistory(repeated,received(2,0,'GOOD',1))['synthetic-1'].actualFlowLpm;
  expect(resumed.map(pointState)).toEqual(['값 사용 불가','수신 중단','사용 가능']);
  expect(resumed.map(point=>point.t)).toEqual([origin,origin+999,origin+1000]);
  expect(resumed[1].detectedAt).toBe(origin+1200);
  expect(resumed[2].v).toBe(0);
  expect(projectHistory(resumed)).toEqual([[origin,null],[origin+999,null],[origin+1000,0]]);
 });
 it('marks an unavailable upstream without altering the retained source observation or its receipt time',()=>{
  const initial=received(0,0);
  initial.devices[0].observations[0].receivedAtUtc=new Date(origin+100).toISOString();
  const history=appendHistory({},parseSnapshot(initial));
  const unavailable={...initial,upstreamState:'UNAVAILABLE' as const,generatedAtUtc:new Date(origin+1500).toISOString()};
  const disconnected=appendHistory(history,unavailable);
  const repeated=appendHistory(disconnected,{...unavailable,generatedAtUtc:new Date(origin+2500).toISOString()});
  expect(repeated['synthetic-1'].actualFlowLpm).toMatchObject([
   {t:origin,v:0,quality:'GOOD',receivedAtUtc:initial.devices[0].observations[0].receivedAtUtc},
   {v:null,marker:'transport',detectedAt:origin+1500},
  ]);
  initial.devices[0].observations[0].receivedAtUtc='invalid';
  expect(()=>parseSnapshot(initial)).toThrow('신호 데이터 형식을 확인할 수 없습니다.');
 });
 it('moves the cursor among original observations and explicit markers without confusing their identifiers',()=>{
  const first:Point={t:origin,v:0,id:'matching',quality:'GOOD'};
  const marker:Point={t:origin+500,v:null,id:'matching',marker:'transport'};
  const last:Point={t:origin+1000,v:10,id:'later',quality:'GOOD'};
  const points=[first,marker,last];
  expect(movePointCursor(points,null,'ArrowLeft')).toBe(pointKey(last));
  expect(movePointCursor(points,null,'ArrowRight')).toBe(pointKey(first));
  expect(movePointCursor(points,pointKey(marker),'ArrowLeft')).toBe(pointKey(first));
  expect(movePointCursor(points,pointKey(first),'ArrowRight')).toBe(pointKey(marker));
  expect(movePointCursor(points,pointKey(last),'Home')).toBe(pointKey(first));
  expect(movePointCursor(points,pointKey(first),'End')).toBe(pointKey(last));
  expect(movePointCursor(points,pointKey(last),'ArrowRight')).toBe(pointKey(last));
  expect(movePointCursor([],pointKey(last),'Home')).toBeNull();
 });
 it('captures independent immutable point copies while collection and the chosen range can continue',()=>{
  const points:Point[]=[{t:origin,v:3,id:'first'},{t:origin+1000,v:null,id:'gap:next',marker:'gap'}];
  const frozen=freezeHistory(points,origin+2000);
  points[0].v=999;points.push({t:origin+3000,v:8,id:'new'});
  expect(frozen.points).toEqual([{t:origin,v:3,id:'first'},{t:origin+1000,v:null,id:'gap:next',marker:'gap'}]);
  expect(frozen.capturedAt).toBe(origin+2000);
  expect(Object.isFrozen(frozen.points)).toBe(true);
  expect(Object.isFrozen(frozen.points[0])).toBe(true);
  expect(pointState(frozen.points[1])).toBe('수신 간격');
 });
 it('does not infer null causes from arbitrary source observation IDs',()=>{
  for(const id of ['transport:source','gap:source']){
   const snapshot=received(0,99,'BAD');snapshot.devices[0].observations[0].observationId=id;
   const point=appendHistory({},snapshot)['synthetic-1'].actualFlowLpm[0];
   expect(pointState(point)).toBe('값 사용 불가');
  }
 });
 it('projects explicit gaps without joining values or manufacturing measured points',()=>{
  const points:Point[]=[{t:0,v:1,id:'a'},{t:4000,v:9,id:'b'},{t:4999,v:null,id:'transport:4999'},{t:5000,v:2,id:'c'}];
  const before=structuredClone(points);
  expect(projectHistory(points)).toEqual([[0,1],[3999,null],[4000,9],[4999,null],[5000,2]]);
  expect(points).toEqual(before);
  expect(inspectHistory([])).toEqual({summaries:0,valid:0,unavailable:0,gaps:0,min:null,max:null,start:null,end:null});
 });
 it('keeps the actual chart last valid point visible when it is isolated after a gap',()=>{
  const series=signalSeries([{t:0,v:10,id:'a'},{t:1000,v:80,id:'b'},{t:2000,v:10,id:'c'},{t:5999,v:null,id:'gap:d',marker:'gap'},{t:6000,v:11,id:'d'}]);
  expect(series.connectNulls).toBe(false);
  expect(series.showSymbol).toBe(true);
  expect(series.showAllSymbol).toBe(true);
  expect(series.symbolSize).toBe(4);
  expect(series.data.at(-1)).toEqual([6000,11]);
  expect(series.data.at(-2)).toEqual([5999,null]);
 });
});
