// Item generator: one deterministic stream of items; any size is a prefix.

import { STREAM, stream, type Xoshiro128ss } from "./prng.ts";
import {
  loadCorpus,
  type Corpus,
  type NonLatinCategory,
  type NonLatinLocale,
  type Phrase,
  type EmojiEntry,
} from "./data.ts";
import { cpLength, isLatinItem, validateItem, MIN_LEN, MAX_LEN } from "./unicode.ts";

export type Variant = "standard" | "shifted";

/** Exactly one non-Latin item per block of this many items (5.0%). */
export const NON_LATIN_BLOCK = 20;

export type LatinTemplate =
  | "command"
  | "categoryCommand"
  | "path"
  | "branch"
  | "settingKey"
  | "symbol"
  | "url"
  | "email"
  | "mixedSeparators"
  | "docTitle"
  | "latinExtended"
  | "issue";

const LATIN_WEIGHTS: Record<Variant, readonly (readonly [LatinTemplate, number])[]> = {
  standard: [
    ["command", 26],
    ["categoryCommand", 12],
    ["path", 12],
    ["branch", 7],
    ["settingKey", 7],
    ["symbol", 9],
    ["url", 4],
    ["email", 3],
    ["mixedSeparators", 7],
    ["docTitle", 8],
    ["latinExtended", 3],
    ["issue", 2],
  ],
  shifted: [
    ["command", 10],
    ["categoryCommand", 5],
    ["path", 20],
    ["branch", 12],
    ["settingKey", 10],
    ["symbol", 16],
    ["url", 8],
    ["email", 6],
    ["mixedSeparators", 5],
    ["docTitle", 4],
    ["latinExtended", 2],
    ["issue", 2],
  ],
};

const NON_LATIN_WEIGHTS: Record<Variant, readonly (readonly [NonLatinCategory, number])[]> = {
  standard: [
    ["ja", 13],
    ["zh-Hans", 11],
    ["zh-Hant", 7],
    ["ko", 9],
    ["ar", 14],
    ["he", 10],
    ["hi", 12],
    ["emoji", 24],
  ],
  shifted: [
    ["ja", 8],
    ["zh-Hans", 8],
    ["zh-Hant", 8],
    ["ko", 8],
    ["ar", 18],
    ["he", 14],
    ["hi", 16],
    ["emoji", 20],
  ],
};

export interface ItemTag {
  kind: LatinTemplate | NonLatinCategory;
  nonLatin: boolean;
}

export interface GeneratedItems {
  items: string[];
  tags: ItemTag[];
}

// --- small string helpers (ASCII-only case changes; no locale APIs) -------

function upperFirst(w: string): string {
  if (w.length === 0) return w;
  const c = w.charCodeAt(0);
  if (c >= 0x61 && c <= 0x7a) return String.fromCharCode(c - 32) + w.slice(1);
  return w;
}

function titleCase(s: string): string {
  return s.split(" ").map(upperFirst).join(" ");
}

function camel(parts: string[]): string {
  const words = parts.join(" ").split(/[ \-]/).filter((w) => w.length > 0);
  return words.map((w, i) => (i === 0 ? w.toLowerCase() : upperFirst(w.toLowerCase()))).join("");
}

function pascal(parts: string[]): string {
  return upperFirst(camel(parts));
}

function slug(parts: string[], sep: string): string {
  return parts
    .join(" ")
    .toLowerCase()
    .split(/[ \-_]/)
    .filter((w) => w.length > 0)
    .join(sep);
}

function renderDigits(zero: number, n: number): string {
  const s = String(n);
  if (zero === 0x30) return s;
  let out = "";
  for (let i = 0; i < s.length; i++) out += String.fromCharCode(zero + s.charCodeAt(i) - 0x30);
  return out;
}

// --- generator context ----------------------------------------------------

class Ctx {
  readonly c: Corpus;
  readonly eff: string[];
  readonly variant: Variant;
  constructor(variant: Variant) {
    this.c = loadCorpus();
    this.variant = variant;
    // Standard uses the even-indexed half of the EFF list, shifted the odd
    // half, so the shifted set has a disjoint general vocabulary.
    const parity = variant === "standard" ? 0 : 1;
    // The split uses original line indices, so the denylist filter does not
    // move words between halves.
    this.eff = this.c.eff.filter((w, i) => i % 2 === parity && !this.c.denylist.has(w));
  }

