import {memo, useEffect, useId, useMemo, useRef, useState, type KeyboardEvent} from 'react';
import {init, use, type ECharts} from 'echarts/core';
import {LineChart, ScatterChart} from 'echarts/charts';
import {GridComponent, MarkAreaComponent, MarkLineComponent, TooltipComponent} from 'echarts/components';
import {CanvasRenderer} from 'echarts/renderers';
import {formatValue, historyWindow, type HistoryRange, type Point} from './data';
import {freezeHistory, inspectHistory, movePointCursor, pointKey, pointState, signalSeries, type FrozenHistory} from './investigation';
import './instrument-trend.css';

use([LineChart, ScatterChart, GridComponent, MarkAreaComponent, MarkLineComponent, TooltipComponent, CanvasRenderer]);

const cursorKeys = ['ArrowLeft', 'ArrowRight', 'Home', 'End'];
const palette = {
  face: '#0d1925',
  grid: '#233546',
  axis: '#547187',
  ink: '#abc4d6',
  live: '#70e5cb',
  past: '#a9bfff',
  gap: '#e0ad76',
  interrupt: '#ffb3a9',
  cursor: '#bddaff',
};
const markerStyles = {
  transport: {color: palette.interrupt, type: 'dashed' as const},
  gap: {color: palette.gap, type: 'dotted' as const},
};
const axisFont = 'Cascadia Mono, Consolas, monospace';
const timestamp = (t: number) => new Date(t).toISOString();
const clock = (value: string) => value.slice(11, 19);

function gapBands(points: readonly Point[]) {
  const bands: [{xAxis: number}, {xAxis: number}][] = [];
  let previousObservation: number | undefined;
  let inGap = false;
  for (const point of points) {
    if (point.marker) {
      inGap = true;
      continue;
    }
    if (inGap && previousObservation !== undefined && point.t > previousObservation) {
      bands.push([{xAxis: previousObservation}, {xAxis: point.t}]);
    }
    previousObservation = point.t;
    inGap = false;
  }
  return bands;
}

function qualityMarks(points: readonly Point[]) {
  return points.filter(point => !point.marker).map(point => ({
    value: [point.t, .5],
    symbol: point.v === null ? 'diamond' : 'rect',
    itemStyle: {color: point.v === null ? palette.gap : palette.live},
  }));
}

function EvidenceTime({value}: {value?: string}) {
  const instant = value === undefined ? NaN : Date.parse(value);
  if (!Number.isFinite(instant)) return <>—</>;
  const utc = timestamp(instant);
  return <time title={utc} dateTime={utc}>{clock(utc)}</time>;
}

