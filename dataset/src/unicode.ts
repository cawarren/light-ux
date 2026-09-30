// Code point helpers, the item validator, the script classifier, a small
// NFD decomposer for the NFD query class, and an approximate grapheme count.
//
// Determinism: nothing here uses Intl or locale-dependent APIs. The only
// runtime normalization call is the NFC assertion inside validateItem(),
// which is the allowed exception (see README).

export function codePoints(s: string): number[] {
  const out: number[] = [];
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i);
    if (c >= 0xd800 && c <= 0xdbff && i + 1 < s.length) {
      const d = s.charCodeAt(i + 1);
      if (d >= 0xdc00 && d <= 0xdfff) {
        out.push(((c - 0xd800) << 10) + (d - 0xdc00) + 0x10000);
        i++;
        continue;
      }
    }
    out.push(c);
  }
  return out;
}

export function cpLength(s: string): number {
  let n = 0;
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i);
    if (c >= 0xd800 && c <= 0xdbff && i + 1 < s.length) {
      const d = s.charCodeAt(i + 1);
      if (d >= 0xdc00 && d <= 0xdfff) i++;
    }
    n++;
  }
  return n;
}

export function fromCodePoints(cps: readonly number[]): string {
  let s = "";
  for (const cp of cps) s += String.fromCodePoint(cp);
  return s;
}

export function hasLoneSurrogate(s: string): boolean {
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i);
    if (c >= 0xd800 && c <= 0xdbff) {
      const d = i + 1 < s.length ? s.charCodeAt(i + 1) : 0;
      if (d >= 0xdc00 && d <= 0xdfff) {
        i++;
        continue;
      }
      return true;
    }
    if (c >= 0xdc00 && c <= 0xdfff) return true;
  }
  return false;
}

type Range = readonly [number, number];

function inRanges(cp: number, ranges: readonly Range[]): boolean {
  for (const [lo, hi] of ranges) if (cp >= lo && cp <= hi) return true;
  return false;
}

export const MIN_LEN = 8;
export const MAX_LEN = 80;

/** Code points that may appear in a Latin item (the ~95% share). */
export const LATIN_RANGES: readonly Range[] = [
  [0x0020, 0x007e],
  [0x00a1, 0x00ac],
  [0x00ae, 0x00ff],
  [0x0100, 0x017f], // Latin Extended-A (U+0130 is banned separately)
  [0x2013, 0x2014], // en and em dash
  [0x2018, 0x2019],
  [0x201c, 0x201d],
  [0x2026, 0x2026], // ellipsis
  [0x2192, 0x2192], // rightwards arrow
];

/**
 * Allowed repertoire: every code point in every item must be in one of these
 * ranges. All are assigned in Unicode <= 14.0 (emoji ranges exclude every
 * code point added in Emoji 15.0), so the "Unicode <= 15.0 / Emoji <= 15.0"
 * rule holds with margin. Sequences (ZWJ, flags, keycaps) come only from the
 * curated emoji list, whose entries are all Emoji <= 13.0.
 */
