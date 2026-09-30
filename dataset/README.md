# dataset — Latency Ladder dataset and query generator

This package generates the items and queries every rung searches. It follows the design in
`docs/phase-0/05-software-foundations.md` §1 and the Dataset paragraph of `docs/spec.md`.
Output is deterministic from a 64-bit seed. The generated files are the source of truth; the
generator is only how they are made.

There are no runtime dependencies beyond the Node 22 standard library. TypeScript runs
through Node's built-in type stripping (`node --experimental-strip-types`), so the package
needs no build step and works offline. `typescript` and `@types/node` are dev dependencies,
used only by `npm run -w dataset typecheck`.

## Commands

Run these from the repository root.

```sh
# Default: the nested sweep 1k/10k/50k/100k from one stream, plus ONE shared query set
npm run -w dataset gen -- --seed-id dev-1        # same as --sizes 1000,10000,50000,100000
#   -> dataset/out/dev-1/{queries.json,manifest.json,provenance.json}
#   -> dataset/out/dev-1/<size>/{items.json,manifest.json,provenance.json}

# A separate query set for each size, written into each size directory
npm run -w dataset gen -- --seed-id dev-1 --sizes 1000,10000 --per-size-queries

# One size, everything in one directory. --queries 0 skips queries.
npm run -w dataset gen -- --seed-id dev-1 --size 100000 --out /tmp/ll

# Distribution-shifted variant. The seed id becomes "dev-1-shifted".
npm run -w dataset gen -- --seed-id dev-1 --shift --size 50000

# (Re)generate queries from existing files: draw from --dataset, check guarantees against --check.
# The queries are identical to the shared set; the header lists only the hashes of these two files.
npm run -w dataset queries -- --dataset dataset/out/dev-1/1000/items.json \
  --check dataset/out/dev-1/100000/items.json --out /tmp/q

# Held-out set (orchestrator host only). The secret comes from a file or LL_HELDOUT_SECRET.
npm run -w dataset gen -- --seed-id heldout-v1 --size 50000 --secret-file /secure/ll.hex --out /secure/out
npm run -w dataset commit -- /secure/out /secure/out/50000   # sha256 lines for commit-reveal

npm run -w dataset sample     # 40-item sample plus approximate match counts (dev seeds only)
npm run -w dataset golden     # rewrite golden/dev-1.json (only after an intended change)
npm run -w dataset test       # node:test suite, about 10 s
```

On this machine (Node 22.22), generating 100k items takes about 0.9 s. The default sweep
(4 sizes plus the shared 1,000-query set, written to disk) takes about 4 s.

## Outputs

| File | Contents | Hash-pinned for dev-1 |
| --- | --- | --- |
| `<size>/items.json` | `{"schema":1,"seedId","count","items":[…]}`, id = index | yes |
| `<size>/manifest.json` | seed id, seed source, variant, counts, script mix, template mix, length histograms, sha256 and byte size of `items.json`. **Byte-stable**: a pure function of the items. | yes |
| `queries.json` | `{"schema":1,"seedId","datasetSha256" (largest size),"datasetSha256BySize","drawnFromSize","checkedAgainstSize","queries":[{"qid","q","class","typeable"}]}` | yes |
| `manifest.json` (next to `queries.json`) | seed id, the per-size dataset hashes, per-class query counts and typeable counts, sha256 of `queries.json`. Byte-stable. | yes |
| `provenance.json` (in every directory) | generator git SHA and dirty flag, Node version, platform, arch. **Not** hash-pinned. No timestamp is recorded, because wall-clock time is banned in `src/`. | no |

**Shared queries (default).** There is one query set per seed. It is drawn from the
**smallest** prefix (1k), so every query targets items that exist at every nested size. The
no-match and NFD guarantees are checked against the **largest** size (100k); because the
sizes are prefixes, that makes them hold at every size. The same `queries.json` is used with
every size. `--per-size-queries` restores separate sets, each drawn from and checked against
its own size.

## Layout

