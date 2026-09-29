import { commandScore } from './node_modules/cmdk/dist/command-score.mjs';
import fs from 'fs';
let s=7; const rnd=()=>(s=(s*1103515245+12345)>>>0)/2**32;
const words='open file go to settings toggle theme git branch commit push pull request terminal new window close tab split editor format document search replace workspace extension install run debug test build Öffnen Datei 設定を開く 打开文件 فتح ملف פתח קובץ फ़ाइल खोलें 👩‍💻 🇯🇵 naïve café'.split(' ');
const seps=[' ','/','-','_','.',' ',' '];
const items=Array.from({length:20000},(_,i)=>{let t=[];const n=2+Math.floor(rnd()*8);for(let k=0;k<n;k++)t.push((rnd()<.2?w=>w[0].toUpperCase()+w.slice(1):w=>w)(words[Math.floor(rnd()*words.length)]));return t.join(seps[Math.floor(rnd()*seps.length)])});
const qs=['a','o','op','Op','ope','open','open fi','gtb','sett','xyz','tset','oppen','settings','設定','ملف','👩','caf','o-f','o f','s/b','T','BRANCH','reqeust','git-br','te te'];
const pow=[];for(let k=0;k<=4096;k++){const b=Buffer.alloc(8);b.writeDoubleLE(Math.pow(0.999,k));pow.push(b.readBigUInt64LE().toString())}
const out={items,qs,pow,scores:qs.map(q=>items.map(it=>{const b=Buffer.alloc(8);b.writeDoubleLE(commandScore(it,q,[]));return b.readBigUInt64LE().toString()}))};
fs.writeFileSync('fixture.json',JSON.stringify(out));
