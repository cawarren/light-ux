# 05 · Software foundations (dataset, reference ranking, R1 skeleton, cheap parity pieces)

Scope: the parts Phase 0 and Phase 1 need that are **pure software** and can be built while the rig hardware is on order: the dataset and query generators, the reference ranking with its conformance suite, the R1 skeleton, and the parity checks that cost little today. Section 6 has the task breakdown.

Sources were read live on 2026-09-29:

- cmdk `main` at `dd2250e` (2025-10-28). The files are `cmdk/src/command-score.ts` and `cmdk/src/index.tsx`, at https://github.com/pacocoursey/cmdk.
- The published npm tarball `cmdk@1.1.1`, which is still `latest` and was published 2025-03-14. Its minified `dist` scorer is token-for-token the same algorithm as `command-score.ts` on `main`. The file has not changed since the `keywords` commit `eb404c0` (2024-01).
- shadcn `apps/v4/registry/new-york-v4/ui/command.tsx` on `main`.

I ran three small spikes. They are in `spikes/`:

- `pathdep.mjs` runs real cmdk 1.1.1 with React 19 under jsdom.
- `cmdkscore/` is a Rust port of the scorer.
- `bench.mjs` measures scorer cost and how many results tie.

Their results are quoted where they matter. Items marked **[decide]** need a human.

---

## 0. Headline findings (read these first)

1. **cmdk's displayed order depends on typing history, so "cmdk's ranking" is not a function of the query alone.**
   - How it happens: `sort()` (index.tsx L368-426) takes the *current DOM order* (`querySelectorAll`) and runs a stable `Array.prototype.sort` by score, highest first. It then moves nodes with `appendChild`. Tied items therefore keep whatever order the *previous* query left them in. When items are remounted after a backspace, React inserts them relative to fiber siblings that cmdk has already moved.
   - Spike (1,500 items, query `coex`, 78 matches, only 26 distinct scores):
     - Pasting the whole query into a fresh mount gives exactly (score descending, dataset index ascending).
     - Typing the query one character at a time differs from that order **at rank 1**.
     - Typing it after an earlier query and a clear also differs.
     - In every case the differences stay inside tie groups.
   - Ties are the normal case, not an edge case. In `bench.mjs` (50k command-like items):
     - `a` gives 39,965 matches, all with **one** score.
     - `op` gives 27k matches but only 34 distinct scores, and the largest tie group has 10.8k items.
   - **Recommendation:**
     - Define the normative order as **score descending, then dataset id ascending**.
     - Accept R1 as conforming only up to tie order.
     - Take visual-diff snapshots only in states where R1 is known to match the canonical order: a fresh mount with the whole query delivered in one input event. [decide]
2. **Floating-point: `Math.pow` differs between platforms and changes scores.**
   - cmdk multiplies by `Math.pow(0.999, n)`.
   - V8's `Math.pow` differs from glibc `pow` for 192 of the exponents 0..2000. Rust's `f64::powf` on Linux differs from V8 for 19 of the exponents 0..200, including n=16 and n=32. `powi` differs for 193 of 201.
   - In the Rust spike, **`powf` gave 596 score mismatches in 500,000 (item, query) pairs. A table of V8-computed `pow` constants (stored as bit patterns) gave 0 mismatches**, which is bit-exact.
   - Because ties decide the order, a one-ULP difference changes rankings.
   - Fix: the reference implementation (TS and Rust) uses a frozen `POW_0999[n]` table generated once from pinned Chrome.
3. **Three parts of the parity checklist fail on stock R1:**
   - cmdk has **no PageUp/PageDown handling** and **no Escape handling**. Escape only works through Radix Dialog in `CommandDialog`.
   - cmdk *intercepts* `Home`/`End`, including Shift+Home and Shift+End, at the root. They move the list selection, so they no longer move or select in the text input.

   The keyboard and text-editing checks must be defined as "matches R1's behaviour" or R1 must be amended. [decide]
4. **Controlled `CommandInput` (`value` plus `onValueChange`) updates the list in a passive `useEffect`** (index.tsx L795-799).
   - A marker keyed on the parent's query state would flip one commit *before* the list updates. That is a concrete, typical-code example of an early marker.
   - Use an uncontrolled input and derive the marker from cmdk's own store (§3.3).
