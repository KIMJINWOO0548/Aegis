import {describe,it,expect} from 'vitest';
import {historyWindow,type Point} from './data';

describe('retained-history time window',()=>{
 it('uses observation time rather than wall clock and includes its boundary',()=>{
  const points:Point[]=[{t:1000,v:1,id:'old'},{t:31000,v:2,id:'boundary'},{t:61000,v:3,id:'latest'}];
  expect(historyWindow(points,30).map(p=>p.id)).toEqual(['boundary','latest']);
  expect(historyWindow(points,60)).toEqual(points);
 });
 it('keeps discontinuities and never changes retained samples when switching range',()=>{
  const points:Point[]=[{t:0,v:1,id:'first'},{t:60000,v:null,id:'gap'},{t:61000,v:8,id:'last'}];
  const original=structuredClone(points);
  expect(historyWindow(points,30).map(p=>p.v)).toEqual([null,8]);
  expect(historyWindow(points,'all')).toEqual(original);
  expect(points).toEqual(original);
 });
 it('handles empty history without inventing values',()=>{
  expect(historyWindow([],30)).toEqual([]);
  expect(historyWindow([],'all')).toEqual([]);
 });
});