  word(r: Xoshiro128ss): string {
    return r.pick(this.eff);
  }

  caseStyle(r: Xoshiro128ss, s: string): string {
    const k = r.below(20);
    if (k < 11) return titleCase(s);
    if (k < 18) return upperFirst(s);
    return s;
  }

  commandCore(r: Xoshiro128ss): string {
    const d = this.c.dev;
    const parts = [r.pick(d.verbs)];
    if (r.chance(3, 10)) parts.push(r.pick(d.adjectives));
    if (r.chance(15, 100)) parts.push(this.word(r));
    parts.push(r.pick(d.nouns));
    if (r.chance(2, 10)) parts.push(r.pick(d.adjuncts));
    return parts.join(" ");
  }

  command(r: Xoshiro128ss): string {
    return this.caseStyle(r, this.commandCore(r));
  }

  docTitle(r: Xoshiro128ss): string {
    const n = r.range(2, 4);
    const words: string[] = [];
    for (let i = 0; i < n; i++) words.push(this.word(r));
    let s = r.chance(7, 10) ? titleCase(words.join(" ")) : upperFirst(words.join(" "));
    const k = r.below(10);
    if (k < 3) s += " " + r.pick(this.c.dev.suffixWords);
    else if (k < 4) s += " " + String(r.range(2019, 2027));
    return s;
  }

  pathSegment(r: Xoshiro128ss): string {
    if (r.chance(6, 10)) return r.pick(this.c.dev.pathDirs);
    const sep = r.pick(["-", "_", ""]);
    return sep === "" ? this.word(r).replace(/-/g, "") : slug([this.word(r), this.word(r)], sep);
  }

  path(r: Xoshiro128ss): string {
    const d = this.c.dev;
    const depth = r.range(1, 4);
    const segs: string[] = [];
    for (let i = 0; i < depth; i++) segs.push(this.pathSegment(r));
    let file: string;
    const k = r.below(10);
    if (k < 4) file = r.pick(d.fileBases) + "." + r.pick(d.extensions);
    else if (k < 5) file = r.pick(d.rootFiles);
    else if (k < 8) file = slug([this.word(r), r.pick(d.symbolNouns)], "-") + "." + r.pick(d.extensions);
    else file = pascal([this.word(r), r.pick(d.symbolSuffixes)]) + "." + r.pick(["tsx", "jsx", "vue", "svelte", "swift", "kt"]);
    const prefix = r.pick(["", "", "", "./", "~/", "/"]);
    return prefix + segs.join("/") + "/" + file;
  }

  branch(r: Xoshiro128ss): string {
    const d = this.c.dev;
    const n = r.range(2, 4);
    const parts: string[] = [];
    for (let i = 0; i < n; i++) parts.push(r.chance(1, 2) ? r.pick(d.nouns) : this.word(r));
    const body = slug(parts, r.chance(9, 10) ? "-" : "_");
    const prefix = r.pick(d.branchPrefixes);
    const k = r.below(10);
    if (k < 5) return `${prefix}/${body}`;
    if (k < 7) return `${r.pick(d.people)}/${prefix}-${body}`;
    if (k < 9) return `${prefix}/${r.pick(d.ticketPrefixes).toLowerCase()}-${r.range(1, 9999)}-${body}`;
    return `${r.pick(d.people)}/${body}`;
  }

  settingKey(r: Xoshiro128ss): string {
    const d = this.c.dev;
    const ns = r.pick(d.settingNamespaces);
    const k = r.below(10);
    if (k < 4) return `${ns}.${r.pick(d.settingKeys)}`;
    if (k < 7) return `${ns}.${camel([r.pick(d.adjectives), r.pick(d.symbolNouns)])}`;
    if (k < 9) return `${ns}.${camel([this.word(r)])}.${camel([r.pick(d.symbolNouns), r.pick(d.symbolSuffixes)])}`;
    return `${ns}.${camel([this.word(r), r.pick(d.symbolNouns)])}`;
  }

