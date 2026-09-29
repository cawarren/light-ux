import { commandScore } from './node_modules/cmdk/dist/command-score.mjs';
let s=7; const rnd=()=>(s=(s*1103515245+12345)>>>0)/2**32;
const words='open file go to settings toggle theme git branch commit push pull request terminal new window close tab split editor format document search replace workspace extension install run debug test build'.split(' ');
const items=Array.from({length:50000},(_,i)=>{let t=[];const n=2+Math.floor(rnd()*8);for(let k=0;k<n;k++)t.push(words[Math.floor(rnd()*words.length)]);return t.join(rnd()<.3?'/':' ')+' '+i});
const qs=['a','o','op','ope','open','open fi','gtb','sett','xyz','tset','open file settings tog'];
for(const q of qs){const t=performance.now();let m=0,ties=new Map();for(const it of items){const v=commandScore(it,q,[]);if(v>0){m++;ties.set(v,(ties.get(v)||0)+1)}}console.log(JSON.stringify(q),'ms',(performance.now()-t).toFixed(0),'matches',m,'distinct',ties.size,'largestTie',Math.max(0,...ties.values()))}
