import {useState, type KeyboardEvent, type ReactNode, type RefObject} from 'react';
import {Box, Check, ChevronRight, Search} from 'lucide-react';
import {Badge} from '@/components/ui/badge';
import {Button} from '@/components/ui/button';
import {collectionEvidence, formatValue, isCurrent, signalLabels, tagState, type Device, type Snapshot} from './data';
import './signal-workspace.css';

const moduleLabels = {CURRENT:'실시간 수신', STALE:'수신 지연', RESTORED:'복원 이력', NO_RECEIPT:'수신 대기', UNAVAILABLE:'확인 불가'};
const tagLabels = {CURRENT:'실시간', HISTORY:'과거', RESTORED:'복원 이력', MISSING:'값 없음', ABSENT:'미수신', BAD:'품질 불량', FIXTURE:'고정 자료'};
const observationClock = new Intl.DateTimeFormat('en-GB',{hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'});

function moveFocus(event: KeyboardEvent<HTMLElement>) {
  if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
  const buttons = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('button[data-pick]'));
  const index = buttons.indexOf(event.target as HTMLButtonElement);
  if (index === -1) return;
  const positions = {Home: 0, End: buttons.length-1, ArrowDown: Math.min(index+1, buttons.length-1), ArrowUp: Math.max(index-1, 0)};
  event.preventDefault();
  buttons[positions[event.key as keyof typeof positions]].focus();
}

export default function SignalWorkspace({devices,device,snapshot,now,available,signal,listsCollapsed,selectDevice,selectSignal,openHistory,historyButtonRef,chart}:{
  devices:Device[];device:Device|undefined;snapshot:Snapshot|null;now:number;available:boolean;signal:string;listsCollapsed:boolean;
  selectDevice:(id:string)=>void;selectSignal:(key:string)=>void;openHistory:()=>void;historyButtonRef:RefObject<HTMLButtonElement|null>;chart:ReactNode;
}) {
  const [filter,setFilter] = useState('all');
  const [query,setQuery] = useState('');
  const term = query.trim().toLocaleLowerCase();
  const tags = Object.entries(signalLabels).map(([key,name])=>{
    const observation = device?.observations.find(item=>item.signalKey===key);
    return {key,name,observation,state:tagState(snapshot,device,observation,now,available)};
  }).filter(({key,name,state})=>(!term||key.toLocaleLowerCase().includes(term)||name.toLocaleLowerCase().includes(term))&&
    (filter==='all'||
    (filter==='current'&&state==='CURRENT')||
    (filter==='history'&&['HISTORY','RESTORED','FIXTURE'].includes(state))||
    (filter==='missing'&&['ABSENT','MISSING','BAD'].includes(state))));
  const firstTag = tags.some(tag=>tag.key===signal)?signal:tags[0]?.key;
  return <section className="telemetry-layout" data-lists-collapsed={listsCollapsed} aria-label="장비 · 태그 · 신호 추이">
    <div id="instrument-lists" className="instrument-lists" hidden={listsCollapsed}>
      <section className="module-picker" aria-labelledby="module-picker-title">
        <header className="instrument-list-heading">
          <h2 id="module-picker-title">장비 · 모듈</h2>
          {snapshot&&<small>{devices.length}개</small>}
        </header>
        {devices.length?<ul className="module-choices" aria-label="수집 모듈 목록" onKeyDown={moveFocus}>
          {devices.map(row=>{
            const selected = row.deviceId===device?.deviceId;
            const status = collectionEvidence(snapshot,row,now,available);
            const freshness = status?.freshness??(isCurrent(snapshot,row,now,available)?'CURRENT':'UNAVAILABLE');
            const label = moduleLabels[freshness];
            return <li key={row.deviceId}><button type="button" data-pick="" tabIndex={selected?0:-1} aria-pressed={selected}
              data-freshness={freshness} aria-label={`${row.moduleId??row.deviceId} · ${row.deviceId} · ${label}`}
              aria-controls="tag-observations signal-chart-panel" className="module-choice" onClick={()=>selectDevice(row.deviceId)}>
              <span className="module-glyph"><Box size={15} aria-hidden="true"/></span>
              <span className="module-text"><strong>{row.moduleId??row.deviceId}</strong>
                {row.equipmentId&&row.equipmentId!==row.deviceId&&<small>{row.equipmentId}</small>}
                <small title={row.deviceId}>{row.deviceId}</small>
                <small className="module-receipt"><i aria-hidden="true"/>{label}</small>
              </span>
              {selected&&<Check size={13} className="module-check" aria-hidden="true"/>}
            </button></li>;
          })}
        </ul>:<p className="instrument-list-empty">{snapshot?'조건에 맞는 모듈이 없습니다.':'모듈 수신 대기'}</p>}
        {device&&<div className="module-history"><Button size="sm" variant="outline" ref={historyButtonRef} onClick={openHistory}>
          보관 이력 <ChevronRight size={15} aria-hidden="true"/>
        </Button></div>}
      </section>
      <section id="tag-observations" className="tag-panel" aria-labelledby="tag-table-title">
        <header className="instrument-list-heading">
          <h2 id="tag-table-title">태그 관측값</h2>
          {device&&<small>{tags.length}개</small>}
        </header>
        <div className="instrument-tag-tools">
          <label className="instrument-tag-search">
            <Search size={15} aria-hidden="true"/>
            <input type="search" aria-label="태그 이름 또는 ID 검색" placeholder="태그 이름 또는 ID 검색" value={query}
              onChange={event=>setQuery(event.target.value)}/>
          </label>
          <select value={filter} onChange={event=>setFilter(event.target.value)} aria-label="태그 상태 필터">
            <option value="all">전체 상태</option><option value="current">실시간</option>
            <option value="history">과거 · 복원</option><option value="missing">누락 · 품질 불량</option>
          </select>
        </div>
        <div className="table-scroll tag-table-scroll" tabIndex={0} role="region" aria-labelledby="tag-table-title">
          <table className="signal-table tag-table" aria-labelledby="tag-table-title">
            <colgroup><col className="tag-name-column"/><col className="tag-value-column"/><col className="tag-quality-column"/><col className="tag-time-column"/></colgroup>
            <thead><tr><th scope="col">태그</th><th scope="col">값</th><th scope="col">상태</th><th scope="col">관측 시각</th></tr></thead>
            <tbody onKeyDown={moveFocus}>
              {device&&tags.map(({key,name,observation,state})=><tr key={key} data-selected={key===signal} data-state={state}>
                <th scope="row"><button type="button" data-pick="" tabIndex={key===firstTag?0:-1} aria-pressed={key===signal}
                  aria-controls="signal-chart-panel" className="signal-name tag-choice" aria-label={`${name} 차트 선택`} onClick={()=>selectSignal(key)}>
                  <span title={name}>{name}</span><small title={key}>{key}</small>
                </button></th>
                <td className="tag-value">{formatValue(observation?.value)}{observation?.unit&&<small>{observation.unit}</small>}</td>
                <td><Badge variant="secondary" className="tag-state">{state==='HISTORY'&&observation?.quality==='STALE'?'오래됨':tagLabels[state]}</Badge><small className="quality-label">{observation?.quality??'ABSENT'}</small></td>
                <td className="tag-time">{observation?<time title={observation.observedAtUtc} dateTime={observation.observedAtUtc}>{observationClock.format(new Date(observation.observedAtUtc))}</time>:'—'}</td>
              </tr>)}
              {(!device||!tags.length)&&<tr><td colSpan={4} className="empty-table">{device?'조건에 맞는 태그가 없습니다.':'모듈을 선택하면 태그가 표시됩니다.'}</td></tr>}
            </tbody>
          </table>
        </div>
      </section>
    </div>
    <div id="signal-chart-panel" className="instrument-inspector">{chart}</div>
  </section>;
}