  symbol(r: Xoshiro128ss): string {
    const d = this.c.dev;
    const k = r.below(20);
    const noun = r.pick(d.symbolNouns);
    if (k < 7) {
      const s = camel([r.pick(d.symbolPrefixes), r.chance(1, 2) ? this.word(r) : r.pick(d.adjectives), noun]);
      return r.chance(1, 4) ? s + "()" : s;
    }
    if (k < 11) return pascal([this.word(r), noun, r.pick(d.symbolSuffixes)]);
    if (k < 14) return slug([r.pick(d.symbolPrefixes), this.word(r), noun], "_");
    if (k < 16) return slug([r.pick(["max", "min", "default", "api", "env", "is"]), this.word(r), noun], "_").toUpperCase();
    if (k < 18) return `${pascal([this.word(r), r.pick(d.symbolSuffixes)])}.${camel([r.pick(d.symbolPrefixes), noun])}()`;
    return `${slug([this.word(r)], "_")}::${pascal([noun, r.pick(d.symbolSuffixes)])}`;
  }

  url(r: Xoshiro128ss): string {
    const d = this.c.dev;
    const k = r.below(10);
    if (k < 3) return `https://github.com/${r.pick(d.people)}/${slug([this.word(r), r.pick(d.symbolNouns)], "-")}/pull/${r.range(1, 9999)}`;
    if (k < 5) return `https://${r.pick(d.domains)}/${this.pathSegment(r)}/${slug([this.word(r)], "-")}`;
    if (k < 7) return `https://${slug([this.word(r)], "")}.${r.pick(d.tlds)}/${slug([this.word(r), this.word(r)], "-")}`;
    if (k < 9) return `http://localhost:${r.pick(["3000", "5173", "8080", "4200"])}/${this.pathSegment(r)}?tab=${slug([r.pick(d.symbolNouns)], "")}#${slug([this.word(r)], "-")}`;
    return `${r.pick(["www", "docs", "app", "api"])}.${slug([this.word(r)], "")}.${r.pick(d.tlds)}`;
  }

  email(r: Xoshiro128ss): string {
    const d = this.c.dev;
    const k = r.below(10);
    const host = r.chance(1, 2) ? r.pick(["example.com", "example.org", "acme.dev", "mail.example.net"]) : `${slug([this.word(r)], "")}.${r.pick(d.tlds)}`;
    if (k < 5) return `${r.pick(d.people)}.${r.pick(d.surnames)}@${host}`;
    if (k < 8) return `${r.pick(d.people)}+${slug([this.word(r)], "")}@${host}`;
    return `${slug([this.word(r), r.pick(["team", "support", "alerts", "billing", "noreply"])], "-")}@${host}`;
  }

  mixedSeparators(r: Xoshiro128ss): string {
    const d = this.c.dev;
    const k = r.below(12);
    const t = () => titleCase(this.word(r));
    switch (k) {
      case 0:
        return `${titleCase(r.pick(d.verbs))} & ${titleCase(r.pick(d.verbs))} ${titleCase(r.pick(d.nouns))}`;
      case 1:
        return `[${r.pick(["WIP", "Draft", "RFC", "Beta", "Deprecated"])}] ${this.command(r)}`;
      case 2:
        return `#${slug([this.word(r), r.pick(d.nouns)], "-")}`;
      case 3:
        return `@${r.pick(d.people)}.${r.pick(d.surnames)} ${r.pick(["mentioned you", "assigned you", "requested review", "replied"])}`;
      case 4:
        return `${this.command(r)} (${r.pick(d.adjectives)})`;
      case 5:
        return `${t()} + ${t()} ${r.pick(["Bundle", "Pack", "Sync", "Mode"])}`;
      case 6:
        return `{${camel([this.word(r), r.pick(d.symbolNouns)])}} ${r.pick(["placeholder", "template variable", "binding"])}`;
      case 7:
        return `${r.pick(d.categories)} — ${this.docTitle(r)}`;
      case 8:
        return `${t()} → ${t()} ${r.pick(["Migration", "Redirect", "Mapping"])}`;
      case 9:
        return `Q${r.range(1, 4)} ${r.range(2019, 2027)} ${t()} ${r.pick(["Roadmap", "Review", "OKRs", "Planning"])}`;
      case 10:
        return `${this.command(r)}…`;
      default:
        return `${t()}.${r.pick(d.extensions)} (${r.range(1, 99)} KB)`;
    }
  }

