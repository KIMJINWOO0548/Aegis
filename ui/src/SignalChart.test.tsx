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
});