export const ALLOWED_RANGES: readonly Range[] = [
  ...LATIN_RANGES,
  [0x203a, 0x203a], // single right-pointing angle quotation mark (breadcrumbs)
  // Hebrew letters, maqaf, geresh, gershayim
  [0x05be, 0x05be],
  [0x05d0, 0x05ea],
  [0x05f3, 0x05f4],
  // Arabic: comma, semicolon, question mark, letters, harakat, digits
  [0x060c, 0x060c],
  [0x061b, 0x061b],
  [0x061f, 0x061f],
  [0x0621, 0x063a],
  [0x0640, 0x0652],
  [0x0660, 0x066d],
  [0x0670, 0x06d3],
  [0x06f0, 0x06f9],
  // Devanagari (whole block assigned by Unicode 7.0)
  [0x0900, 0x097f],
  // CJK symbols and punctuation (not U+3000 ideographic space), kana, Hangul
  [0x3001, 0x303f],
  [0x3041, 0x3096],
  [0x3099, 0x309f],
  [0x30a0, 0x30ff],
  [0x3131, 0x318e],
  [0x4e00, 0x9fff],
  [0xac00, 0xd7a3],
  [0xff01, 0xff5e], // fullwidth ASCII variants
  // Emoji building blocks
  [0x200d, 0x200d], // ZWJ
  [0xfe0f, 0xfe0f], // VS16
  [0x20e3, 0x20e3], // combining enclosing keycap
  [0x203c, 0x203c],
  [0x2049, 0x2049],
  [0x2122, 0x2122],
  [0x2139, 0x2139],
  [0x2194, 0x2199],
  [0x21a9, 0x21aa],
  [0x231a, 0x231b],
  [0x2328, 0x2328],
  [0x23cf, 0x23cf],
  [0x23e9, 0x23f3],
  [0x23f8, 0x23fa],
  [0x25aa, 0x25ab],
  [0x25b6, 0x25b6],
  [0x25c0, 0x25c0],
  [0x25fb, 0x25fe],
  [0x2600, 0x27bf],
  [0x2934, 0x2935],
  [0x2b05, 0x2b07],
  [0x2b1b, 0x2b1c],
  [0x2b50, 0x2b50],
  [0x2b55, 0x2b55],
  [0x1f1e6, 0x1f1ff], // regional indicators
  [0x1f300, 0x1f5ff],
  [0x1f600, 0x1f64f],
  [0x1f680, 0x1f6d7],
  [0x1f6e0, 0x1f6ec],
  [0x1f6f0, 0x1f6fc],
  [0x1f7e0, 0x1f7eb],
  [0x1f90c, 0x1f9ff],
  [0x1fa70, 0x1fa74],
  [0x1fa78, 0x1fa7c],
  [0x1fa80, 0x1fa86],
  [0x1fa90, 0x1faac],
  [0x1fab0, 0x1faba],
  [0x1fac0, 0x1fac5],
  [0x1fad0, 0x1fad9],
  [0x1fae0, 0x1fae7],
  [0x1faf0, 0x1faf6],
];

/** Whitespace other than U+0020 (all banned). */
const OTHER_WHITESPACE: readonly Range[] = [
  [0x0009, 0x000d],
  [0x0085, 0x0085],
  [0x00a0, 0x00a0],
  [0x1680, 0x1680],
  [0x2000, 0x200a],
  [0x2028, 0x2029],
  [0x202f, 0x202f],
  [0x205f, 0x205f],
  [0x3000, 0x3000],
  [0xfeff, 0xfeff],
];

const CONTROL: readonly Range[] = [
  [0x0000, 0x001f],
  [0x007f, 0x009f],
];

/** Bidi overrides, embeddings, isolates and marks. Natural RTL text only. */
const BIDI_CONTROLS: readonly Range[] = [
  [0x202a, 0x202e],
  [0x2066, 0x2069],
  [0x200e, 0x200f],
  [0x061c, 0x061c],
];

export const BANNED_CODE_POINTS: ReadonlyMap<number, string> = new Map([
  [0x0130, "U+0130 (toLowerCase changes UTF-16 length)"],
  [0x03a3, "U+03A3 (context-dependent final sigma)"],
]);

function hex(cp: number): string {
  return "U+" + cp.toString(16).toUpperCase().padStart(4, "0");
}

/**
 * Validate one item. Returns null when valid, else a reason string.
 * Rules: well-formed UTF-16; 8-80 code points; NFC; no control chars; no
 * bidi controls; no whitespace other than U+0020; no leading/trailing
 * space; no U+0130 / U+03A3; every code point within ALLOWED_RANGES.
 */
