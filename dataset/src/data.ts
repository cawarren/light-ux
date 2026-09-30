// Loads the committed word lists and phrase lists, and checks them.

import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

export const PKG_DIR = join(dirname(fileURLToPath(import.meta.url)), "..");
const DATA_DIR = join(PKG_DIR, "data");

export const EFF_SHA256 = "addd35536511597a02fa0a9ff1e5284677b8883b83e986e43f15a3db996b903e";

export const NON_LATIN_LOCALES = ["ja", "zh-Hans", "zh-Hant", "ko", "ar", "he", "hi"] as const;
export type NonLatinLocale = (typeof NON_LATIN_LOCALES)[number];
export type NonLatinCategory = NonLatinLocale | "emoji";

export interface Phrase {
  text: string;
  gloss: string;
}

export type EmojiKind = "single" | "zwj" | "skin" | "keycap" | "flag";
export interface EmojiEntry {
  emoji: string;
  label: string;
  kind: EmojiKind;
}

export interface DevVocab {
  verbs: string[];
  nouns: string[];
  adjectives: string[];
  adjuncts: string[];
  categories: string[];
  pathDirs: string[];
  fileBases: string[];
  rootFiles: string[];
  extensions: string[];
  branchPrefixes: string[];
  people: string[];
  surnames: string[];
  settingNamespaces: string[];
  settingKeys: string[];
  symbolPrefixes: string[];
  symbolNouns: string[];
  symbolSuffixes: string[];
  domains: string[];
  tlds: string[];
  suffixWords: string[];
  ticketPrefixes: string[];
}

export interface Corpus {
  /** The full EFF list, in file order (index = line). */
  eff: string[];
  /** Words from data/words/denylist.txt, never used in items. */
  denylist: ReadonlySet<string>;
  dev: DevVocab;
  phrases: Record<NonLatinLocale, Phrase[]>;
  latinExtended: Phrase[];
  emoji: EmojiEntry[];
}

function readText(rel: string): string {
  const buf = readFileSync(join(DATA_DIR, rel));
  const s = buf.toString("utf8");
  if (s.includes("\r")) throw new Error(`${rel}: CR found; data files must use LF`);
  return s;
}

/** Non-comment, non-empty lines. Comments start with "# " (so "#️⃣" is data). */
function dataLines(s: string): string[] {
  return s.split("\n").filter((l) => l.length > 0 && !l.startsWith("# "));
}

function loadPhrases(rel: string): Phrase[] {
  const s = readText(rel);
  if (s !== s.normalize("NFC")) throw new Error(`${rel}: not NFC`);
  return dataLines(s).map((l, i) => {
    const parts = l.split("\t");
    if (parts.length !== 2 || !parts[0] || !parts[1]) throw new Error(`${rel}:${i}: expected 2 TAB fields`);
    return { text: parts[0], gloss: parts[1] };
  });
}

let cached: Corpus | undefined;

export function loadCorpus(): Corpus {
  if (cached) return cached;
  const effBuf = readFileSync(join(DATA_DIR, "words", "eff_large_wordlist.txt"));
  const effHash = createHash("sha256").update(effBuf).digest("hex");
  if (effHash !== EFF_SHA256) throw new Error(`EFF word list hash mismatch: ${effHash}`);
  const eff = dataLines(effBuf.toString("utf8")).map((l) => {
    const w = l.split("\t")[1];
    if (!w) throw new Error(`bad EFF line: ${l}`);
    return w;
  });
  const denylist = new Set(dataLines(readText("words/denylist.txt")).map((l) => l.trim()));
  for (const w of denylist) if (!eff.includes(w)) throw new Error(`denylist word not in EFF list: ${w}`);
  const dev = JSON.parse(readText("words/dev-vocab.json")) as DevVocab;
  const phrases = {} as Record<NonLatinLocale, Phrase[]>;
  for (const loc of NON_LATIN_LOCALES) phrases[loc] = loadPhrases(`phrases/${loc}.tsv`);
  const latinExtended = loadPhrases("phrases/latin-extended.tsv");
  const emojiText = readText("phrases/emoji.tsv");
  if (emojiText !== emojiText.normalize("NFC")) throw new Error("emoji.tsv: not NFC");
  const emoji = dataLines(emojiText).map((l, i) => {
    const [e, label, kind] = l.split("\t");
    if (!e || !label || !kind) throw new Error(`emoji.tsv:${i}: expected 3 TAB fields`);
    if (!["single", "zwj", "skin", "keycap", "flag"].includes(kind)) throw new Error(`emoji.tsv:${i}: bad kind`);
    return { emoji: e, label, kind: kind as EmojiKind };
  });
  cached = { eff, denylist, dev, phrases, latinExtended, emoji };
  return cached;
}
