import {expect,it} from 'vitest';
import {archiveRecordsPath,parseArchiveCatalog} from './archive-sessions';

it('keeps the current and prior-session routes separate',()=>{
 expect(archiveRecordsPath('')).toBe('/api/stored-records');
 expect(archiveRecordsPath('run-abcd12_3')).toBe('/api/archive-sessions/run-abcd12_3/records');
 expect(()=>archiveRecordsPath('../runs')).toThrow();
 expect(()=>archiveRecordsPath('run-abcd1234%2Freview')).toThrow();
 expect(()=>archiveRecordsPath('run-abcd1234\n')).toThrow();
});

it('keeps an empty archive catalog and a truncated catalog distinct',()=>{
 expect(parseArchiveCatalog({schemaVersion:'archive-sessions-1',hasMore:false,sessions:[]}))
  .toEqual({sessions:[],hasMore:false});
 const session={sessionId:'run-abcd12_3',lastModifiedAtUtc:'2026-11-21T08:03:12Z',storeIds:['EQ-SUPPLY-01']};
 expect(parseArchiveCatalog({schemaVersion:'archive-sessions-1',hasMore:true,sessions:[session]}))
  .toEqual({sessions:[session],hasMore:true});
 expect(()=>parseArchiveCatalog({schemaVersion:'archive-sessions-9',hasMore:false,sessions:[]})).toThrow();
 expect(()=>parseArchiveCatalog({schemaVersion:'archive-sessions-1',hasMore:false,sessions:[{...session,sessionId:'/old'}]})).toThrow();
});
