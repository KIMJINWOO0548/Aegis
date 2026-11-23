const sources=['EMULATORS','FIXTURES','UNKNOWN'] as const;
const upstream=['CONNECTED','UNAVAILABLE','CONFIGURATION_REQUIRED','UNKNOWN'] as const;
const workspace=['READY','NOT_CONFIGURED','UNAVAILABLE'] as const;
const connection=['CONNECTING','CONNECTED','DISCONNECTED','REJECTED','STALE','UNKNOWN'] as const;
const engine=['RUNNING','UNAVAILABLE','ARCHIVE_NOT_CONFIGURED','UNKNOWN'] as const;
const freshness=['CURRENT','STALE','RESTORED','NO_RECEIPT','UNAVAILABLE','UNKNOWN'] as const;
const worker=['RUNNING','RESTARTING','FAILED','UNAVAILABLE','NOT_APPLICABLE','UNKNOWN'] as const;

const utcPattern=/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,9})?(?:Z|\+00:00)$/;
function fields(value:unknown):Record<string,unknown>{
 if(typeof value!=='object'||value===null||Array.isArray(value))throw Error();
 return value as Record<string,unknown>;
}
function member<T extends string>(value:unknown,values:readonly T[]):T{
 if(!values.includes(value as T))throw Error();
 return value as T;
}
function utc(value:unknown):string{
 if(typeof value!=='string'||!utcPattern.test(value)||!Number.isFinite(Date.parse(value)))throw Error();
 return value;
}
function count(value:unknown):number{
 if(typeof value!=='number'||!Number.isSafeInteger(value)||value<0)throw Error();
 return value;
}
function optional<T>(value:unknown,parse:(value:unknown)=>T):T|null{
 return value===null?null:parse(value);
}
function flag(value:unknown):boolean{
 if(typeof value!=='boolean')throw Error();
 return value;
}

function publicModule(value:unknown){
 const row=fields(value);
 const values=fields(row.quality);
 const quality={good:count(values.good),missing:count(values.missing),stale:count(values.stale),other:count(values.other)};
 if(quality.good+quality.missing+quality.stale+quality.other>8)throw Error();
 return {
  slot:count(row.slot),connection:member(row.connection,connection),engine:member(row.engine,engine),
  lastReceivedAtUtc:optional(row.lastReceivedAtUtc,utc),lastObservedAtUtc:optional(row.lastObservedAtUtc,utc),
  archiveEnabled:optional(row.archiveEnabled,flag),lastCommittedSequence:optional(row.lastCommittedSequence,count),
  pendingBatches:optional(row.pendingBatches,count),freshness:member(row.freshness,freshness),
  workerState:member(row.workerState,worker),
  quality,
 };
}

/** Whitelist the exact fields exposed in the preview and saved file. */
export function parseLocalDiagnostics(value:unknown){
 const report=fields(value);
 if(report.schemaVersion!=='diagnostics-report-1'||report.modelState!=='NOT_REGISTERED'||
     !Array.isArray(report.modules)||report.modules.length>32)throw Error();
 const modules=report.modules.map(publicModule);
 if(modules.some((row,index)=>row.slot!==index+1))throw Error();
 return {
  schemaVersion:report.schemaVersion,generatedAtUtc:utc(report.generatedAtUtc),
  sourceMode:member(report.sourceMode,sources),upstreamState:member(report.upstreamState,upstream),
  workspaceState:member(report.workspaceState,workspace),modelState:report.modelState,modules,
 };
}

export type LocalDiagnosticsReport=ReturnType<typeof parseLocalDiagnostics>;
export function localDiagnosticsJson(report:LocalDiagnosticsReport):string{
 return JSON.stringify(report,null,2);
}