5. **The match set is not "query is a subsequence of item".** The transposition and duplicate-letter branch skips a query character:
   - `score("op","opp") = 0.1`.
   - `score("ab","ba") = 0.1`.
   - `score("request","reqeust") = 0.1`.

   An R2 or R3 prefilter index built on subsequences or n-grams would drop valid results. The golden suite must include these cases.
6. **Cost at 50k items:** scoring alone takes 55-285 ms per keystroke in Node (bench, 50k items). That is before any DOM work.
   - R1 will probably be slower than the 100 ms key cadence.
   - Key events will queue, which the rig or orchestrator team must handle (see `03-orchestrator.md` §A.seq_key).
   - This supports the open question about a 10k default.

---

## 1. Dataset generator

### 1.1 Recommendation

- **Language:** TypeScript on Node, pinned in `.nvmrc` and `package.json` `engines`. It uses the same toolchain as the reference ranker, the golden generator and Playwright, which humans own. There are no runtime dependencies beyond the Node standard library.
- **Truth artifacts:** the *generated files*, not the generator. Every output is committed or stored along with its SHA-256 and a `manifest.json` containing:
  - generator git SHA
  - Node version
  - seed id
  - counts
  - script mix
  - length histogram

  Cross-platform determinism matters because outsiders must be able to regenerate the files ("reproduce within 10%"). CI enforces it by regenerating on Linux, macOS and Windows runners and diffing the hashes.
- **PRNG:** **xoshiro128\*\*** with 32-bit state words, seeded by SplitMix64 from a 64-bit seed. It uses only `Math.imul`, `>>> 0` and shifts, so every JS engine gives the same results. Check the implementation against the reference C test vectors, and port it to Rust later if needed.
  - Never use `Math.random`.
  - Never use floats in sampling. Use integer ranges with rejection sampling (`nextU32() % n` with rejection) and integer weights for mixes.
  - One PRNG stream per concern (items, queries, script mix), derived as `splitmix(seed ^ streamConst)`. Changing one then does not reshuffle the others.
- **Other determinism hazards to ban in the code, enforced by lint:**
  - `localeCompare`
  - `Intl.*`, including `Segmenter` (it depends on the ICU version)
  - `Array.prototype.sort` without an explicit comparator
  - `Object` key-order tricks
  - `Set` iteration over generated data (insertion order is fine but fragile)
  - OS line endings: always write LF
  - Unicode normalization calls at runtime: see below

### 1.2 Content design

The dataset's realism decides how many results tie and how many match, and those drive R1's latency. The distribution should look like a real command palette. It should not be random letters.

- **Latin, about 95%:** templates over a committed word list. Suggested templates:
  - verb + noun ("Open Recent File")
  - file paths `src/components/ui/…`
  - git branches `feat/…`
  - settings keys `editor.formatOnSave`
  - symbols `useLayoutEffect`
  - URLs and emails
  - mixed separators `/ _ - . # @ ( [ { & +`. These exercise cmdk's `IS_GAP_REGEXP` and `IS_SPACE_REGEXP` branches.
  - Title Case and camelCase, so the case-mismatch penalty matters.

  Word list: use a permissively licensed list, for example the EFF large wordlist (CC-BY-3.0), plus a curated developer vocabulary. **[decide]** the license and attribution format for publication.
- **Non-Latin, about 5%** (exact target 5.0% ± 0.2% at every sweep size): curated per-script phrase lists, committed and **reviewed by native speakers** (a human task, about 0.5 day, possibly outsourced), mixed with Latin in some items so bidi runs are exercised:
  - CJK (ja, zh-Hans, zh-Hant, ko)
  - Arabic, including lam-alef ligatures and digits
  - Hebrew
  - Devanagari, including conjuncts and nukta
  - emoji: ZWJ families and professions, skin-tone modifiers, keycaps, flags

  Note: **Windows' Segoe UI Emoji does not draw flag emoji**; it shows the regional-indicator letters. R1 and R5 will agree with each other, but the result is ugly, so it is worth documenting.
