import { test } from "node:test";
import assert from "node:assert/strict";
import {
  validateItem,
  toNfd,
  ALLOWED_RANGES,
  BANNED_CODE_POINTS,
  approxClusters,
  approxGraphemeLength,
  codePoints,
  cpLength,
  isTypeable,
  hasNfdDecomposition,
} from "../src/unicode.ts";
import { loadCorpus, NON_LATIN_LOCALES } from "../src/data.ts";

const OK = "Open Recent File";

test("valid items pass", () => {
  for (const s of [OK, "ファイルを開く (Open File)", "👩‍💻 Toggle Word Wrap", "फ़ाइल खोलें - नया टैब", "الصفحة ٣ من ١٢", "🇯🇵 Switch Language", "1️⃣ Step One: Run Task"]) {
    assert.equal(validateItem(s), null, s);
  }
});

test("validator rejects each banned case", () => {
  const cases: [string, RegExp][] = [
    ["short", /too short/],
    ["x".repeat(81), /too long/],
    ["\u{1F600}".repeat(81), /too long/],
    [" Open Recent File", /leading or trailing/],
    ["Open Recent File ", /leading or trailing/],
    ["Open\tRecent File", /control/],
    ["Open Recent\u0007File", /control/],
    ["Open Recent\u0085File", /control/],
    ["Open Recent File", /whitespace/],
    ["Open　Recent File", /whitespace/],
    ["Open Recent File", /whitespace/],
    ["Open Recent‮File", /bidi/],
    ["Open Recent‪File", /bidi/],
    ["Open Recent⁦File⁩", /bidi/],
    ["Open Recent‏File", /bidi/],
    ["İstanbul Office", /U\+0130/],
    ["Settings Σ Panel", /U\+03A3/],
    ["Café Settings", /repertoire|NFC/],
    ["Open Recent \ud800File", /lone surrogate/],
    ["Open Recent \udc00File", /lone surrogate/],
    ["\u{1FAE8} Shaking Face", /repertoire/], // Emoji 15.0
    ["\u{1F6DC} Wireless Mode", /repertoire/], // Emoji 15.0
    ["\u{1FAE9} Face With Bags", /repertoire/], // Emoji 16.0
    ["Settings \u{E0067}\u{E007F}", /repertoire/], // tag characters
    ["Open  Recent File", /double space/],
    ["Open Recent‍File", /ZWJ/],
    ["Open File फ़", /NFC/], // U+095E is a composition exclusion
    ["Open File ​ here", /repertoire/],
  ];
  for (const [s, re] of cases) {
    const r = validateItem(s);
    assert.ok(r !== null && re.test(r), `${JSON.stringify(s)} -> ${r}`);
  }
});

test("NFD check: decomposed Hangul and kana are rejected", () => {
  assert.match(validateItem(toNfd("파일 열기 설정 열기")) ?? "", /repertoire|NFC/);
  assert.match(validateItem("ファイルを保存" + toNfd("ダウンロード")) ?? "", /NFC/);
});

test("toNfd agrees with String.prototype.normalize('NFD') over the allowed repertoire", () => {
  const bad: string[] = [];
  for (const [lo, hi] of ALLOWED_RANGES) {
    for (let cp = lo; cp <= hi; cp++) {
      if (BANNED_CODE_POINTS.has(cp)) continue;
      const s = String.fromCodePoint(cp);
      const want = s.normalize("NFD");
      if (toNfd(s) !== want) bad.push(cp.toString(16));
      if (hasNfdDecomposition(cp) !== (want !== s)) bad.push("flag:" + cp.toString(16));
    }
  }
  assert.deepEqual(bad, []);
});

test("approximate clusters keep emoji sequences and marks together", () => {
  assert.equal(approxGraphemeLength("👨‍👩‍👧‍👦"), 1);
  assert.equal(approxGraphemeLength("🇯🇵🇰🇷"), 2);
  assert.equal(approxGraphemeLength("1️⃣"), 1);
  assert.equal(approxGraphemeLength("👍🏽"), 1);
  assert.equal(approxGraphemeLength("फ़ाइल"), 3); // फ़ा इ ल
  assert.equal(approxClusters("ab").length, 2);
  assert.equal(cpLength("👩‍💻"), 3);
  assert.deepEqual(codePoints("a😀"), [0x61, 0x1f600]);
});

test("isTypeable = ASCII lower-case, digits, space", () => {
  for (const q of ["a", "open fi", "gtb", " ", "x ", "abc123"]) assert.equal(isTypeable(q), true, q);
  for (const q of ["", "A", "Open", "git-br", "s/b", "設定", "é", "a\tb", "a_b"]) assert.equal(isTypeable(q), false, q);
});

test("committed phrase lists are NFC and inside the repertoire", () => {
  const c = loadCorpus();
  const all = [
    ...NON_LATIN_LOCALES.flatMap((l) => c.phrases[l].map((p) => p.text)),
    ...c.latinExtended.map((p) => p.text),
    ...c.emoji.map((e) => e.emoji + " " + e.label),
  ];
  for (const p of all) {
    const padded = p.replace(/\{[nm]\}/g, "1") + " Settings Panel"; // pad to >= 8 code points
    const r = validateItem(padded.length > 0 ? padded : p);
    assert.equal(r, null, `${p}: ${r}`);
  }
  assert.ok(c.eff.length === 7776);
});
