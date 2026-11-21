import {useState, type KeyboardEvent, type ReactNode, type RefObject} from 'react';
import {Box, Check, ChevronRight} from 'lucide-react';
import {Badge} from '@/components/ui/badge';
import {Button} from '@/components/ui/button';
import {Card, CardContent, CardHeader, CardTitle} from '@/components/ui/card';
import {collectionEvidence, formatValue, isCurrent, signalLabels, tagState, time, type Device, type Snapshot} from './data';
import './signal-workspace.css';

const moduleLabels = {CURRENT:'실시간 수신', STALE:'수신 지연', RESTORED:'복원 이력', NO_RECEIPT:'수신 대기', UNAVAILABLE:'확인 불가'};
const tagLabels = {CURRENT:'실시간', HISTORY:'과거', RESTORED:'복원 이력', MISSING:'값 없음', ABSENT:'미수신', BAD:'품질 불량', FIXTURE:'고정 자료'};

function moveFocus(event: KeyboardEvent<HTMLElement>) {
  if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
  const buttons = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('button[data-pick]'));
  const index = buttons.indexOf(event.target as HTMLButtonElement);
  if (index === -1) return;
  const positions = {Home: 0, End: buttons.length-1, ArrowDown: Math.min(index+1, buttons.length-1), ArrowUp: Math.max(index-1, 0)};
  event.preventDefault();
  buttons[positions[event.key as keyof typeof positions]].focus();
}

export default function SignalWorkspace({devices,device,snapshot,now,available,signal,selectDevice,selectSignal,openHistory,historyButtonRef,chart}:{
  devices:Device[];device:Device|undefined;snapshot:Snapshot|null;now:number;available:boolean;signal:string;
  selectDevice:(id:string)=>void;selectSignal:(key:string)=>void;openHistory:()=>void;historyButtonRef:RefObject<HTMLButtonElement|null>;chart:ReactNode;
}) {
  const [filter,setFilter] = useState('all');
  const tags = Object.entries(signalLabels).map(([key,name])=>{
    const observation = device?.observations.find(item=>item.signalKey===key);
    return {key,name,observation,state:tagState(snapshot,device,observation,now,available)};
  }).filter(({state})=>filter==='all'||
    (filter==='current'&&state==='CURRENT')||
    (filter==='history'&&['HISTORY','RESTORED','FIXTURE'].includes(state))||
    (filter==='missing'&&['ABSENT','MISSING','BAD'].includes(state)));
  const firstTag = tags.some(tag=>tag.key===signal)?signal:tags[0]?.key;
  return <section className="telemetry-layout" aria-label="장비 · 태그 · 신호 추이">
    <Card className="panel module-picker" aria-labelledby="module-picker-title">
      <CardHeader className="panel-heading"><CardTitle><h2 id="module-picker-title">장비 · 모듈</h2></CardTitle>
        {snapshot&&<small>{devices.length}개</small>}
      </CardHeader>
      {devices.length?<ul className="module-choices" aria-label="수집 모듈 목록" onKeyDown={moveFocus}>
        {devices.map(row=>{
          const selected = row.deviceId===device?.deviceId;
          const status = collectionEvidence(snapshot,row,now,available);
          const label = status?moduleLabels[status.freshness]:isCurrent(snapshot,row,now,available)?moduleLabels.CURRENT:'확인 불가';
          return <li key={row.deviceId}><button type="button" data-pick="" tabIndex={selected?0:-1} aria-pressed={selected}
            aria-label={`${row.moduleId ?? row.deviceId} · ${row.deviceId} · ${label}`}
            aria-controls="tag-observations signal-chart-panel" className="module-choice" onClick={()=>selectDevice(row.deviceId)}>
            <Box size={17} aria-hidden="true"/>
            <span><strong>{row.moduleId??row.deviceId}</strong><small>{row.equipmentId??row.deviceId}</small><small>{label} · {row.deviceId}</small></span>
            {selected&&<Check size={15} aria-hidden="true"/>}
          </button></li>;
        })}
      </ul>:<CardContent className="panel-body"><p className="quiet-state">{snapshot?'조건에 맞는 모듈이 없습니다.':'모듈 수신 대기'}</p></CardContent>}
      {device&&<CardContent className="panel-body module-history"><Button size="sm" variant="outline" ref={historyButtonRef} onClick={openHistory}>
        보관 이력 <ChevronRight size={15} aria-hidden="true"/>
      </Button></CardContent>}
    </Card>
    <div className="telemetry-detail">
      <Card id="tag-observations" className="panel tag-panel" aria-labelledby="tag-table-title">
        <CardHeader className="panel-heading">
          <CardTitle><h2 id="tag-table-title">태그 관측값</h2>{device&&<p className="tag-scope">{device.deviceId}</p>}</CardTitle>
          <select value={filter} onChange={event=>setFilter(event.target.value)} aria-label="태그 상태 필터">
            <option value="all">전체 상태</option><option value="current">실시간</option>
            <option value="history">과거 · 복원</option><option value="missing">누락 · 품질 불량</option>
          </select>
        </CardHeader>
        <div className="table-scroll tag-table-scroll" tabIndex={0} role="region" aria-labelledby="tag-table-title">
          <table className="signal-table tag-table" aria-labelledby="tag-table-title">
            <thead><tr><th scope="col">태그</th><th scope="col">값</th><th scope="col">상태</th><th scope="col">관측 시각</th></tr></thead>
            <tbody onKeyDown={moveFocus}>
              {device&&tags.map(({key,name,observation,state})=><tr key={key} data-selected={key===signal} data-state={state}>
                <th scope="row"><button type="button" data-pick="" tabIndex={key===firstTag?0:-1} aria-pressed={key===signal}
                  aria-controls="signal-chart-panel" className="signal-name tag-choice" aria-label={`${name} 차트 선택`} onClick={()=>selectSignal(key)}>
                  <span>{name}</span><small>{key}</small>
                </button></th>
                <td className="tag-value">{formatValue(observation?.value)}{observation?.unit&&<small>{observation.unit}</small>}</td>
                <td><Badge variant="secondary" className={state==='CURRENT'?'tag-current':'tag-state'}>{tagLabels[state]}</Badge><small className="quality-label">{observation?.quality??'ABSENT'}</small></td>
                <td className="tag-time">{observation?<time title={observation.observedAtUtc} dateTime={observation.observedAtUtc}>{time(observation.observedAtUtc)}</time>:'—'}</td>
              </tr>)}
              {(!device||!tags.length)&&<tr><td colSpan={4} className="empty-table">{device?'이 상태에 맞는 태그가 없습니다.':'모듈을 선택하면 태그가 표시됩니다.'}</td></tr>}
            </tbody>
          </table>
        </div>
      </Card>
      <div id="signal-chart-panel">{chart}</div>
    </div>
  </section>;
}
