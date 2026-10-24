import {test} from 'node:test';
import assert from 'node:assert/strict';
import {quantity,blank} from './recipe.ts';
test('scales exact values and ranges without changing source',()=>{const q={kind:'range' as const,min:5,max:10,unit:'g'};assert.equal(quantity(q,1.5),'7,5–15 г');assert.equal(q.min,5);assert.equal(quantity({kind:'exact',value:2,unit:'piece'},.5),'1 шт.');});
test('qualitative quantities stay qualitative',()=>{assert.equal(quantity({kind:'to_taste'},10),'по вкусу');assert.equal(quantity({kind:'as_needed'},.5),'по необходимости');});
test('new rows have distinct stable ids',()=>{assert.notEqual(blank().ingredients[0].id,blank().ingredients[0].id)});
