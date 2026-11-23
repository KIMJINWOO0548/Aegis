import {describe,it,expect} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import SignalChart from './SignalChart';

describe('chart unavailable state',()=>{
 it('explains missing samples but keeps an actual zero as a chart',()=>{
  const render=(v:number|null)=>renderToStaticMarkup(<SignalChart points={[{t:0,v,id:'sample'}]} label="실측 유량" unit="L/min" historical={false} context="synthetic unit test"/>);
  expect(render(null)).toContain('표시 범위에 사용 가능한 표본이 없습니다.');
  expect(render(0)).not.toContain('표시 범위에 사용 가능한 표본이 없습니다.');
  expect(render(0)).toContain('role="img"');
 });
 it('exposes the keyboard instructions and distinguishes transport from observation gaps',()=>{
  const rendered=renderToStaticMarkup(<SignalChart points={[
   {t:0,v:0,id:'sample',quality:'GOOD'},
   {t:10,v:null,id:'transport',marker:'transport'},
   {t:20,v:null,id:'gap',marker:'gap'},
   {t:21,v:null,id:'missing',quality:'MISSING'},
  ]} label="실측 유량" unit="L/min" historical={false} context="synthetic unit test"/>);
  expect(rendered).toContain('aria-keyshortcuts="ArrowLeft ArrowRight Home End"');
  expect(rendered).toContain('연결 중단 1건');
  expect(rendered).toContain('긴 관측 간격 1건');
  expect(rendered).toContain('값 사용 불가 · MISSING');
 });
});
