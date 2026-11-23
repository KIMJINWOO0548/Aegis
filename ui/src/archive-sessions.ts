export type ArchiveSession = {sessionId:string;lastModifiedAtUtc:string;storeIds:string[]};
export type ArchiveCatalog = {sessions:ArchiveSession[];hasMore:boolean};

const sessionIdPattern=/^run-[a-z0-9_]{8}$/;

export function isArchiveSessionId(value:unknown):value is string {
 return typeof value==='string'&&value.length===12&&sessionIdPattern.test(value);
}

export function parseArchiveCatalog(value:unknown):ArchiveCatalog {
 if(typeof value!=='object'||value===null)throw Error();
 const catalog=value as Record<string,unknown>;
 if(catalog.schemaVersion!=='archive-sessions-1'||!Array.isArray(catalog.sessions)||typeof catalog.hasMore!=='boolean')throw Error();
 const sessions:ArchiveSession[]=catalog.sessions.map((session:unknown)=>{
  if(typeof session!=='object'||session===null)throw Error();
  const entry=session as Record<string,unknown>;
  if(!isArchiveSessionId(entry.sessionId)||typeof entry.lastModifiedAtUtc!=='string'||!Number.isFinite(Date.parse(entry.lastModifiedAtUtc))||
      !Array.isArray(entry.storeIds)||!entry.storeIds.every(storeId=>typeof storeId==='string'))throw Error();
  return {sessionId:entry.sessionId,lastModifiedAtUtc:entry.lastModifiedAtUtc,storeIds:entry.storeIds};
 });
 return {sessions,hasMore:catalog.hasMore};
}

export function archiveRecordsPath(sessionId:string):string {
 if(!sessionId)return '/api/stored-records';
 if(!isArchiveSessionId(sessionId))throw Error();
 return '/api/archive-sessions/'+sessionId+'/records';
}