- **Uniqueness:** every item is unique by exact string. cmdk selects by *value equality* (`state.value === value.current`), so duplicate values highlight together and break keyboard tests. Recommend also making items unique after `toLowerCase()`, to keep screen-reader and keyboard assertions unambiguous.
- **Length:** 8-80 **Unicode scalar values** (code points). This is deterministic and needs no ICU. Record the grapheme and UTF-16 lengths in the manifest histogram. **[decide]** whether "characters" means code points or graphemes.
- **Unicode rules**, which the generator validates and fails on:
  - All items are **NFC**. The normalization is done offline and the committed phrase lists are checked. The generator asserts `s === s.normalize('NFC')`.
  - No control characters.
  - No bidi *override or embedding* characters (U+202A-202E, U+2066-2069). Natural RTL text only.
  - No leading or trailing whitespace. cmdk `.trim()`s values.
  - No internal whitespace other than U+0020.
  - No lone surrogates.
  - Excluded code points:
    - **U+0130 İ.** It is the only code point whose `toLowerCase()` changes UTF-16 length (checked over all of Unicode in Node 22 / ICU 78). This misaligns cmdk's `string` and `lowerString` indices: `score("İstanbul","istanbul") = 0.168` instead of about 0.99.
    - **U+03A3 Σ.** Context-dependent final sigma: `"ΑΣ".toLowerCase() === "ας"`, so querying `σ` against it scores 0.
  - Code points assigned only at or below Unicode 15.0, and emoji at or below Emoji 15.0. This keeps Chrome's ICU, Rust's `std` tables and OS fonts in agreement.
- **Size sweep:** generate one 100k stream and take **nested prefixes** (1k ⊂ 10k ⊂ 50k ⊂ 100k), so ids are stable across sizes. Use a stratified non-Latin draw so every prefix hits the 5% target. **[decide]** whether nesting is acceptable, since it means the 1k set is not an independent sample.
- **Output format:**
  - `items.json` = `{"schema":1,"seedId":"dev-1","count":50000,"items":["…", …]}`, in UTF-8 with LF line endings. The id is the array index.
  - `manifest.json` as above.
  - JSON works everywhere, including serde and `fetch`.
  - Rungs **load the dataset at runtime** from a path or URL the orchestrator provides (`/dataset/items.json` for web, `--dataset <path>` for native). Nothing is baked in at build time. The held-out swap then needs no rebuild, and build-time precomputation cannot specialize to the dataset. **[decide]** whether R2's "precomputed search index" may be built at build time. I recommend runtime only.

### 1.3 Query generator and held-out sets

- Queries are generated **from the dataset they target**, using a separate PRNG stream.
- Stratified classes with fixed quotas (for example, for 1,000 queries):

  | Class | Example |
  | --- | --- |
  | single character, weighted by first-character frequency (feeds `A.first_key`) | `o` |
  | word prefix | `sett` |
  | multi-word prefix | `open fi` |
  | acronym | `gtf` |
  | mid-word substring | `ranc` |
  | transposition | `reqeust` |
  | doubled letter | `oppen` |
  | case variants | `BRANCH` |
  | separator variants | `git-br`, `s/b` |
  | whitespace-only and leading or trailing spaces | `" "`, `"x "` |
  | non-Latin substrings | `設定`, `ملف`, `👩` |
  | NFD variants | expect 0 results |
  | guaranteed no-match | |
  | exactly 20-character ASCII (for `A.seq_key`) | |

  Report match-count and tie histograms per class in the manifest.
- **Rig-typeable subset:** HID can only type what the keyboard layout produces. The `A.*` query schedule uses ASCII lower-case, digits and space on a pinned US layout. Non-Latin queries are only used in the correctness suite, where they are injected programmatically.
- **Held-out secrecy:**
  - The generator code and the *dev* seeds are public to agents. The agents cannot modify them (spec guardrail).
  - Held-out seeds: `seed = HMAC-SHA256(secret, "heldout-v1/items")` and so on. The 256-bit secret lives only in the orchestrator's secret store (`03-orchestrator.md` §layout: private repo or encrypted blob decrypted only on the orchestrator host).
  - **Commit-reveal:** publish only `sha256(items.json)`, `sha256(queries.json)` and `sha256(goldens)` in the public repo at freeze time. Reveal the secret at publication, so outsiders can verify nothing was cherry-picked.
  - Add a **distribution-shifted** held-out set (a different word list and template mix) alongside the same-distribution set. If a rung passes the dev set but fails or slows on the shifted set, it has overfit.
  - Hygiene:
    - Held-out runs happen only on the orchestrator.
    - Parity and CI logs that agents can read print only pass/fail counts and query *ids*, never query text or ranked results.
    - Failure diffs go to a human-only location.