export function validateItem(s: string): string | null {
  if (typeof s !== "string") return "not a string";
  if (hasLoneSurrogate(s)) return "lone surrogate";
  const cps = codePoints(s);
  if (cps.length < MIN_LEN) return `too short (${cps.length} code points)`;
  if (cps.length > MAX_LEN) return `too long (${cps.length} code points)`;
  if (cps[0] === 0x20 || cps[cps.length - 1] === 0x20) return "leading or trailing space";
  for (let i = 0; i < cps.length; i++) {
    const cp = cps[i] as number;
    if (inRanges(cp, CONTROL)) return `control character ${hex(cp)}`;
    if (inRanges(cp, BIDI_CONTROLS)) return `bidi control ${hex(cp)}`;
    if (inRanges(cp, OTHER_WHITESPACE)) return `non-U+0020 whitespace ${hex(cp)}`;
    const banned = BANNED_CODE_POINTS.get(cp);
    if (banned !== undefined) return `banned code point ${banned}`;
    if (!inRanges(cp, ALLOWED_RANGES)) return `code point outside allowed repertoire ${hex(cp)}`;
    if (cp === 0x20 && cps[i + 1] === 0x20) return "double space";
    if (cp === 0x200d) {
      const prev = cps[i - 1];
      const next = cps[i + 1];
      if (prev === undefined || next === undefined || !isEmojiish(prev) || !isEmojiish(next)) {
        return "ZWJ outside an emoji sequence";
      }
    }
  }
  // Allowed exception to the runtime-normalization ban: assert NFC.
  if (s !== s.normalize("NFC")) return "not NFC";
  return null;
}

function isEmojiish(cp: number): boolean {
  return cp >= 0x1f000 || (cp >= 0x2000 && cp <= 0x2bff) || cp === 0xfe0f;
}

/** Latin item = every code point in LATIN_RANGES. Otherwise non-Latin. */
export function isLatinItem(s: string): boolean {
  for (const cp of codePoints(s)) if (!inRanges(cp, LATIN_RANGES)) return false;
  return true;
}

export function isAscii(s: string): boolean {
  for (let i = 0; i < s.length; i++) if (s.charCodeAt(i) > 0x7e) return false;
  return true;
}

export type Script = "han" | "kana" | "hangul" | "arabic" | "hebrew" | "devanagari" | "emoji";

/** Scripts present in a string (by explicit code point ranges). */
export function scriptsOf(s: string): Set<Script> {
  const out = new Set<Script>();
  for (const cp of codePoints(s)) {
    if (cp >= 0x4e00 && cp <= 0x9fff) out.add("han");
    else if ((cp >= 0x3041 && cp <= 0x30ff) || cp === 0x30fc) out.add("kana");
    else if ((cp >= 0xac00 && cp <= 0xd7a3) || (cp >= 0x3131 && cp <= 0x318e)) out.add("hangul");
    else if (cp >= 0x0600 && cp <= 0x06ff) out.add("arabic");
    else if (cp >= 0x0590 && cp <= 0x05ff) out.add("hebrew");
    else if (cp >= 0x0900 && cp <= 0x097f) out.add("devanagari");
    else if (cp >= 0x1f000 || (cp >= 0x2190 && cp <= 0x2bff && cp !== 0x2192) || cp === 0x20e3) out.add("emoji");
  }
  return out;
}

/** Has at least one ASCII letter (used to tag "mixed with Latin"). */
export function hasAsciiLetter(s: string): boolean {
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i);
    if ((c >= 0x41 && c <= 0x5a) || (c >= 0x61 && c <= 0x7a)) return true;
  }
  return false;
}

/**
 * Typeable on a pinned US layout without modifiers: ASCII lower-case
 * letters, digits and space. Non-empty.
 */
export function isTypeable(q: string): boolean {
  if (q.length === 0) return false;
  for (let i = 0; i < q.length; i++) {
    const c = q.charCodeAt(i);
    const ok = (c >= 0x61 && c <= 0x7a) || (c >= 0x30 && c <= 0x39) || c === 0x20;
    if (!ok) return false;
  }
  return true;
}

// --- Combining / cluster-extending code points (approximate grapheme rule) ---

