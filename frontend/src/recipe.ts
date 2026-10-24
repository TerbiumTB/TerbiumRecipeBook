import type {Quantity,Recipe} from './types';
export const units:Record<string,string>={g:'г',kg:'кг',ml:'мл',l:'л',piece:'шт.',tsp:'ч. л.',tbsp:'ст. л.',pinch:'щеп.',clove:'зубч.',bunch:'пуч.'};
export const localId=()=> 'i'+crypto.randomUUID().replaceAll('-','');
export function quantity(q:Quantity,ratio:number){const n=(v:number)=>new Intl.NumberFormat('ru',{maximumFractionDigits:2}).format(v*ratio);if(q.kind==='exact')return `${n(q.value)} ${units[q.unit]}`;if(q.kind==='range')return `${n(q.min)}–${n(q.max)} ${units[q.unit]}`;return q.kind==='to_taste'?'по вкусу':'по необходимости'}
export function blank():Recipe{return {schemaVersion:1,title:'',servings:2,time:{totalMinutes:30,activeMinutes:15},ingredients:[{id:localId(),name:'',quantity:{kind:'exact',value:100,unit:'g'},preparation:null,group:null,optional:false}],steps:[{id:localId(),title:'',instruction:'',ingredientIds:[],timerSeconds:null}],userNotes:''}}