---

## 2. Reference ranking

### 2.1 What cmdk actually does

Behaviour of cmdk 1.1.1 with default props, as it applies to this project:

| Aspect | Behaviour (source) | Consequence |
| --- | --- | --- |
| Score | `commandScore(value, search, keywords)`: memoized recursive fuzzy match (command-score.ts). Constants: continue 1, space-word jump 0.9, non-space-word jump 0.8, char jump 0.17, transposition 0.1, skip penalty 0.999^n, case mismatch 0.9999, not complete 0.99. `PENALTY_DISTANCE_FROM_START` is declared but **unused**. | A match's position in the item does not matter while `stringIndex==0`. Spike: `score("zz…zz open","open") === score("zz open","open")`. |
| Keywords | Concatenated: `value + ' ' + keywords.join(' ')` before scoring. | Recommend **no keywords** in v1. **[decide]** |
| Case folding | `toLowerCase()` (full Unicode, locale-independent, *with* Final_Sigma). Case-mismatch penalty compares the *original* characters. | `score("a b","a-b") = 0.9999`: hyphen and space are equivalent, but the original characters differ, so the penalty applies. |
| Whitespace | `/[\s-]/g` → `' '` in the lowercased strings. JS `\s` is {U+0009-000D, 0020, 00A0, 1680, 2000-200A, 2028, 2029, 202F, 205F, 3000, FEFF}. | Rust `char::is_whitespace` is **different**: it includes U+0085 and excludes U+FEFF. The port needs an explicit set. |
| Unicode unit | `charAt` and `indexOf` work on **UTF-16 code units**. The regexes test single code units. | Astral characters and emoji match unit by unit. The port must work on `Vec<u16>`, not on `char`s. |
| Normalization / diacritics | None. `score("café","cafe") = 0`, and an NFD query against an NFC item scores 0. | Dataset and queries are NFC. No folding. |
| Empty search | `!state.search`: no filtering or sorting; all items shown in current DOM order. The search is **not trimmed**. | `" "` is a real query: it matches items containing whitespace or `-`, and `score("x y"," ") = 0.1683`. |
| Visibility | Item rendered iff `score > 0` (L682-684). | Result set is exactly {score > 0}. |
| Sort | Stable sort of the current DOM order by score descending; groups sorted by their best item. | Path-dependent ties (§0.1). Recommend **no groups** in v1. |
| Count shown | All matches. No limit, no virtualization in R1. | Conformance compares the **full** list. |
| Selection | After each search change, `schedule(1, selectFirstItem)`: the first non-disabled item in DOM order is selected (`aria-selected`, `data-selected`). | The selected id is part of the result contract. |
| `shouldFilter`, `filter`, `loop`, `vimBindings` | Defaults: filtering on, the cmdk scorer, no loop, **vim bindings on** (Ctrl+N/J next, Ctrl+P/K previous). Meta+Arrow goes to first or last, Alt+Arrow jumps between groups. | Keyboard spec (§4.2). |

### 2.2 Normative reference (proposed text for the spec)

> `rank(items, q)`: if `q === ""` return all ids in dataset order. Otherwise, for each item compute `s = cmdkScore_1_1_1(item, q, [])` in IEEE-754 binary64, evaluated exactly as the vendored source with `Math.pow(0.999, n)` replaced by the frozen table `POW_0999[n]`. Return the ids with `s > 0`, ordered by `s` descending and then id ascending. Ties are exact binary64 equality. The selected item is `result[0]`, or none. Queries are used as typed: no trim, no normalization.
>
> Conformance levels:
>
> - **strict**: identical id sequence. Required for R2-R6.
> - **tie-insensitive**: identical length, identical score at every rank, and the same id *set* within each tie group. Accepted for R1 only, because stock cmdk's tie order is history-dependent.

Ambiguities this resolves, which the spec must state explicitly:

- case folding, final sigma and U+0130
- the whitespace set
- UTF-16 units
- no normalization
- tie order
- untrimmed queries
- the full list rather than the top K
- the selected-first-item rule
- float `pow`
- keywords and groups

### 2.3 Implementations and verification

- **`ranker-ts`:** vendors `command-score.ts` from 1.1.1 verbatim under its MIT license, with the only change being `Math.pow` → `POW_0999`. It adds `rank()`.
  - Test 1: bit-equal to the unmodified `cmdk/dist` function under the pinned Chrome. The table is generated by that Chrome, so equality holds by construction, but the test guards against Chrome upgrades.
  - Test 2: a **browser oracle**. Real cmdk runs in pinned Chrome through Playwright. For each query, mount fresh, `fill()` once, and read the `[cmdk-item]` order. It must be strict-equal to `rank()` on a sample of about 200 queries per dataset. (The jsdom spike already shows this holds.)
- **`ladder-rank` (Rust crate):**
  - Works on `Vec<u16>` with explicit JS-whitespace and gap predicates.
  - Mirrors JS `charAt` out-of-range (`''`) as `None`.
  - Clamps `slice(start, end)` when `end < start`. JS returns `""`; a naive Rust slice would panic.
  - Uses the `POW_0999` table.
  - Uses a **lowercase table generated from pinned Chrome** (`String.fromCodePoint(c).toLowerCase()` for every code point, plus a final-sigma rule and a test) instead of `str::to_lowercase`. This avoids drift between Chrome's ICU and Rust's Unicode tables.
  - Builds for native (R5) and `wasm32` (R4). R4 and R5 may use it or reimplement it, but they must pass the goldens.
  - **Spike result:** a 110-line port matched cmdk bit-for-bit on 500,000 pairs (20k mixed-script items × 25 queries). `f64::powf` produced 596 mismatches.
- **Differential testing:**
  - The full dev dataset × all dev queries, comparing TS and Rust score bits.
  - Plus property-based fuzzing: random strings over a tricky alphabet (separators, the whitespace set, astral characters, `İ`, `Σ`, repeated letters) with Node as the oracle, through a JSONL pipe.
  - Plus hand-written edge goldens: every row of the table above, and the quirks in §0.5.
- **Golden files**, one per (dataset, query set):

  ```
  {"qid":17,"count":27269,"idsSha256":"…","selected":4821,
   "top":[[4821,"3fef…"],…100 entries of [id, scoreBitsHex]],
   "tieGroups":[[scoreBitsHex, count],…]}
  ```

  Hashes plus the top 100 keep the files small: a 1-character query at 50k produces about 40k ids, which would come to tens of MB raw. When a hash mismatches, the runner regenerates the full list with `ranker-ts` and prints the first divergent rank.
  - Generation cost: 55-285 ms per query per 50k items in Node, so 1,000 queries take about 3 minutes. Rust is faster.
- **Conformance runner** (`ladder conform --rung R5 --dataset dev-1 --queries dev-1`): drives the rung, collects the ids it ranks, compares them at the required level, and writes a JUnit or JSON report.

---

## 3. R1 skeleton (scope only)

### 3.1 "Typical, default config, production build, no virtualization", concretely

- `npx create-next-app@latest` with the defaults (TypeScript, ESLint, Tailwind, App Router, Turbopack), then `npx shadcn@latest init` and `npx shadcn@latest add command`. The last step pulls `cmdk` (currently 1.1.1), `lucide-react` and the dialog.
- **Pin every version in the lockfile at project start.** Record `next`, `react`, `cmdk` and `tailwindcss` in the manifest.
- **Unmodified shadcn `command.tsx`** (new-york-v4). It is used inline: `<Command><CommandInput autoFocus/><CommandList><CommandEmpty>No results found.</CommandEmpty>{items.map((t,i)=><CommandItem key={i} value={t}>{t}</CommandItem>)}</CommandList></Command>`.
  - There is no `shouldFilter`, custom `filter`, `CommandGroup` or icons.
  - **Uncontrolled input.** shadcn's own demos use it, and it is the path where the list updates inside the input event (§0.4).