  latinExtended(r: Xoshiro128ss): string {
    const p = r.pick(this.c.latinExtended).text;
    const k = r.below(5);
    if (k === 0) return p;
    if (k === 1) return `${p} (${this.word(r)})`;
    if (k === 2) return `${r.pick(this.c.dev.categories)}: ${p}`;
    if (k === 3) return `${p} – ${this.docTitle(r)}`;
    return `${this.docTitle(r)} ${p}`;
  }

  issue(r: Xoshiro128ss): string {
    const d = this.c.dev;
    const k = r.below(3);
    const sentence = upperFirst(this.commandCore(r));
    if (k === 0) return `#${r.range(1, 9999)} ${sentence}`;
    if (k === 1) return `${r.pick(d.ticketPrefixes)}-${r.range(1, 9999)}: ${sentence}`;
    return `Fix ${r.pick(d.nouns)} in ${this.path(r)}`;
  }

  latin(r: Xoshiro128ss, t: LatinTemplate): string {
    switch (t) {
      case "command":
        return this.command(r);
      case "categoryCommand":
        return `${r.pick(this.c.dev.categories)}: ${this.command(r)}`;
      case "path":
        return this.path(r);
      case "branch":
        return this.branch(r);
      case "settingKey":
        return this.settingKey(r);
      case "symbol":
        return this.symbol(r);
      case "url":
        return this.url(r);
      case "email":
        return this.email(r);
      case "mixedSeparators":
        return this.mixedSeparators(r);
      case "docTitle":
        return this.docTitle(r);
      case "latinExtended":
        return this.latinExtended(r);
      case "issue":
        return this.issue(r);
    }
  }

  // --- non-Latin ---

  /** Pick a digit system for a locale: ASCII, Arabic-Indic (ar) or Devanagari (hi). */
  digitZero(r: Xoshiro128ss, loc: NonLatinLocale): number {
    if (loc === "ar" && r.chance(1, 2)) return 0x0660;
    if (loc === "hi" && r.chance(1, 3)) return 0x0966;
    return 0x30;
  }

  digits(r: Xoshiro128ss, loc: NonLatinLocale, n: number): string {
    return renderDigits(this.digitZero(r, loc), n);
  }

  fill(r: Xoshiro128ss, loc: NonLatinLocale, p: Phrase): string {
    if (!p.text.includes("{")) return p.text;
    const m = r.range(2, 250);
    const n = r.range(1, m);
    const zero = this.digitZero(r, loc); // n and m share one digit system
    return p.text.replace("{n}", renderDigits(zero, n)).replace("{m}", renderDigits(zero, m));
  }

  nonLatinPhrase(r: Xoshiro128ss, loc: NonLatinLocale): string {
    const P = this.c.phrases[loc];
    const cjk = loc === "ja" || loc === "zh-Hans" || loc === "zh-Hant";
    const k = r.below(14);
    const one = () => this.fill(r, loc, r.pick(P));
    if (k < 2) return one();
    if (k < 6) {
      const sep = cjk ? r.pick(["：", " › ", " / ", "・"]) : r.pick([" › ", " - ", " / ", ": "]);
      const n = r.range(2, 3);
      const parts: string[] = [];
      while (parts.length < n) {
        const p = one();
        if (!parts.includes(p)) parts.push(p);
      }
      return parts.join(sep);
    }
    if (k < 9) {
      const p = r.pick(P);
      const text = this.fill(r, loc, p);
      const gloss = p.gloss.replace("{n}", "N").replace("{m}", "M");
      return r.chance(1, 2) ? `${text} (${gloss})` : `${gloss} — ${text}`;
    }
    if (k < 11) return `${r.pick(this.c.dev.categories)}: ${one()}`;
    if (k < 13) {
      const target = r.chance(1, 2) ? this.path(r) : this.branch(r);
      return `${one()} ${target}`;
    }
    return `${one()} (${this.digits(r, loc, r.range(1, 999))})`;
  }

