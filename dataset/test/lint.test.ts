// Lightweight determinism lint: grep src/ for banned APIs (see README).
import { test } from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { PKG_DIR } from "../src/data.ts";

const SRC = join(PKG_DIR, "src");
// normalize() is allowed only where it validates (item validator, data loader).
const NORMALIZE_ALLOWED = new Set(["unicode.ts", "data.ts"]);

const BANNED: [RegExp, string][] = [
  [/Math\.random/, "Math.random"],
  [/localeCompare/, "localeCompare"],
  [/\bIntl\./, "Intl.*"],
  [/\.sort\(\s*\)/, ".sort() without comparator"],
  [/\.toSorted\(\s*\)/, ".toSorted() without comparator"],
  [/toLocale(Lower|Upper)Case|toLocaleString/, "locale-dependent case/format"],
  [/Date\.now|new Date\(/, "wall-clock time"],
  [/\r\n/, "CRLF literal"],
];

function stripComments(src: string): string {
  return src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");
}

test("src/ uses no banned APIs", () => {
  const problems: string[] = [];
  for (const f of readdirSync(SRC).filter((f) => f.endsWith(".ts"))) {
    const code = stripComments(readFileSync(join(SRC, f), "utf8"));
    for (const [re, what] of BANNED) if (re.test(code)) problems.push(`${f}: ${what}`);
    if (!NORMALIZE_ALLOWED.has(f) && /\.normalize\(/.test(code)) problems.push(`${f}: normalize() outside validators`);
  }
  assert.deepEqual(problems, []);
});