- **Inline `<Command>` versus `CommandDialog`.** The dialog is the more typical ⌘K surface. It adds Radix portal, focus trap, overlay and open animations, and gives Escape a meaning. **[decide]** I recommend inline for Scenario A, with a `CommandDialog` variant kept for the keyboard and Escape checks only if the human wants it.
- **Data loading:** a client component fetches `/dataset/items.json` in `useEffect`.
  - The typical alternatives are importing a JSON bundle or server-rendering from an RSC.
  - SSR-rendering 50k shadcn rows would put roughly 20 MB of HTML into the page (about 400 B of class attributes per row). That dominates Scenario D and does not represent what teams ship for dynamic data. **[decide]**
- `next build && next start`, using Chrome's default flags and a clean profile (per spec).
- **Test hooks** (humans own them; agents never change R1):
  - The marker (§3.3).
  - A read-only `window.__ladder = { dataset, version }` for the parity scripts.
  - The Enter action sets `data-last-selected` on the root (a visible no-op), so Enter can be asserted.

### 3.2 Next.js versus Vite (open question in the spec)

After hydration, Scenario A runs identical React and cmdk code, so the frameworks should differ mainly in:

- cold start (D): the RSC payload, router and hydration
- memory

Recommendation: build **R1 on Next** (the spec as written) and **R1-vite** (`npm create vite@latest -- --template react-ts` + Tailwind + shadcn) from the *same* `Palette.tsx`, which is about 2 agent-hours extra.

Measure both at Gate 1 on Scenarios A and D. If A differs by less than the noise band, the question matters only for D, and the answer is itself a small result.

### 3.3 Latency marker in React: "same code path, same frame"

Where the list update actually happens in cmdk (uncontrolled input):

1. `onChange` → `store.setState('search')`. `filterItems()` and `sort()` run synchronously; `sort()` reorders DOM nodes directly.
2. `emit()` → `useSyncExternalStore` subscribers re-render. Updates from a discrete event run at SyncLane and commit in the same task, where items mount or unmount.
3. Layout effects run `schedule()`d work in further synchronous commits (sorting newly mounted items, selecting the first item), still before paint.

**Recommended marker:**

- A `<LatencyMarker/>` rendered *inside* `<Command>`.
- It reads `const search = useCommandState(s => s.search)`. `useCommandState` is exported by cmdk and reads the same store as the items, so it re-renders in the **same commit** as the item mounts.
- It toggles a black or white class on its own DOM node in `useLayoutEffect(..., [search])`. The layout effect runs in that commit, after all DOM mutations for the new query, before the browser's rendering opportunity.
- Geometry comes from the harness-owned `marker.json` (`04-calibration.md` §4): `position:fixed`, no transition, no `will-change`, pure `#000` and `#fff`, and it ignores the theme.

Why the naive versions go wrong:

- **Early, if keyed off the input value.** Examples: `onValueChange` → parent `useState`, a `keydown` handler, or `useDeferredValue`/`startTransition` in R2.
  - With a controlled `CommandInput`, cmdk sets `search` in a passive `useEffect` *after* the parent's commit.
  - With deferred rendering, the list commits one or more frames later.
  - In both cases the marker flips before the list.
- **Late with `requestAnimationFrame`.** An rAF callback queued *during* a rAF callback, or during a frame's rendering steps, runs on the **next** frame. If React commits from inside a rAF or a scheduler task that lands after the frame's rAF phase, a marker set in "the next rAF" misses the frame the list paints in.
- **Unreliable with `useEffect`.** React 18+ flushes passive effects synchronously only for discrete-input updates. For other lanes, such as transitions or updates from a `fetch` or worker message in R2 and R3, passive effects run after paint, so the marker can be one frame late. A `setState` in an effect also adds a second render.
- **Rule for all web rungs:** the marker DOM write happens in the same JS task as, and after, the last DOM mutation that reflects the new query, with no rendering opportunity in between. Rungs R4 to R6 use the equivalent rule: the marker is in the same draw submission or present as the list.

**Cheap software proxy for marker honesty** (Phase 1, before the camera exists):

- An injected script (Playwright `addInitScript`) keeps a rAF frame counter and `MutationObserver`s on the marker and the list.
- For each query change it records `(frameCounter, performance.now(), taskSeq)` for the last list mutation and for the marker mutation.
- It asserts both fall in the same frame and that the marker is last.
- Optionally, a Chrome trace (`Paint`/`Commit` events) cross-checks this.