  emojiItem(r: Xoshiro128ss): string {
    const E = this.c.emoji;
    const e: EmojiEntry = r.pick(E);
    const k = r.below(10);
    if (e.kind === "flag" && k < 6) {
      return r.chance(1, 2)
        ? `${e.emoji} Switch Language to ${e.label}`
        : `${e.emoji} ${e.label} ${r.pick(["Keyboard Layout", "Spell Check", "Locale Settings", "Translation"])}`;
    }
    if (e.kind === "keycap" && k < 6) return `${e.emoji} ${e.label}: ${this.command(r)}`;
    if (k < 4) return `${e.emoji} ${this.command(r)}`;
    if (k < 5) return `${this.command(r)} ${e.emoji}`;
    if (k < 7) return `${e.emoji} ${e.label}: ${r.chance(1, 2) ? this.docTitle(r) : this.command(r)}`;
    if (k < 8) {
      const loc = r.pick(["ja", "zh-Hans", "ko", "ar", "he", "hi"] as const);
      return `${e.emoji} ${this.fill(r, loc, r.pick(this.c.phrases[loc]))}`;
    }
    if (k < 9) return `${e.emoji}${r.pick(E).emoji} ${this.command(r)}`;
    return `${e.label} ${e.emoji} ${this.docTitle(r)}`;
  }

  nonLatin(r: Xoshiro128ss, cat: NonLatinCategory): string {
    return cat === "emoji" ? this.emojiItem(r) : this.nonLatinPhrase(r, cat);
  }
}

export interface GenOptions {
  seed: bigint;
  size: number;
  variant?: Variant;
}

const MAX_ATTEMPTS = 10000;

export function generateItems(opts: GenOptions): GeneratedItems {
  const variant = opts.variant ?? "standard";
  const ctx = new Ctx(variant);
  const mix = stream(opts.seed, STREAM.mix);
  const lat = stream(opts.seed, STREAM.latin);
  const nl = stream(opts.seed, STREAM.nonLatin);
  const latinWeights = LATIN_WEIGHTS[variant];
  const nlWeights = NON_LATIN_WEIGHTS[variant];

  const items: string[] = [];
  const tags: ItemTag[] = [];
  const seenExact = new Set<string>();
  const seenLower = new Set<string>();
  let nlPos = -1;

  for (let i = 0; i < opts.size; i++) {
    if (i % NON_LATIN_BLOCK === 0) nlPos = mix.below(NON_LATIN_BLOCK);
    const isNL = i % NON_LATIN_BLOCK === nlPos;
    let item: string | undefined;
    let kind: ItemTag["kind"] | undefined;
    for (let attempt = 0; attempt < MAX_ATTEMPTS; attempt++) {
      let cand: string;
      if (isNL) {
        const cat = nl.weighted(nlWeights);
        cand = ctx.nonLatin(nl, cat);
        kind = cat;
      } else {
        const t = lat.weighted(latinWeights);
        cand = ctx.latin(lat, t);
        kind = t;
      }
      const len = cpLength(cand);
      if (len < MIN_LEN || len > MAX_LEN) continue; // rejection: same slot, same stream
      const bad = validateItem(cand);
      if (bad !== null) throw new Error(`generator produced an invalid item (${bad}): ${JSON.stringify(cand)}`);
      if (isLatinItem(cand) === isNL) {
        throw new Error(`script class mismatch at ${i}: ${JSON.stringify(cand)}`);
      }
      const lower = cand.toLowerCase();
      if (seenExact.has(cand) || seenLower.has(lower)) continue;
      seenExact.add(cand);
      seenLower.add(lower);
      item = cand;
      break;
    }
    if (item === undefined || kind === undefined) throw new Error(`could not generate a unique item at index ${i}`);
    items.push(item);
    tags.push({ kind, nonLatin: isNL });
  }
  return { items, tags };
}