function SignalChart({points, label, unit, historical, context}: {
  points: Point[];
  label: string;
  unit: string;
  historical: boolean;
  context: string;
}) {
  const host = useRef<HTMLDivElement>(null);
  const chart = useRef<ECharts | null>(null);
  const [range, setRange] = useState<HistoryRange>('all');
  const [frozen, setFrozen] = useState<FrozenHistory | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const rangeId = useId();
  const cursorHintId = useId();
  const inspectionPoints = frozen?.points ?? points;
  const visiblePoints = useMemo(() => historyWindow(inspectionPoints, range), [inspectionPoints, range]);
  const summary = useMemo(() => inspectHistory(visiblePoints), [visiblePoints]);
  const chartSeries = useMemo(() => signalSeries(visiblePoints), [visiblePoints]);
  const rows = useMemo(() => visiblePoints.slice().reverse(), [visiblePoints]);
  const markers = useMemo(() => visiblePoints.filter(point => point.marker !== undefined), [visiblePoints]);
  const bands = useMemo(() => gapBands(visiblePoints), [visiblePoints]);
  const quality = useMemo(() => qualityMarks(visiblePoints), [visiblePoints]);
  const interruptionCount = markers.filter(point => point.marker === 'transport').length;
  const gapCount = markers.length - interruptionCount;
  const latest = visiblePoints.at(-1);
  const selected = cursor === null ? latest : visiblePoints.find(point => pointKey(point) === cursor);
  const selectedIndex = selected === undefined ? -1 : visiblePoints.indexOf(selected);
  const moveCursor = (key: string) => setCursor(previous => movePointCursor(
    visiblePoints, previous ?? (latest ? pointKey(latest) : null), key,
  ));
  const handleCursorKey = (event: KeyboardEvent<HTMLElement>) => {
    if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey || !cursorKeys.includes(event.key)) return;
    event.preventDefault();
    moveCursor(event.key);
  };
  const muted = historical || frozen !== null;
  const hasPoints = visiblePoints.length > 0;
  const hasValues = summary.valid > 0;

  useEffect(() => {
    if (!hasPoints || !host.current) return;
    const instance = init(host.current, undefined, {renderer: 'canvas'});
    chart.current = instance;
    const resize = new ResizeObserver(() => instance.resize());
    resize.observe(host.current);
    return () => { resize.disconnect(); instance.dispose(); chart.current = null; };
  }, [hasPoints]);

  useEffect(() => {
    if (summary.start === null || summary.end === null) return;
    const min = summary.start === summary.end ? summary.start - 1000 : summary.start;
    const max = summary.start === summary.end ? summary.end + 1000 : summary.end;
    const baseTimeAxis = {type: 'time', min, max, axisLine: {lineStyle: {color: palette.axis}}, axisTick: {show: false}};
    const marks = markers.flatMap(point => point.marker ? [{xAxis: point.t, lineStyle: markerStyles[point.marker]}] : []);
    if (selected) marks.push({xAxis: selected.t, lineStyle: {color: palette.cursor, type: 'dashed'}});
    const traceColor = muted ? palette.past : palette.live;
    chart.current?.setOption({
      animation: false,
      backgroundColor: palette.face,
      useUTC: true,
      textStyle: {fontFamily: axisFont},
      grid: [
        {left: 57, right: 17, top: 13, bottom: 55},
        {left: 57, right: 17, bottom: 31, height: 13},
      ],
      tooltip: {
        trigger: 'axis',
        renderMode: 'richText',
        backgroundColor: '#162737',
        borderColor: '#3d586b',
        textStyle: {color: '#e6eff5'},
      },
      xAxis: [
        {...baseTimeAxis, axisLabel: {show: false}, splitLine: {show: true, lineStyle: {color: palette.grid}}},
        {...baseTimeAxis, gridIndex: 1, name: '품질', nameLocation: 'start', nameGap: 6,
          nameTextStyle: {color: palette.ink, fontSize: 10, fontFamily: 'Segoe UI, Malgun Gothic, sans-serif'},
          axisLabel: {color: palette.ink, fontSize: 10, hideOverlap: true, margin: 8,
            formatter: (value: number) => clock(timestamp(value))}},
      ],
      yAxis: [
        {type: 'value', scale: true, show: hasValues, axisLabel: {color: palette.ink, fontSize: 10},
          splitLine: {lineStyle: {color: palette.grid}}},
        {type: 'value', gridIndex: 1, min: 0, max: 1, show: false},
      ],
      series: [
        {...chartSeries, name: unit ? label + ' (' + unit + ')' : label, lineStyle: {width: 2, color: traceColor},
          itemStyle: {color: traceColor},
          markLine: {silent: true, symbol: 'none', label: {show: false}, data: marks},
          markArea: {silent: true, itemStyle: {color: '#583b2ac9'}, label: {show: false}, data: bands}},
        {type: 'scatter', silent: true, xAxisIndex: 1, yAxisIndex: 1, symbolSize: 7,
          tooltip: {show: false}, data: quality.map(point => point.symbol === 'rect'
            ? {...point, itemStyle: {color: traceColor}} : point)},
      ],
    }, true);
  }, [bands, chartSeries, hasValues, label, markers, muted, quality, selected, summary.start, summary.end, unit]);

  const timeLabel = selected?.marker === 'transport' ? '중단 감지 시각' : '원본 관측 시각';
  const selectedTime = !selected || selected.marker === 'gap' ? undefined : timestamp(selected.detectedAt ?? selected.t);

  return <div className="signal-investigation instrument-trend">
    <div className="chart-window trend-controls">
      <p className={frozen ? 'inspection-state is-frozen' : 'inspection-state'} role="status">
        {frozen ? <>조사 화면 고정 <time title={timestamp(frozen.capturedAt)} dateTime={timestamp(frozen.capturedAt)}>{clock(timestamp(frozen.capturedAt))} UTC</time> · 수집은 계속됩니다.</>
          : historical ? '과거 UI 요약' : '수신 UI 요약'}
      </p>
      <label className="sr-only" htmlFor={rangeId}>표시 범위</label>
      <select id={rangeId} value={range} onChange={event => setRange(event.target.value === 'all' ? 'all' : event.target.value === '30' ? 30 : 60)}>
        <option value="all">보관 중인 전체</option>
        <option value="30">최근 30초</option>
        <option value="60">최근 60초</option>
      </select>
      <button className="inspection-toggle" type="button" aria-pressed={frozen !== null}
        onClick={() => setFrozen(frozen ? null : freezeHistory(points, Date.now()))}>
        {frozen ? '실시간 요약 따라가기' : '조사 화면 고정'}
      </button>
    </div>
    <div className="chart-cursor" role="group" aria-label={label + ' 요약 커서'} aria-describedby={cursorHintId}
      aria-keyshortcuts="ArrowLeft ArrowRight Home End" tabIndex={hasPoints ? 0 : undefined} onKeyDown={handleCursorKey}>
      <figure className="trend-figure">
        <div className="trend-heading">
          <strong>신호 추이</strong>
          {unit && <span className="trend-unit">{unit}</span>}
          <span className="chart-window-time">
            {summary.start !== null && summary.end !== null
              ? <><EvidenceTime value={timestamp(summary.start)} />–<EvidenceTime value={timestamp(summary.end)} /> UTC</>
              : '보관된 요약 없음'}
          </span>
        </div>
        <div className="trend-plot">
          <div ref={host} className="chart" hidden={!hasPoints} role="img"
            aria-label={label + ' ' + unit + ', 1Hz UI 요약 ' + summary.summaries + '개. 사용 가능 ' + summary.valid
              + '개, 값 사용 불가 ' + summary.unavailable + '개. 아래에서 관측을 조사할 수 있습니다.'}/>
          {!hasValues && <div className="empty-chart trend-empty" role="status">
            <p>표시 범위에 사용 가능한 표본이 없습니다.</p>
            <small>누락·지연·수신 단절을 값 0과 구분합니다.</small>
          </div>}
        </div>
        <figcaption className="trend-legend" aria-label="차트 공백 구분과 표본 품질">
          <span className="trace-key" data-past={muted}>{muted ? '고정·과거 요약' : '사용 가능 표본'}</span>
          <span className="quality-key">표본별 품질</span>
          {summary.unavailable > 0 && <span className="unavailable-key">값 사용 불가 {summary.unavailable}건</span>}
          {interruptionCount > 0 && <span className="interrupt-key">연결 중단 {interruptionCount}건</span>}
          {gapCount > 0 && <span className="gap-key">긴 관측 간격 {gapCount}건</span>}
          <span className="trend-count">유효 {summary.valid} / 관측 {summary.summaries}건</span>
        </figcaption>
      </figure>
      <div className="chart-cursor-detail">
        {selected ? <dl className="chart-cursor-result" role="status">
          <div><dt>커서 시점 값</dt><dd className="trend-cursor-value">
            {selected.marker || selected.v === null ? '—' : String(selected.v) + ' ' + unit}
            {!selected.marker && selected.v === null && selected.receivedValue != null
              && <small>받은 값 {selected.receivedValue} {unit} · 추이에서 제외</small>}
          </dd></div>
          <div><dt>{timeLabel}</dt><dd><EvidenceTime value={selectedTime}/></dd></div>
          <div><dt>수집기 수신 시각</dt><dd><EvidenceTime value={selected.receivedAtUtc}/></dd></div>
          <div><dt>수신 품질</dt><dd>{selected.quality ? selected.quality + ' · ' : ''}{pointState(selected)}</dd></div>
        </dl> : <div role="status" className="chart-cursor-result trend-cursor-empty">
          {cursor ? '선택한 항목이 표시 범위에 없습니다.' : '보관된 UI 요약 없음'}
        </div>}
        <div className="trend-cursor-tools">
          <p className="chart-cursor-hint" id={cursorHintId}>← / → 관측 이동 · Home / End 처음과 끝 · UTC</p>
          <div className="chart-cursor-actions">
            <button type="button" disabled={!hasPoints || selectedIndex === 0} onClick={() => moveCursor('ArrowLeft')} aria-label="이전 관측">이전</button>
            <button type="button" disabled={!hasPoints || selectedIndex === visiblePoints.length - 1} onClick={() => moveCursor('ArrowRight')} aria-label="다음 관측">다음</button>
          </div>
        </div>
      </div>
    </div>
    <dl className="inspection-summary">
      <div><dt>표시 / 보관 요약</dt><dd>{summary.summaries} / {inspectionPoints.filter(point => !point.marker).length}</dd></div>
      <div><dt>사용 가능 / 사용 불가</dt><dd>{summary.valid} / {summary.unavailable}</dd></div>
      <div><dt>2.5초 초과 간격</dt><dd>{summary.gaps}</dd></div>
      <div><dt>요약 최솟값 / 최댓값</dt><dd>{formatValue(summary.min)} / {formatValue(summary.max)} <small>{unit}</small></dd></div>
    </dl>
    <details className="inspection-details">
      <summary>UI 요약 표로 조사 ({summary.summaries}개{markers.length ? ' · 구간 표식 ' + markers.length + '개' : ''})</summary>
      <p className="chart-note">최근 시각부터 표시합니다. 값 없음과 단절 표시는 0이 아닙니다. 원시 100Hz 신호·DB 보관 이력이 아니며, 요약 사이의 짧은 변화는 복원할 수 없습니다.</p>
      <div className="inspection-table-scroll" tabIndex={0} role="region" aria-label={context + ' ' + label + ' UI 요약 표'}>
        <table className="inspection-table">
          <caption>{context} · {label} · {frozen ? '고정된 조사 화면' : '수신 UI 요약'} · UTC</caption>
          <thead><tr><th scope="col">관측·중단 감지 시각 (UTC)</th><th scope="col">값 ({unit})</th><th scope="col">요약 상태</th></tr></thead>
          <tbody>{rows.map(point => {
            const time = timestamp(point.detectedAt ?? point.t);
            const key = pointKey(point);
            return <tr key={key} data-selected={selected !== undefined && pointKey(selected) === key}>
              <th scope="row">{point.marker === 'gap' ? '관측 사이' : <time dateTime={time}>{time}</time>}</th>
              <td>{point.v === null ? '값 없음' : formatValue(point.v)}</td>
              <td>{pointState(point)}{point.quality && ' · ' + point.quality}</td>
            </tr>;
          })}</tbody>
        </table>
      </div>
    </details>
  </div>;
}

export default memo(SignalChart);