This catches most dishonest markers in CI. The 1,000 fps camera remains the authority (spec).

---

## 4. Parity pieces that are cheap now

| Piece | What to build | Phase | Notes |
| --- | --- | --- | --- |
| **Correct results** | `ladder conform` (§2.3). Web: `fill()` per query and read the DOM, or R2 and R3 use a `__ladder.results()` probe fed from the same array the renderer uses. Native: a `--probe` JSON-over-stdio or localhost mode **in the release binary** (so it is not a separate code path), plus a check of the top 10 against the accessibility tree (UIA / AX via AccessKit). | **Phase 1 (R1 + R5)** | For R1, run tie-insensitive without reloads. Reloading 50k rows 1,000 times would take hours. |
| **Marker-honesty proxy** | §3.3 | **Phase 1** | It is the first line of defence for agents; the camera audits it. |
| **Keyboard script** (Playwright) | A written keyboard spec plus assertions on `aria-selected`, `aria-activedescendant` and focus-visible for ↓ ↑ Home End Enter Ctrl+J/K/N/P and Meta/Alt+Arrow. PageUp/PageDown/Escape as defined by the **[decide]** in §0.3. | **Phase 1 for web**. The R5 native driver (UIA on Windows, AX on macOS, or HID keys from the rig plus an accessibility-tree read) is late Phase 1 or Phase 2. | cmdk's Home/End interception is R1 behaviour. Decide whether rungs must copy it. |
| **Text-editing script** | Type, Shift+Arrow and Ctrl+A select, Ctrl+C/V with clipboard permission, Ctrl+Z/Ctrl+Shift+Z. Assert the input value and that the results match the reference after each step. | **Phase 1 for web**; native in Phase 2. | Undo in a React-controlled input is fragile; R1 is uncontrolled, so native undo works. Shift+Home is intercepted by cmdk. |
| **Visual diff harness** | A fixed viewport and palette geometry. Screenshots with `animations:'disabled'` and `caret:'hide'`, at DPR 1, 1.5 and 2, light and dark. The metric is SSIM or dssim plus a pixelmatch overlay; the threshold is **[decide]**. A capture adapter accepts PNGs from any source (Playwright for web; Windows.Graphics.Capture or `screencapture -l` for native). | **Phase 1:** the harness plus R1 baselines (about 20 states, taken on fresh mount + `fill`). R5 comparison at Gate 1 is advisory. | Chrome/Skia and DirectWrite/CoreText anti-aliasing differ, so a strict pixel diff will fail R5. The text-shaping check needs per-string crops at 2x and human sign-off, not only a global threshold. |
| Theming, scaling, accessibility-tree dump, IME, screen reader | — | Phase 2+ (R1 baselines captured in Phase 1 are cheap) | CDP `Accessibility.getFullAXTree` for the dump. IME stays manual. |
| **Sandbox / launch-config review** | A lint over the orchestrator's Chrome launch arguments. | Phase 1, trivial | Playwright adds automation flags and, on Linux as root, `--no-sandbox`. **Never measure latency through Playwright-launched Chrome.** Parity runs only. |

---

## 5. Risks

| Risk | Effect | Mitigation |
| --- | --- | --- |
| Tie semantics left unspecified | R1 "fails" its own reference, or rungs argue about order | §2.2 two conformance levels; human sign-off. |
| Float or Unicode drift (a Chrome upgrade, a Rust `std` upgrade) | Silent rank flips | Frozen `POW_0999` and lowercase tables; CI regenerates the tables from the pinned Chrome and diffs them. |
| Synthetic data is unrealistic | Tie and match distributions, and so R1's latency, are artifacts of the generator | Command-palette templates; publish histograms; distribution-shifted held-out set. |
| R1 at 50k slower than the key cadence | Queued input and ambiguous per-key attribution | Flagged to rig and orchestrator. Consider a 10k headline (open question in the spec). |
| Parity checklist unachievable by stock R1 (PageUp/PageDown/Escape/Home) | Baseline is non-parity | Define the checks relative to R1 or amend R1 by rule. |
| Held-out leakage via logs or artifacts | The overfit guard fails | Id-only logs, orchestrator-only runs, commit-reveal. |
| Font coverage differs by OS (flags on Windows, Devanagari fallback) | Text-shaping diff noise | Per-OS baselines; the diff is always within a machine, never across. |
| Prefilter indexes in R2 and R3 miss transposition matches | Wrong results that look plausible | Golden edge cases (§0.5) and fuzzing against the reference. |

