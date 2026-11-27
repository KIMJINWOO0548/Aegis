import {expect,it} from 'vitest';
import {localDiagnosticsJson,parseLocalDiagnostics} from './local-diagnostics';

const moduleSnapshot={
 slot:1,connection:'CONNECTED',engine:'RUNNING',lastReceivedAtUtc:'2026-11-24T01:13:00.1234567Z',
 lastObservedAtUtc:null,archiveEnabled:true,lastCommittedSequence:0,pendingBatches:null,freshness:'CURRENT',
 workerState:'RUNNING',quality:{good:1,missing:0,stale:0,other:0},
};
const snapshot={
 schemaVersion:'diagnostics-report-1',generatedAtUtc:'2026-11-24T01:13:00+00:00',sourceMode:'EMULATORS',
 upstreamState:'CONNECTED',workspaceState:'READY',modelState:'NOT_REGISTERED',modules:[moduleSnapshot],
};

it('serializes only allowed diagnostic values for preview and export without replacing zero or null',()=>{
 const parsed=parseLocalDiagnostics({...snapshot,extraPath:'C:/private/demo',modules:[
  {...moduleSnapshot,deviceId:'secret-device-name',nativeSequence:0,sourceRunId:'private-run-id',
   nativeFlow:{state:'READY',sampleCount:32,meanAbsoluteDeviationLpm:0,sourceRunId:'private-run-id',rawFrames:[1,2]},
   quality:{...moduleSnapshot.quality,raw:117}},
 ]});
 const preview=localDiagnosticsJson(parsed);
 expect(JSON.parse(preview)).toEqual(snapshot);
 expect(preview).not.toMatch(/extraPath|private|secret-device-name|raw|nativeFlow|nativeSequence|sourceRunId/);
});

it('rejects unrecognized metadata rather than exporting it',()=>{
 expect(()=>parseLocalDiagnostics({...snapshot,modules:[{...moduleSnapshot,connection:'unexpected-label'}]})).toThrow();
 expect(()=>parseLocalDiagnostics({...snapshot,generatedAtUtc:'2026-11-24T01:13:00+04:00'})).toThrow();
 expect(()=>parseLocalDiagnostics({...snapshot,modules:[moduleSnapshot,moduleSnapshot]})).toThrow();
 expect(()=>parseLocalDiagnostics({...snapshot,modules:[{...moduleSnapshot,quality:{...moduleSnapshot.quality,missing:8}}]})).toThrow();
});
