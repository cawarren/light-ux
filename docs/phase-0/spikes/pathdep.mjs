import { JSDOM } from 'jsdom';
const dom = new JSDOM('<!doctype html><div id=root></div>', { pretendToBeVisual: true });
globalThis.window = dom.window; globalThis.document = dom.window.document;
globalThis.HTMLElement = dom.window.HTMLElement;
globalThis.Element = dom.window.Element; globalThis.Node = dom.window.Node;
globalThis.ResizeObserver = class { observe(){} unobserve(){} disconnect(){} };
dom.window.HTMLElement.prototype.scrollIntoView = function(){};
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const React = (await import('react')).default;
const { createRoot } = await import('react-dom/client');
const { act } = await import('react');
const { Command } = await import('cmdk');
const { commandScore } = await import('./node_modules/cmdk/dist/command-score.mjs');

// deterministic PRNG
let s = 12345; const rnd = () => (s = (s * 1103515245 + 12345) >>> 0) / 2**32;
const syl = ['co','ma','nd','ex','pa','le','ti','on','re','st','ar','ch','ing','fi','le'];
const items = Array.from({length: 1500}, (_, i) => {
  const w = () => Array.from({length: 1 + Math.floor(rnd()*3)}, () => syl[Math.floor(rnd()*syl.length)]).join('');
  return `${w()} ${w()} ${w()} ${i}`;   // unique via suffix
});
function App(){ return React.createElement(Command, {label:'x'},
  React.createElement(Command.Input, {}),
  React.createElement(Command.List, {}, items.map((t,i)=>React.createElement(Command.Item,{key:i,value:t,'data-idx':i},t))));
}
async function mount(){ const el=document.createElement('div'); document.body.appendChild(el); const root=createRoot(el); await act(async()=>root.render(React.createElement(App))); return {el,root}; }
const setter = Object.getOwnPropertyDescriptor(dom.window.HTMLInputElement.prototype,'value').set;
async function type(el, v){ const inp=el.querySelector('[cmdk-input]'); await act(async()=>{ setter.call(inp,v); inp.dispatchEvent(new dom.window.Event('input',{bubbles:true})); }); }
const order = el => [...el.querySelectorAll('[cmdk-item]')].map(n=>+n.getAttribute('data-idx'));

const Q = 'coex';
// A: paste full query into fresh mount
let A = await mount(); await type(A.el, Q); const oA = order(A.el);
// B: type char by char
let B = await mount(); for (let k=1;k<=Q.length;k++) await type(B.el, Q.slice(0,k)); const oB = order(B.el);
// C: type, backspace to empty, a different query, clear, retype
let C = await mount(); for (const v of ['m','ma','','st','s','','c','co','coe','coex']) await type(C.el, v); const oC = order(C.el);
// canonical: score desc, index asc
const sc = items.map((t,i)=>[i, commandScore(t, Q, [])]).filter(x=>x[1]>0).sort((a,b)=>b[1]-a[1]||a[0]-b[0]);
const canon = sc.map(x=>x[0]);
const eq=(a,b)=>a.length===b.length&&a.every((x,i)=>x===b[i]);
const distinct = new Set(sc.map(x=>x[1])).size;
console.log({matches: canon.length, distinctScores: distinct, A_eq_canon: eq(oA,canon), B_eq_canon: eq(oB,canon), C_eq_canon: eq(oC,canon), A_eq_B: eq(oA,oB)});
// are B/C differences only within tie groups?
const scoreOf = new Map(sc.map(x=>[x[0],x[1]]));
const bucketsEq = o => o.length===canon.length && o.every((x,i)=>scoreOf.get(x)===scoreOf.get(canon[i]));
console.log({B_tieInsensitiveEq: bucketsEq(oB), C_tieInsensitiveEq: bucketsEq(oC)});
let firstDiff = oC.findIndex((x,i)=>x!==canon[i]); console.log('first C diff at rank', firstDiff);
process.exit(0);