---

## 6. Task breakdown

Estimates are in agent-hours (AH) for code and human-days (HD) for review, decisions and secrets. They assume the spikes in `spikes/` are reused.

| # | Task | Est. | Depends on | Phase |
| --- | --- | --- | --- | --- |
| F1 | Dataset generator: PRNG with test vectors, templates, script lists, validators, nested sweep, manifest, 3-OS CI hash check | 8-10 AH + 0.5 HD native-speaker review | decisions D1-D3 | 0 |
| F2 | Query generator (classes, quotas, rig-typeable subset, histograms) + held-out process (HMAC seeds, private store, commit-reveal, shifted set) | 5-6 AH + 0.5 HD secrets setup | F1 | 0 |
| F3 | `ranker-ts`: vendored scorer, `POW_0999` + lowercase table generation from pinned Chrome, `rank()` | 3 AH | — | 0 |
| F4 | `ladder-rank` Rust crate (u16, tables, wasm target) + differential and fuzz tests against F3 | 6-8 AH | F3 | 0-1 |
| F5 | Browser oracle: real cmdk in pinned Chrome vs `rank()` (strict, fresh mount) | 3-4 AH | F3, F1 | 0 |
| F6 | Golden generator + `ladder conform` runner (strict and tie-insensitive, JSON/JUnit, redacted output) | 5-6 AH | F2, F3 | 0-1 |
| F7 | R1 (Next) + R1-vite from a shared `Palette.tsx`: pinned versions, runtime dataset load, marker, test hooks | 5-7 AH + 0.5 HD human "is this typical?" sign-off | F1, D4-D6 | 1 |
| F8 | Marker-honesty software proxy (injected script + assertions; optional trace check) | 4 AH | F7 | 1 |
| F9 | Keyboard spec + Playwright script (web) | 4 AH (+ 8-12 AH native driver, later) | F7, D7 | 1 (native: 1-2) |
| F10 | Text-editing script (web) | 3 AH (+ native later) | F7 | 1 |
| F11 | Visual diff harness + capture adapters + R1 baselines | 6-8 AH | F7, D8 | 1 |
| F12 | CI wiring (generator hash check, TS/Rust differential, goldens, R1 parity on push) + launch-config lint | 4 AH | F1-F11 | 1 |

**Total:** about 56-73 AH plus about 2 HD of human review, and the decisions below. The critical path is F1 → F2 → F6 → ready to run `conform` against R1 and R5. It takes about 20 AH and can finish before the rig hardware arrives. F3, F4 and F5 run in parallel with F1.

## 7. Open decisions for a human

- **D1.** "Characters" for the 8-80 length: code points (recommended) or graphemes.
- **D2.** Word-list source, license and attribution. Whether nested size-sweep prefixes are acceptable.
- **D3.** Exclude U+0130, U+03A3, NFD text, Unicode above 15.0 and bidi overrides (recommended), or keep them and specify cmdk's quirks as reference behaviour.
- **D4.** Inline `Command` or `CommandDialog` for R1.
- **D5.** R1 data loading: client `fetch` (recommended), a bundled import, or SSR.
- **D6.** Next only, or Next plus Vite measured at Gate 1 (recommended).
- **D7.** Keyboard and text-editing checks defined as "same as R1" (no PageUp/PageDown/Escape; Home/End move the selection), or R1 amended by rule.
- **D8.** Visual-fidelity metric and threshold. Text-shaping sign-off procedure.
- **D9.** Tie semantics: strict (score descending, id ascending) for R2-R6 with R1 tie-insensitive (recommended), or tie-insensitive for all rungs.
- **D10.** Whether R2 may precompute its index at build time, or only at runtime (recommended).
- **D11.** No keywords and no groups in v1 (recommended).
- **D12.** 50k or 10k headline, given that scoring alone costs 55-285 ms per keystroke at 50k.