```
src/prng.ts      xoshiro128** + SplitMix64, integer-only sampling, stream constants
src/unicode.ts   item validator, repertoire, script classifier, NFD table, approx. clusters
src/data.ts      loads and checks the committed word and phrase lists
src/items.ts     Latin templates, non-Latin templates, stratified 5% mix, uniqueness
src/queries.ts   14 query classes with quotas, typeable flag, no-match guarantee
src/seeds.ts     dev seed registry, HMAC held-out seeds, secret loading
src/output.ts    LF JSON serialization, sha256, manifest
src/pipeline.ts  glue used by the CLI and the tests
src/cli.ts       gen | queries | commit | sample | golden
data/words/      eff_large_wordlist.txt (CC BY 3.0 US, see ATTRIBUTION.md), dev-vocab.json
data/phrases/    ja, zh-Hans, zh-Hant, ko, ar, he, hi, emoji, latin-extended (.tsv)
data/words/denylist.txt  EFF words filtered out at load
golden/dev-1.json  sha256 of dev-1 items.json + manifest.json per size, shared queries.json + manifest.json
test/            prng, unicode, items, queries, seeds, lint (banned-API grep)
```

`out/` is gitignored through `dataset/.gitignore`.

## Decisions (owner defaults, recorded 2026-09-30)