/** Marks and joiners that extend the preceding cluster in our repertoire. */
export function isExtender(cp: number): boolean {
  return (
    (cp >= 0x0900 && cp <= 0x0903) ||
    (cp >= 0x093a && cp <= 0x094f && cp !== 0x093d) ||
    (cp >= 0x0951 && cp <= 0x0957) ||
    (cp >= 0x0962 && cp <= 0x0963) ||
    (cp >= 0x064b && cp <= 0x065f) ||
    cp === 0x0670 ||
    (cp >= 0x3099 && cp <= 0x309a) ||
    cp === 0x20e3 ||
    cp === 0xfe0f ||
    (cp >= 0x1f3fb && cp <= 0x1f3ff) ||
    cp === 0x200d
  );
}

function isRegionalIndicator(cp: number): boolean {
  return cp >= 0x1f1e6 && cp <= 0x1f1ff;
}

/**
 * Split into approximate grapheme clusters with a simple committed rule:
 * a cluster is a base code point followed by any extenders (isExtender);
 * after a ZWJ the next code point joins the cluster; regional indicators
 * pair up. Devanagari conjuncts (virama + consonant) are NOT merged, which
 * matches Unicode <= 15.0 extended grapheme clusters (GB9c arrived in 15.1).
 * This is an approximation of UAX #29, labelled as such in the manifest.
 */
export function approxClusters(s: string): number[][] {
  const cps = codePoints(s);
  const out: number[][] = [];
  let i = 0;
  while (i < cps.length) {
    const cluster: number[] = [cps[i] as number];
    const first = cps[i] as number;
    i++;
    if (isRegionalIndicator(first) && i < cps.length && isRegionalIndicator(cps[i] as number)) {
      cluster.push(cps[i] as number);
      i++;
    }
    while (i < cps.length) {
      const cp = cps[i] as number;
      if (cp === 0x200d && i + 1 < cps.length) {
        cluster.push(cp, cps[i + 1] as number);
        i += 2;
      } else if (isExtender(cp)) {
        cluster.push(cp);
        i++;
      } else break;
    }
    out.push(cluster);
  }
  return out;
}

export function approxGraphemeLength(s: string): number {
  return approxClusters(s).length;
}

// --- NFD decomposition without String.prototype.normalize -----------------

