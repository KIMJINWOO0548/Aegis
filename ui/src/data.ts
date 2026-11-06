export interface Observation { observationId:string; signalKey:string; value:number|null; unit:string; observedAtUtc:string; quality:string }
export interface ReviewEvent {eventId:number; disposition:'UNRESOLVED'; startAtUtc:string; lastAtUtc:string; peakDeviationLpm:number; sampleCount:number; endReason:string|null; relatedSignalKeys:string[]}
export interface Review {algorithmVersion:string; state:string; observedAtUtc:string|null; rawErrorLpm:number|null; trendErrorLpm:number|null; contextReason:string|null; eventCount:number; omittedEventCount:number; events:ReviewEvent[]}
export interface Device {deviceId:string;equipmentId:string|null;moduleId:string|null;connection:{state:string;lastReceivedAtUtc?:string;acceptedBatches?:number;rejectedBatches?:number;reasonCode?:string;pollLatencyMs?:number}|null;ingestion:{archiveEnabled:boolean;lastCommittedSequence:number|null;lastObservedAtUtc:string|null;pendingBatches:number|null;observedLagMs:number|null;acceptedSignalValues?:number}|null;observations:Observation[];diagnostics:{evaluation:string;enabled:boolean;issues:{signalKey:string;reasonCode:string;message:string}[]}|null;warnings:{id:string;state:string;createdAtUtc:string}[];review:Review|null;engineState:string;nativeSequence:number|null}
export interface Snapshot {schemaVersion:'dashboard-1';generatedAtUtc:string;upstreamState:'CONNECTED'|'UNAVAILABLE'|'CONFIGURATION_REQUIRED';sourceMode:'EMULATORS'|'FIXTURES'|'UNKNOWN';message:string|null;devices:Device[];inference:{state:'NOT_REGISTERED';message:string}}
const record=(v:unknown):v is Record<string,unknown>=>typeof v==='object'&&v!==null&&!Array.isArray(v);
const text=(v:unknown):v is string=>typeof v==='string'&&v.length<=512;
const identity=(v:unknown)=>typeof v==='string'&&/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(v);
const timestamp=(v:unknown):v is string=>typeof v==='string'&&/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)$/.test(v)&&Number.isFinite(Date.parse(v));
const nullable=(v:unknown,test:(v:unknown)=>boolean)=>v===null||test(v);
const number=(v:unknown)=>typeof v==='number'&&Number.isFinite(v);
const count=(v:unknown)=>number(v)&&Number.isSafeInteger(v)&&Number(v)>=0;
const quality=(v:unknown)=>['GOOD','BAD','MISSING','STALE'].includes(String(v));
const units:Record<string,string>={setpointFlowLpm:'L/min',actualFlowLpm:'L/min',supplyPressureKpa:'kPa',valveCommandPct:'%',valveFeedbackPct:'%',motorCurrentAmp:'A',temperatureC:'degC',cycleCount:'count'};
export function parseSnapshot(value:unknown):Snapshot {
  if(!record(value)||value.schemaVersion!=='dashboard-1'||!timestamp(value.generatedAtUtc)||!nullable(value.message,text)||!['CONNECTED','UNAVAILABLE','CONFIGURATION_REQUIRED'].includes(String(value.upstreamState))||!['EMULATORS','FIXTURES','UNKNOWN'].includes(String(value.sourceMode))||!Array.isArray(value.devices)||value.devices.length>32||!record(value.inference)||value.inference.state!=='NOT_REGISTERED'||!text(value.inference.message)) throw Error('지원하지 않는 대시보드 응답입니다.');
  const ids=new Set<string>();
  for(const d of value.devices){
    if(!record(d)||typeof d.deviceId!=='string'||!identity(d.deviceId)||ids.has(d.deviceId)||!nullable(d.equipmentId,identity)||!nullable(d.moduleId,identity)||!['RUNNING','UNAVAILABLE','ARCHIVE_NOT_CONFIGURED'].includes(String(d.engineState))||!nullable(d.nativeSequence,count)||!Array.isArray(d.observations)||d.observations.length>8||!Array.isArray(d.warnings)||d.warnings.length>32) throw Error('장비 데이터 형식을 확인할 수 없습니다.');
    ids.add(d.deviceId);
    const signals=new Set<string>();
    for(const o of d.observations) {if(!record(o)||!text(o.observationId)||!o.observationId||typeof o.signalKey!=='string'||!Object.hasOwn(units,o.signalKey)||signals.has(o.signalKey)||o.unit!==units[o.signalKey]||!quality(o.quality)||!timestamp(o.observedAtUtc)||!nullable(o.value,number)||(o.quality==='GOOD'&&o.value===null)) throw Error('신호 데이터 형식을 확인할 수 없습니다.');signals.add(o.signalKey);}
    if(d.connection!==null&&(!record(d.connection)||!['CONNECTING','CONNECTED','DISCONNECTED','REJECTED','STALE'].includes(String(d.connection.state))||('lastReceivedAtUtc' in d.connection&&!nullable(d.connection.lastReceivedAtUtc,timestamp))))throw Error('연결 상태 형식 오류');
    if(d.ingestion!==null&&(!record(d.ingestion)||typeof d.ingestion.archiveEnabled!=='boolean'||!nullable(d.ingestion.lastCommittedSequence,count)||!nullable(d.ingestion.lastObservedAtUtc,timestamp)||!nullable(d.ingestion.pendingBatches,count)||!nullable(d.ingestion.observedLagMs,v=>number(v)&&Number(v)>=0)))throw Error('보관 상태 형식 오류');
    if(d.diagnostics!==null&&(!record(d.diagnostics)||typeof d.diagnostics.enabled!=='boolean'||!['INSUFFICIENT_DATA','NORMAL','ANOMALOUS','UNAVAILABLE'].includes(String(d.diagnostics.evaluation))||!Array.isArray(d.diagnostics.issues)||d.diagnostics.issues.length>8||d.diagnostics.issues.some(i=>!record(i)||!text(i.signalKey)||!Object.hasOwn(units,i.signalKey)||!text(i.reasonCode)||!text(i.message))))throw Error('진단 상태 형식 오류');
    if(d.warnings.some(w=>!record(w)||!text(w.id)||!text(w.state)||!timestamp(w.createdAtUtc)))throw Error('경고 형식 오류');
    if(d.review!==null&&(!record(d.review)||d.review.algorithmVersion!=='flow-review-dotnet-dev-1'||!text(d.review.state)||!nullable(d.review.observedAtUtc,timestamp)||!nullable(d.review.rawErrorLpm,number)||!nullable(d.review.trendErrorLpm,number)||!nullable(d.review.contextReason,text)||!count(d.review.eventCount)||!count(d.review.omittedEventCount)||!Array.isArray(d.review.events)||d.review.events.length>32||d.review.events.some(e=>!record(e)||e.disposition!=='UNRESOLVED'||!count(e.eventId)||!timestamp(e.startAtUtc)||!timestamp(e.lastAtUtc)||!number(e.peakDeviationLpm)||!count(e.sampleCount)||!nullable(e.endReason,text)||!Array.isArray(e.relatedSignalKeys)||e.relatedSignalKeys.length>8||e.relatedSignalKeys.some(k=>typeof k!=='string'||!Object.hasOwn(units,k))))) throw Error('검토 근거 형식을 확인할 수 없습니다.');
  }
  return value as unknown as Snapshot;
}
export const signalLabels:Record<string,string>={setpointFlowLpm:'설정 유량',actualFlowLpm:'실측 유량',supplyPressureKpa:'공급 압력',valveCommandPct:'밸브 명령',valveFeedbackPct:'밸브 피드백',motorCurrentAmp:'모터 전류',temperatureC:'온도',cycleCount:'사이클 수'};
export const fresh=(stamp:string,now:number)=>now-Date.parse(stamp)<3000&&now-Date.parse(stamp)>=-1000;
export function isCurrent(snapshot:Snapshot|null,device:Device|undefined,now=Date.now(),transport=true):boolean {return transport&&!!snapshot&&!!device&&snapshot.upstreamState==='CONNECTED'&&device.connection?.state==='CONNECTED'&&fresh(snapshot.generatedAtUtc,now);}
export function observationCurrent(snapshot:Snapshot|null,device:Device|undefined,o:Observation,now=Date.now(),transport=true):boolean{return isCurrent(snapshot,device,now,transport)&&o.quality==='GOOD'&&o.value!==null&&fresh(o.observedAtUtc,now);}
export interface Point {t:number;v:number|null;id:string}
export type HistoryRange='all'|30|60;
export function historyWindow(points:Point[],range:HistoryRange):Point[]{
  if(range==='all'||!points.length)return points;
  const start=points[points.length-1].t-range*1000;
  return points.filter(point=>point.t>=start);
}
export type History=Record<string,Record<string,Point[]>>;
export function interruptHistory(history:History,now=Date.now()):History {return Object.fromEntries(Object.entries(history).map(([device,signals])=>[device,Object.fromEntries(Object.entries(signals).map(([signal,points])=>[signal,points.length&&points.at(-1)?.v!==null?[...points,{t:Math.max(now,points.at(-1)!.t+1),v:null,id:`transport:${now}`}].slice(-120):points]))]));}
export function appendHistory(history:History,snapshot:Snapshot):History {
  const next:History={};
  for(const d of snapshot.devices){
    const series={...(history[d.deviceId]??{})};
    for(const o of d.observations){const points=series[o.signalKey]??[];const t=Date.parse(o.observedAtUtc);if(points.some(p=>p.id===o.observationId)||points.length&&t<=points[points.length-1].t)continue;const gap=points.length&&t-points[points.length-1].t>2500?[{t:t-1,v:null,id:`gap:${o.observationId}`}]:[];series[o.signalKey]=[...points,...gap,{t,v:observationCurrent(snapshot,d,o,Date.parse(snapshot.generatedAtUtc))?o.value:null,id:o.observationId}].slice(-120);}
    next[d.deviceId]=series;
  }
  return next;
}
export function selectDevice(devices:Device[],id:string,filter:string,query:string):Device|undefined {const rows=filterDevices(devices,filter,query);return rows.find(d=>d.deviceId===id)??rows[0];}
export function filterDevices(devices:Device[],filter:string,query:string):Device[]{const q=query.toLocaleLowerCase();return devices.filter(d=>(filter==='all'||(filter==='archive'?d.ingestion?.archiveEnabled:d.connection?.state===filter))&&`${d.deviceId} ${d.equipmentId??''} ${d.moduleId??''} ${d.observations.map(o=>`${o.signalKey} ${signalLabels[o.signalKey]??''}`).join(' ')}`.toLocaleLowerCase().includes(q));}
export const formatValue=(value:number|null|undefined)=>value==null?'—':value.toLocaleString('ko-KR',{maximumFractionDigits:3});
export const time=(value:string|undefined|null)=>value&&Number.isFinite(Date.parse(value))?new Date(value).toLocaleTimeString('ko-KR',{hour12:false}):'수신 전';