| Topic | Decision |
| --- | --- |
| Length | 8–80 **Unicode code points**. The manifest also records UTF-16 and UTF-8 lengths, and an *approximate* grapheme count, as information only. No `Intl.Segmenter` is used. The approximate count follows a committed rule (`approxClusters`: base plus marks, VS16, skin tones, keycap, ZWJ joins, regional-indicator pairs; Devanagari conjuncts are not merged, as in Unicode ≤ 15.0). |
| Repertoire | NFC only. The following are excluded: U+0130, U+03A3, NFD text, bidi overrides, embeddings, isolates and marks (U+202A–202E, U+2066–2069, U+200E/F, U+061C), control characters, whitespace other than U+0020, double spaces, leading or trailing space, and lone surrogates. Every code point must fall in `ALLOWED_RANGES`. Those ranges are all assigned by Unicode 14.0, and they leave out every code point added in Emoji 15.0, which is stricter than the ≤ 15.0 rule. Emoji sequences come only from `emoji.tsv`, whose entries are all Emoji ≤ 13.0. The generator validates every item and **throws** on any violation, apart from length, which is handled by rejection sampling. |
| Keywords and groups | None. |
| Uniqueness | Unique by exact string **and** by `toLowerCase()`. |
| Size sweep | Nested prefixes of one stream (1k ⊂ 10k ⊂ 50k ⊂ 100k), so ids are stable across sizes. Generating size N directly gives exactly the first N items of any larger run. |
| Non-Latin share | Stratified: each consecutive block of 20 items has exactly one non-Latin item, at a position drawn uniformly from 0–19 for each block from its own PRNG stream (`mix`). There is no fixed stride: consecutive non-Latin items can be 1 to 39 items apart, and a test checks that every offset occurs. Every prefix whose length is a multiple of 20 is exactly 5.000%, any other prefix is within one item of it, and the sweep sizes are all exactly 5.000%. |
| Latin (95%) | Templates over EFF words and the developer vocabulary: commands (verb + [adj] + noun + [adjunct], in Title, Sentence or lower case), `Category: command`, file paths, git branches, settings keys, symbols (camel, Pascal, snake, SCREAMING, `a::B`, `A.b()`), URLs, emails, mixed separators `/ _ - . # @ ( [ { & +`, document titles, issue refs, and a few Latin-with-diacritics phrases (about 1% of items are non-ASCII Latin). |
| Word list | The EFF large wordlist was fetched over HTTPS on 2026-09-30 and is committed unmodified with attribution. Its sha256 is checked at load time. The standard variant uses the even-indexed half and the shifted variant uses the odd-indexed half, so their general vocabularies do not overlap. `dev-vocab.json` is original. |
| Denylist | `data/words/denylist.txt` is a short, conservative list (52 words) of EFF words that would read badly in a command palette: crude, sexual, violent, bodily, slur-adjacent, political or trademark words, for example `grope`, `thong`, `handgun`, `racism`, `marxism`, `facebook`. The filter is applied at load, and the EFF file stays unmodified. The even/odd split uses the original line numbers, so the filter does not move words between variants. Every denylist entry must exist in the EFF list, and a test checks that no denylisted word appears as a word in any item. Editing the list changes the dataset. |
| PRNG | xoshiro128** (32-bit), seeded by SplitMix64 (BigInt) from a 64-bit seed. The state words are `[lo(a), hi(a), lo(b), hi(b)]` for two SplitMix outputs `a` and `b`. Sampling is `below(n)` with rejection. Weights are integers. There is one stream per concern (`mix`, `latin`, `nonLatin`, `queries`, and one per query class), each seeded with `seed ^ streamConst`. Both generators are checked against published reference vectors (see `test/prng.test.ts`). |
| Held-out | `seed = first 8 bytes BE of HMAC-SHA256(secret, "<seedId>/items")` (and `/queries`). The secret is at least 256 bits, read from `--secret-file` (hex or raw) or `LL_HELDOUT_SECRET`, and never written anywhere. `commit` prints sha256 lines for commit-reveal. `sample` refuses non-dev seeds. |
| Shifted variant | Selected with `--shift`, or a seed id ending in `-shifted`. The template weights are different (paths, symbols, branches and URLs up; commands down), it uses the other half of the EFF list, and the non-Latin mix leans more on RTL and Devanagari. For dev seeds the seed is `dev ^ "shifted!"`. For held-out ids the HMAC label contains `-shifted`. |
| Output | See [Outputs](#outputs). The default directory is `dataset/out/<seedId>/`. The JSON has one entry per line, UTF-8, LF only, with `id = index` and `qid = index`. |
| Dev seed | `dev-1` = `0x4c4c2d6465762d31` ("LL-dev-1"). |

### Determinism rules, enforced by `test/lint.test.ts`

The following are banned in `src/`:

- `Math.random`
- `localeCompare`
- `Intl.*`
- `.sort()` and `.toSorted()` without a comparator
- `toLocale*`
- `Date.now` and `new Date(`
- CRLF literals
- `.normalize(` outside `unicode.ts` (the NFC assertion in the validator) and `data.ts` (the NFC check of the phrase lists).

The NFD query class uses a committed decomposition table (`toNfd`: algorithmic Hangul plus Latin, kana, Arabic and Devanagari entries) instead of `normalize`. A test checks it against `String.prototype.normalize("NFD")` over the whole allowed repertoire.

## Query classes (per 1,000; quotas scale by largest remainder)

| Class | Quota | How it is drawn (always from the target dataset) | Typeable |
| --- | --- | --- | --- |
| single-char | 80 | First character of a random item, so it is weighted by first-character frequency. Repeats are allowed. | yes |
| word-prefix | 140 | 2–8 character prefix of an ASCII word | yes |
| multi-word-prefix | 100 | 1–3 whole words, then a prefix of the next word (`open fi`) | yes |
| acronym | 70 | First letters of 2–4 words or camelCase humps | yes |
| mid-word | 80 | 3–5 character substring that does not start the word | yes |
| transposition | 60 | Two adjacent letters swapped in a word prefix (`reqeust`) | yes |
| doubled-letter | 50 | One letter doubled (`oppen`) | yes |
| case-variant | 60 | UPPER, Title or aLtErNaTiNg case of a word or multi-word prefix | no |
| separator-variant | 60 | Two words joined by `- _ / . :` differently from the item (`git-br`, `s/b`) | no |
| whitespace | 40 | `" "`, `"  "`, and leading, trailing or both-sides spaces around a prefix | yes |
| non-latin | 80 | 1–3 approximate clusters from a non-Latin run, sometimes a lone emoji code point (`👩`) | no |
| nfd | 30 | NFD form of a window around a decomposable character. Guaranteed to match nothing. | no |
| no-match | 50 | Rare-letter strings, a prefix plus rare letters, or a prefix plus a character absent from the dataset. Guaranteed to match nothing. | mixed |
| ascii-20 | 100 | Exactly 20 characters of `[a-z0-9 ]` from an item, starting at a word boundary (for `A.seq_key`) | yes |

`typeable` means the query uses only ASCII lower-case letters, digits and space on a pinned US layout.

**No-match guarantee.** A no-match query is accepted only if `relaxedMatch` fails against every item. `relaxedMatch` is an over-approximation of cmdk's match set: a subsequence match in which non-first, non-adjacent query characters may be skipped, as cmdk's transposition and duplicate branch allows. So "no relaxed match" implies cmdk score 0. The reference ranker should confirm this.

## Pending human tasks

- **Native-speaker review** of every file in `data/phrases/`: ja, zh-Hans, zh-Hant (Taiwan usage), ko, ar, he, hi, and `latin-extended.tsv` (de, fr, es, pt, pl, sv, da, cs). The lists are short common UI phrases I wrote. Each file is marked "PENDING NATIVE-SPEAKER REVIEW". Editing any list changes the dataset, so regenerate `golden/dev-1.json` afterwards.
- **License and attribution format for publication** (EFF CC BY 3.0 US), per §1.2 [decide].

## Realism check (dev-1, 50k)

This is from `npm run -w dataset sample`: every 1,250th item.

```
     0  Move & Generate Schema
  1250  ingrid/acetone-commit
  2500  Create Line Numbers
  3750  Go to changes on save
  5000  Disable First Report
  6250  Slack: Save All Hidden Notebook As JSON
  7500  223페이지 중 168페이지 feat/mob-9252-tightly-bookmark
  8750  DATA-4802: Revert task
 10000  restore project
 11250  release/color-theme-petition-certificate-certificate
 12500  Deploy End Of Line Sequence To The Side
 13750  bugfix/developer-tools-schema
 15000  C/C++: Uložit změny
 16250  elena.nguyen@example.com
 17500  workbench.stargazer.stateFactory
 18750  diego.garcia@example.org
 20000  pkg/bin/tooltip.java
 21250  spike/bookmark-full-screen-relapsing
 22500  Toggle Channel As Markdown
 23750  BackwashCallback.applyPalette()
 25000  libs/predator-vacancy/headwear/DarkroomCallback.swift
 26250  Format Repository
 27500  #suburb-calendar
 28750  Approve Hidden Tab Size
 30000  Unpin pending launch configuration
 31250  obscurity::ListenerFactory
 32500  {hamletEffect} binding
 33750  Pull test explorer
 35000  hotfix/proj-4152-secret-gratuity-stung-full-screen
 36250  Vigorous sampling crux kettle (final) Abrir archivo
 37500  test/mob-6115-remedy-yam-user
 38750  Expand passcode editor by date
 40000  C#: View Task
 41250  Explorer: Push implementation to clipboard
 42500  ./src/crawlers_waking/footer.cs
 43750  Slack: Move note on save
 45000  distinct_frugality/api/country/server/primal-request.h
 46250  /migrations/alias-prankster/appraisal-junkman/aloha-cache.sh
 47500  amara/release-tab-size-outsource-status-bar-window
 48750  Insert Archived Color Theme
```

Some non-Latin items from the same set:

```
ファイルを保存・ファイルを開く・すべて選択       Upload — 上传
התחבר (Log In)                                  5️⃣ Step Five: Unstage current developer tools
طباعة (٦٩٤)                                      Live Share: 108페이지 중 36페이지
👩‍🔬 Scientist: Sync local comment               Rainbow Flag 🏳️‍🌈 Stowing Juggle Snare Hatchery
```

The table below gives match counts at 50k. They use a **case-insensitive subsequence approximation, not the ranker**:

| Query | Matches | Share |
| --- | ---: | ---: |
| `a` | 40,292 | 80.6% |
| `o` | 38,846 | 77.7% |
| `op` | 15,145 | 30.3% |
| `open` | 5,847 | 11.7% |
| `open fi` | 328 | 0.7% |
| `sett` | 9,360 | 18.7% |
| `gtb` | 3,189 | 6.4% |
| `reqeust` | 21 | <0.1% (subsequence only; cmdk's transposition branch will match more) |
| `設定` | 27 | 0.05% |
| `ملف` | 56 | 0.1% |
| `👩` | 65 | 0.1% |

For comparison, the spike's `a` at 50k gave 39,965 matches, all with one score. The share for `op` is lower here (30% against about 54% in the spike), because the templates are more varied than the spike's fixed 30-word list.

## Open questions

1. Per-class **match-count and tie histograms** in the query manifest (§1.3) need the reference ranker. They are left for `parity/` to add (the manifest has a `note` saying so).
2. The denylist is my conservative first pass. It should be reviewed along with the phrase lists.
