// Self-generated differential fixture (stand-in until dataset/ lands).
//   node scripts/gen-fixture.ts [--count 20000] [--out ../fixtures/f20k] [--seed 7] [--clean]
// Writes <out>/items.json {"schema":1,"items":[...]} and <out>/queries.json {"schema":1,"queries":[{qid,q,cls}]}.
// Deliberately includes hazards the real dataset excludes (İ, Σ, NBSP, U+FEFF, U+0085, tabs,
// astral letters with case mappings, combining marks) unless --clean is given (--clean keeps
// items safe for the cmdk browser oracle: no leading/trailing whitespace, no İ+U+0307 crasher).
import fs from 'node:fs';
import path from 'node:path';
import { arg, flag } from './io.ts';

const count = Number(arg('--count', '20000'));
const out = path.resolve(arg('--out', path.join(import.meta.dirname, '../../fixtures/f20k'))!);
const clean = flag('--clean');
let s = Number(arg('--seed', '7')) >>> 0;
const rnd = () => { s = (s + 0x6d2b79f5) >>> 0; let t = s; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
const pick = <T,>(a: readonly T[]) => a[Math.floor(rnd() * a.length)];

const latin = 'open file go to settings toggle theme git branch commit push pull request terminal new window close tab split editor format document search replace workspace extension install run debug test build recent folder preferences keyboard shortcuts user snippets source control explorer output problems panel view zoom reset reload developer tools command palette markdown preview json schema python rust typescript react component hook layout effect state memo'.split(' ');
const other = ['Öffnen', 'Datei', 'Ordner', 'naïve', 'café', 'résumé', 'Ångström', '設定を開く', '打开文件', '設定', '檔案', '열기', 'فتح', 'ملف', 'لا', 'פתח', 'קובץ', 'फ़ाइल', 'खोलें', 'क्षत्रिय', '👩‍💻', '🇯🇵', '👍🏽', '1️⃣', '👨‍👩‍👧', 'ΑΣ', 'ΟΔΟΣ', 'Σοφία', 'ΣΊΣΥΦΟΣ', 'İstanbul', 'DİYARBAKIR', 'ǅemal', 'ﬁle', 'ß', 'ẞig', '𐐀𐐁', '𐐨', '𝐀𝐁𝐂', 'Ωmega', 'ｏｐｅｎ', 'i̇x', 'école', 'ABCͅD'];
const seps = [' ', ' ', ' ', '/', '-', '_', '.', '#', '@', '+', '&', '(', '[', '{', '"', '\\'];
const hazardSeps = [' ', ' ', '　', '﻿', '\u0085', '\t', ' ', ' ', ' ', ' - '];
const caseFx = [(w: string) => w, (w: string) => w, (w: string) => w[0].toUpperCase() + w.slice(1), (w: string) => w.toUpperCase()];

const seen = new Set<string>();
const items: string[] = [];
while (items.length < count) {
  const n = 2 + Math.floor(rnd() * 6);
  const words: string[] = [];
  for (let k = 0; k < n; k++) words.push(pick(caseFx)(rnd() < 0.08 ? pick(other) : pick(latin)));
  let str = '';
  for (let k = 0; k < words.length; k++) {
    if (k) str += !clean && rnd() < 0.04 ? pick(hazardSeps) : rnd() < 0.7 ? ' ' : pick(seps);
    str += words[k];
  }
  if (!clean && rnd() < 0.01) str = pick([' ', '\t', '-']) + str;
  if (clean) str = str.trim();
  if ([...str].length > 80 || seen.has(str) || !str) continue;
  seen.add(str);
  items.push(str);
}

const qs: [string, string][] = [
  ['single', 'a'], ['single', 'o'], ['single', 'T'], ['single', 'z'], ['single', 'é'],
  ['prefix', 'op'], ['prefix', 'Op'], ['prefix', 'ope'], ['prefix', 'open'], ['prefix', 'sett'], ['prefix', 'settings'],
  ['multi', 'open fi'], ['multi', 'toggle th'], ['multi', 'new window'],
  ['acronym', 'gtb'], ['acronym', 'osf'], ['acronym', 'cp'],
  ['mid', 'ranc'], ['mid', 'ett'], ['mid', 'indo'],
  ['transposition', 'reqeust'], ['transposition', 'tset'], ['transposition', 'ba'], ['transposition', 'opne'],
  ['doubled', 'oppen'], ['doubled', 'opp'], ['doubled', 'sseet'],
  ['case', 'BRANCH'], ['case', 'Git'], ['case', 'oPeN'],
  ['sep', 'git-br'], ['sep', 's/b'], ['sep', 'o-f'], ['sep', 'o f'], ['sep', 'a b'], ['sep', 'a-b'], ['sep', 'te te'], ['sep', 'x.y'],
  ['ws', ' '], ['ws', 'x '], ['ws', ' o'], ['ws', ' '], ['ws', '﻿'], ['ws', '  '],
  ['nonlatin', '設定'], ['nonlatin', 'ملف'], ['nonlatin', '👩'], ['nonlatin', '🇯'], ['nonlatin', 'फ़'], ['nonlatin', 'caf'], ['nonlatin', 'café'],
  ['nfd', 'café'], ['nfd', 'é'],
  ['casefold', 'σ'], ['casefold', 'ς'], ['casefold', 'Σ'], ['casefold', 'οδος'], ['casefold', 'istanbul'], ['casefold', 'İ'], ['casefold', 'i̇'],
  ['casefold', '𐐨'], ['casefold', '𐐀'], ['casefold', 'ss'], ['casefold', 'ﬁ'], ['casefold', 'ǆ'],
  ['nomatch', 'xyzzyq'], ['nomatch', 'qqqqqqqqqqqqqqqqqqqq'],
  ['ascii20', 'open recent file tes'], ['ascii20', 'toggle theme devtool'],
  ['empty', ''],
];
if (!clean) qs.push(['crash', 'İ̇']); // upstream cmdk throws RangeError when an item contains U+0307 not preceded by U+0307
const queries = qs.map(([cls, q], i) => ({ qid: i, q, cls }));

fs.mkdirSync(out, { recursive: true });
fs.writeFileSync(path.join(out, 'items.json'), JSON.stringify({ schema: 1, seedId: `fixture-${count}${clean ? '-clean' : ''}`, count, items }) + '\n');
fs.writeFileSync(path.join(out, 'queries.json'), JSON.stringify({ schema: 1, queries }) + '\n');
console.log(`wrote ${items.length} items, ${queries.length} queries to ${out}`);