// Latin-1 Supplement and Latin Extended-A canonical decompositions that can
// occur in our Latin repertoire, plus kana voiced marks. Verified against
// String.prototype.normalize("NFD") in test/unicode.test.ts.
const NFD_TABLE: ReadonlyMap<number, readonly number[]> = (() => {
  const m = new Map<number, readonly number[]>();
  const add = (base: string, mark: number, composed: string): void => {
    for (let i = 0; i < composed.length; i++) {
      const c = composed.charCodeAt(i);
      if (c === 0x2e) continue; // "." = no such letter
      m.set(c, [base.charCodeAt(i), mark]);
    }
  };
  // Pairs of (bases, composed) aligned by index; "." marks gaps.
  add("AEIOUaeiou", 0x0300, "ÀÈÌÒÙàèìòù"); // grave
  add("AEIOUYaeiouy", 0x0301, "ÁÉÍÓÚÝáéíóúý"); // acute
  add("AEIOUaeiou", 0x0302, "ÂÊÎÔÛâêîôû"); // circumflex
  add("ANOano", 0x0303, "ÃÑÕãñõ"); // tilde
  add("AEIOUaeiouy", 0x0308, "ÄËÏÖÜäëïöüÿ"); // diaeresis
  add("Aa", 0x030a, "Åå"); // ring
  add("Cc", 0x0327, "Çç"); // cedilla
  add("AaEeIiOoUu", 0x0304, "ĀāĒēĪīŌōŪū"); // macron
  add("AaGgUuEeIiOo", 0x0306, "ĂăĞğŬŭĔĕĬĭŎŏ"); // breve
  add("AaEeIiUu", 0x0328, "ĄąĘęĮįŲų"); // ogonek
  add("CcNnSsZzRrLlGg", 0x0301, "ĆćŃńŚśŹźŔŕĹĺ.."); // acute (Latin Ext-A)
  add("CcDdEeNnRrSsTtZzLl", 0x030c, "ČčĎďĚěŇňŘřŠšŤťŽžĽľ"); // caron
  add("CcEeGgZz", 0x0307, "ĊċĖėĠġŻż"); // dot above (no U+0130)
  add("UuOo", 0x030b, "ŰűŐő"); // double acute
  add("SsTtGgKkLlNnRr", 0x0327, "ŞşŢţĢģĶķĻļŅņŖŗ"); // cedilla
  add("Uu", 0x030a, "Ůů"); // ring
  add("CcGgHhJjSsWwYy", 0x0302, "ĈĉĜĝĤĥĴĵŜŝŴŵŶŷ"); // circumflex
  add("IiUu", 0x0303, "ĨĩŨũ"); // tilde
  add("Y", 0x0308, "Ÿ");
  // Kana: voiced (U+3099) and semi-voiced (U+309A) sound marks.
  const voiced = "かきくけこさしすせそたちつてとはひふへほカキクケコサシスセソタチツテトハヒフヘホゝヽ";
  for (let i = 0; i < voiced.length; i++) {
    const b = voiced.charCodeAt(i);
    m.set(b + 1, [b, 0x3099]);
  }
  m.set(0x3094, [0x3046, 0x3099]); // ゔ
  m.set(0x30f4, [0x30a6, 0x3099]); // ヴ
  m.set(0x30f7, [0x30ef, 0x3099]); // ヷ
  m.set(0x30f8, [0x30f0, 0x3099]); // ヸ
  m.set(0x30f9, [0x30f1, 0x3099]); // ヹ
  m.set(0x30fa, [0x30f2, 0x3099]); // ヺ
  // Arabic hamza/madda forms.
  m.set(0x0622, [0x0627, 0x0653]);
  m.set(0x0623, [0x0627, 0x0654]);
  m.set(0x0624, [0x0648, 0x0654]);
  m.set(0x0625, [0x0627, 0x0655]);
  m.set(0x0626, [0x064a, 0x0654]);
  m.set(0x06c0, [0x06d5, 0x0654]);
  m.set(0x06c2, [0x06c1, 0x0654]);
  m.set(0x06d3, [0x06d2, 0x0654]);
  // Devanagari nukta letters.
  m.set(0x0929, [0x0928, 0x093c]);
  m.set(0x0931, [0x0930, 0x093c]);
  m.set(0x0934, [0x0933, 0x093c]);
  const nuktaBases = [0x0915, 0x0916, 0x0917, 0x091c, 0x0921, 0x0922, 0x092b, 0x092f];
  for (let i = 0; i < nuktaBases.length; i++) m.set(0x0958 + i, [nuktaBases[i] as number, 0x093c]);
  const semi = "はひふへほハヒフヘホ";
  for (let i = 0; i < semi.length; i++) {
    const b = semi.charCodeAt(i);
    m.set(b + 2, [b, 0x309a]);
  }
  return m;
})();

const S_BASE = 0xac00;
const L_BASE = 0x1100;
const V_BASE = 0x1161;
const T_BASE = 0x11a7;
const T_COUNT = 28;
const N_COUNT = 588;
const S_COUNT = 11172;

/** Does this code point have a canonical decomposition we know about? */
export function hasNfdDecomposition(cp: number): boolean {
  return (cp >= S_BASE && cp < S_BASE + S_COUNT) || NFD_TABLE.has(cp);
}

/**
 * NFD for the code points in our repertoire: algorithmic Hangul
 * decomposition plus NFD_TABLE. Other code points pass through (in our
 * repertoire they are already NFD-stable). Checked against
 * String.prototype.normalize in tests.
 */
export function toNfd(s: string): string {
  const out: number[] = [];
  for (const cp of codePoints(s)) {
    if (cp >= S_BASE && cp < S_BASE + S_COUNT) {
      const si = cp - S_BASE;
      out.push(L_BASE + Math.floor(si / N_COUNT), V_BASE + Math.floor((si % N_COUNT) / T_COUNT));
      const t = si % T_COUNT;
      if (t !== 0) out.push(T_BASE + t);
      continue;
    }
    const d = NFD_TABLE.get(cp);
    if (d !== undefined) out.push(...d);
    else out.push(cp);
  }
  return fromCodePoints(out);
}
