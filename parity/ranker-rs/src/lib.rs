//! `ladder-rank`: the Latency Ladder reference ranker in Rust.
//!
//! A bit-exact port of cmdk 1.1.1 `command-score.ts` (MIT, Paco Coursey) evaluated the
//! way the pinned Chromium evaluates it, plus the normative `rank()` of
//! docs/phase-0/05-software-foundations.md §2.2:
//!
//! * all string operations are on UTF-16 code units (`&[u16]`), like JS `charAt`/`indexOf`;
//! * `Math.pow(0.999, n)` is the frozen table `POW_0999[n]` generated in Chromium;
//! * `toLowerCase()` is a frozen table generated in Chromium plus the Final_Sigma rule
//!   (never `str::to_lowercase`);
//! * JS `\s` is an explicit code-unit set (not `char::is_whitespace`);
//! * JS `charAt` out of range (`""`) is modelled as `None`, and `slice` clamps.
#![forbid(unsafe_code)]

mod tables {
    include!(concat!(env!("OUT_DIR"), "/tables.rs"));
}
pub use tables::{POW_MAX, TABLE_CHROMIUM};

const SCORE_CONTINUE_MATCH: f64 = 1.0;
const SCORE_SPACE_WORD_JUMP: f64 = 0.9;
const SCORE_NON_SPACE_WORD_JUMP: f64 = 0.8;
const SCORE_CHARACTER_JUMP: f64 = 0.17;
const SCORE_TRANSPOSITION: f64 = 0.1;
const PENALTY_CASE_MISMATCH: f64 = 0.9999;
const PENALTY_NOT_COMPLETE: f64 = 0.99;

/// `POW_0999[n]`: `Math.pow(0.999, n)` as computed by the pinned Chromium.
#[inline]
pub fn pow_0999(n: usize) -> Option<f64> {
    tables::POW_0999_BITS.get(n).map(|&b| f64::from_bits(b))
}

/// Why a score could not be produced.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ScoreError {
    /// Upstream cmdk recurses without bound and throws `RangeError: Maximum call stack size
    /// exceeded` for this input (only reachable when lowercasing lengthens the query, i.e.
    /// U+0130). The TS reference throws the same error.
    JsRangeError,
    /// The lowercased item is longer than the frozen POW table (`POW_MAX` code units).
    TooLong,
}

impl core::fmt::Display for ScoreError {
    fn fmt(&self, f: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        match self {
            ScoreError::JsRangeError => f.write_str("RangeError"),
            ScoreError::TooLong => f.write_str("TooLong"),
        }
    }
}
impl std::error::Error for ScoreError {}

// ---------------------------------------------------------------------------
// Character classes (single UTF-16 code units, as JS non-unicode regexes see them)

/// JS RegExp `\s` on one UTF-16 code unit (ES2024: WhiteSpace + LineTerminator).
/// Explicit set; differs from `char::is_whitespace` (U+0085 excluded, U+FEFF included).
#[inline]
pub fn is_js_whitespace(c: u16) -> bool {
    matches!(c, 0x09..=0x0d | 0x20 | 0xa0 | 0x1680 | 0x2000..=0x200a | 0x2028 | 0x2029 | 0x202f | 0x205f | 0x3000 | 0xfeff)
}
/// `/[\s-]/`
#[inline]
pub fn is_space(c: u16) -> bool {
    c == 0x2d || is_js_whitespace(c)
}
/// `/[\\\/_+.#"@\[\(\{&]/`
#[inline]
pub fn is_gap(c: u16) -> bool {
    matches!(c, 0x5c | 0x2f | 0x5f | 0x2b | 0x2e | 0x23 | 0x22 | 0x40 | 0x5b | 0x28 | 0x7b | 0x26)
}

/// The `\s` code units as measured in Chromium (for tests: must equal `is_js_whitespace`).
pub fn js_whitespace_table() -> &'static [u16] {
    tables::JS_WHITESPACE_TABLE
}

// ---------------------------------------------------------------------------
// String.prototype.toLowerCase (root locale), from the Chromium table

fn in_ranges(r: &[(u32, u32)], c: u32) -> bool {
    r.binary_search_by(|&(lo, hi)| {
        if hi < c {
            core::cmp::Ordering::Less
        } else if lo > c {
            core::cmp::Ordering::Greater
        } else {
            core::cmp::Ordering::Equal
        }
    })
    .is_ok()
}

/// Code point starting at `i` (lone surrogates are returned as themselves) and its length.
#[inline]
fn cp_at(s: &[u16], i: usize) -> (u32, usize) {
    let u = s[i];
    if (0xd800..0xdc00).contains(&u) {
        if let Some(&l) = s.get(i + 1) {
            if (0xdc00..0xe000).contains(&l) {
                return (0x10000 + (((u as u32) - 0xd800) << 10) + ((l as u32) - 0xdc00), 2);
            }
        }
    }
    (u as u32, 1)
}
/// Code point ending just before `i`, and its start index.
#[inline]
fn cp_before(s: &[u16], i: usize) -> Option<(u32, usize)> {
    if i == 0 {
        return None;
    }
    let l = s[i - 1];
    if (0xdc00..0xe000).contains(&l) && i >= 2 {
        let h = s[i - 2];
        if (0xd800..0xdc00).contains(&h) {
            return Some((0x10000 + (((h as u32) - 0xd800) << 10) + ((l as u32) - 0xdc00), i - 2));
        }
    }
    Some((l as u32, i - 1))
}

/// Final_Sigma: Σ at [start, end) lowers to ς iff preceded by a cased letter (skipping
/// case-ignorables) and not followed by one (skipping case-ignorables). Classes are the
/// ones measured in Chromium (ICU: case-ignorable wins over cased).
fn is_final_sigma(s: &[u16], start: usize, end: usize) -> bool {
    let mut j = start;
    let before = loop {
        match cp_before(s, j) {
            None => break false,
            Some((c, pj)) => {
                if in_ranges(tables::PRE_IGNORABLE, c) {
                    j = pj;
                    continue;
                }
                break in_ranges(tables::PRE_CASED, c);
            }
        }
    };
    if !before {
        return false;
    }
    let mut k = end;
    let after = loop {
        if k >= s.len() {
            break false;
        }
        let (c, n) = cp_at(s, k);
        if in_ranges(tables::FOL_IGNORABLE, c) {
            k += n;
            continue;
        }
        break in_ranges(tables::FOL_CASED, c);
    };
    !after
}

/// JS `String.prototype.toLowerCase()` as the pinned Chromium computes it, on UTF-16.
pub fn js_to_lower_case(s: &[u16]) -> Vec<u16> {
    let mut out = Vec::with_capacity(s.len());
    let mut i = 0;
    while i < s.len() {
        let u = s[i];
        if u < 0x80 {
            out.push(if (0x41..=0x5a).contains(&u) { u + 32 } else { u });
            i += 1;
            continue;
        }
        let (c, n) = cp_at(s, i);
        if c == 0x3a3 {
            out.push(if is_final_sigma(s, i, i + n) { 0x3c2 } else { 0x3c3 });
        } else if let Ok(k) = tables::LOWER_MAP.binary_search_by_key(&c, |e| e.0) {
            out.extend_from_slice(tables::LOWER_MAP[k].1);
        } else {
            out.extend_from_slice(&s[i..i + n]);
        }
        i += n;
    }
    out
}

/// cmdk `formatInput`: `toLowerCase().replace(/[\s-]/g, ' ')`.
pub fn format_input(s: &[u16]) -> Vec<u16> {
    let mut v = js_to_lower_case(s);
    for c in v.iter_mut() {
        if is_space(*c) {
            *c = 0x20;
        }
    }
    v
}

// ---------------------------------------------------------------------------
// JS string primitives on UTF-16

/// JS `s.charAt(i)`: out of range (including negative) is `""`, modelled as `None`.
#[inline]
fn char_at(s: &[u16], i: isize) -> Option<u16> {
    if i < 0 {
        None
    } else {
        s.get(i as usize).copied()
    }
}

/// JS `s.indexOf(ch, from)` for a single code unit (`ch` is never `""` here).
#[inline]
fn index_of(s: &[u16], ch: u16, from: usize) -> Option<usize> {
    if from >= s.len() {
        return None;
    }
    s[from..].iter().position(|&c| c == ch).map(|p| p + from)
}

/// Number of matches of a single-unit global regex in JS `s.slice(start, end)` (start,
/// end >= 0). `slice` clamps both ends to the length and yields `""` when end <= start.
#[inline]
fn count_in_slice(s: &[u16], start: usize, end: usize, f: fn(u16) -> bool) -> usize {
    let (a, b) = (start.min(s.len()), end.min(s.len()));
    if b <= a {
        return 0;
    }
    s[a..b].iter().filter(|&&c| f(c)).count()
}

// ---------------------------------------------------------------------------
// The scorer

/// An item preprocessed once per dataset: its UTF-16 units and `formatInput` of them.
#[derive(Debug, Clone)]
pub struct Prepared {
    pub s: Vec<u16>,
    pub ls: Vec<u16>,
}

impl Prepared {
    pub fn new(s: Vec<u16>) -> Self {
        let ls = format_input(&s);
        Prepared { s, ls }
    }
    pub fn from_text(s: &str) -> Self {
        Self::new(s.encode_utf16().collect())
    }
}

/// A query preprocessed once.
#[derive(Debug, Clone)]
pub struct Query {
    pub a: Vec<u16>,
    pub la: Vec<u16>,
}
impl Query {
    pub fn new(a: Vec<u16>) -> Self {
        let la = format_input(&a);
        Query { a, la }
    }
    pub fn from_text(q: &str) -> Self {
        Self::new(q.encode_utf16().collect())
    }
}

/// Reusable scoring state (the memo table, which upstream keeps as an object keyed
/// `"${stringIndex},${abbreviationIndex}"`; a dense table is observationally identical
/// because the memoized function is pure in (stringIndex, abbreviationIndex)).
#[derive(Default)]
pub struct Scorer {
    memo: Vec<f64>,
}

struct Ctx<'a> {
    s: &'a [u16],
    a: &'a [u16],
    ls: &'a [u16],
    la: &'a [u16],
    memo: &'a mut [f64],
}

impl Ctx<'_> {
    fn inner(&mut self, si: usize, ai: usize) -> Result<f64, ScoreError> {
        if ai == self.a.len() {
            return Ok(if si == self.s.len() { SCORE_CONTINUE_MATCH } else { PENALTY_NOT_COMPLETE });
        }
        // lowerAbbreviation.charAt(ai) === "" only when ai ran past the end of the query via
        // the transposition branch (ai > a.len()); upstream then recurses forever -> RangeError.
        let ach = match self.la.get(ai) {
            Some(&c) => c,
            None => return Err(ScoreError::JsRangeError),
        };
        let key = si * self.la.len() + ai;
        let m = self.memo[key];
        if !m.is_nan() {
            return Ok(m);
        }

        let (s, a, ls, la) = (self.s, self.a, self.ls, self.la);
        let mut index = index_of(ls, ach, si);
        let mut high_score = 0.0f64;
        while let Some(idx) = index {
            let mut score = self.inner(idx + 1, ai + 1)?;
            if score > high_score {
                let prev = char_at(s, idx as isize - 1);
                if idx == si {
                    score *= SCORE_CONTINUE_MATCH;
                } else if prev.is_some_and(is_gap) {
                    score *= SCORE_NON_SPACE_WORD_JUMP;
                    let n = count_in_slice(s, si, idx - 1, is_gap);
                    if n > 0 && si > 0 {
                        score *= pow_0999(n).ok_or(ScoreError::TooLong)?;
                    }
                } else if prev.is_some_and(is_space) {
                    score *= SCORE_SPACE_WORD_JUMP;
                    let n = count_in_slice(s, si, idx - 1, is_space);
                    if n > 0 && si > 0 {
                        score *= pow_0999(n).ok_or(ScoreError::TooLong)?;
                    }
                } else {
                    score *= SCORE_CHARACTER_JUMP;
                    if si > 0 {
                        score *= pow_0999(idx - si).ok_or(ScoreError::TooLong)?;
                    }
                }
                if char_at(s, idx as isize) != char_at(a, ai as isize) {
                    score *= PENALTY_CASE_MISMATCH;
                }
            }

            let next = char_at(la, ai as isize + 1);
            let lprev = char_at(ls, idx as isize - 1);
            if (score < SCORE_TRANSPOSITION && lprev == next) || (next == Some(ach) && lprev != Some(ach)) {
                let transposed = self.inner(idx + 1, ai + 2)?;
                if transposed * SCORE_TRANSPOSITION > score {
                    score = transposed * SCORE_TRANSPOSITION;
                }
            }
            if score > high_score {
                high_score = score;
            }
            index = index_of(ls, ach, idx + 1);
        }
        self.memo[key] = high_score;
        Ok(high_score)
    }
}

impl Scorer {
    pub fn new() -> Self {
        Self::default()
    }

    /// `commandScore(item, q, [])` on preprocessed inputs.
    pub fn score(&mut self, item: &Prepared, q: &Query) -> Result<f64, ScoreError> {
        if item.ls.len() > POW_MAX {
            return Err(ScoreError::TooLong);
        }
        if q.a.is_empty() {
            // abbreviationIndex === abbreviation.length at the first call.
            return Ok(if item.s.is_empty() { SCORE_CONTINUE_MATCH } else { PENALTY_NOT_COMPLETE });
        }
        let n = (item.ls.len() + 1) * q.la.len();
        self.memo.clear();
        self.memo.resize(n, f64::NAN);
        let mut ctx = Ctx { s: &item.s, a: &q.a, ls: &item.ls, la: &q.la, memo: &mut self.memo };
        ctx.inner(0, 0)
    }
}

/// `commandScore(item, q, [])` (cmdk 1.1.1 with the frozen POW table), on UTF-16.
pub fn score(item: &[u16], q: &[u16]) -> Result<f64, ScoreError> {
    Scorer::new().score(&Prepared::new(item.to_vec()), &Query::new(q.to_vec()))
}

/// Convenience wrapper over `&str` (which cannot hold lone surrogates).
pub fn score_str(item: &str, q: &str) -> Result<f64, ScoreError> {
    Scorer::new().score(&Prepared::from_text(item), &Query::from_text(q))
}

// ---------------------------------------------------------------------------
// rank()

/// A dataset prepared for ranking; ids are indices.
pub struct Dataset {
    pub items: Vec<Prepared>,
}

impl Dataset {
    pub fn new(items: Vec<Vec<u16>>) -> Self {
        Dataset { items: items.into_iter().map(Prepared::new).collect() }
    }
    pub fn from_strs<S: AsRef<str>>(items: &[S]) -> Self {
        Dataset { items: items.iter().map(|s| Prepared::from_text(s.as_ref())).collect() }
    }
    pub fn len(&self) -> usize {
        self.items.len()
    }
    pub fn is_empty(&self) -> bool {
        self.items.is_empty()
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct Ranking {
    /// Matching ids in normative order.
    pub ids: Vec<u32>,
    /// Scores parallel to `ids`; `None` for the empty query (nothing is scored).
    pub scores: Option<Vec<f64>>,
    /// `ids[0]`, or `None` when nothing matches.
    pub selected: Option<u32>,
}

/// §2.2: `q` empty -> all ids in dataset order. Otherwise ids with score > 0, ordered by
/// score descending (exact binary64 comparison), then id ascending. No trim, no normalization.
pub fn rank(ds: &Dataset, q: &[u16]) -> Result<Ranking, ScoreError> {
    rank_with(&mut Scorer::new(), ds, &Query::new(q.to_vec()))
}

pub fn rank_with(scorer: &mut Scorer, ds: &Dataset, q: &Query) -> Result<Ranking, ScoreError> {
    if q.a.is_empty() {
        let ids: Vec<u32> = (0..ds.items.len() as u32).collect();
        let selected = ids.first().copied();
        return Ok(Ranking { ids, scores: None, selected });
    }
    let mut hits: Vec<(f64, u32)> = Vec::new();
    for (i, it) in ds.items.iter().enumerate() {
        let s = scorer.score(it, q)?;
        if s > 0.0 {
            hits.push((s, i as u32));
        }
    }
    // Scores are finite and > 0, so partial_cmp is total here.
    hits.sort_unstable_by(|x, y| y.0.partial_cmp(&x.0).unwrap().then(x.1.cmp(&y.1)));
    let ids: Vec<u32> = hits.iter().map(|h| h.1).collect();
    let selected = ids.first().copied();
    Ok(Ranking { ids, scores: Some(hits.iter().map(|h| h.0).collect()), selected })
}

/// Runs of exactly equal scores in a ranking: `(score bits, count)`.
pub fn tie_groups(scores: &[f64]) -> Vec<(u64, usize)> {
    let mut out: Vec<(u64, usize)> = Vec::new();
    for &s in scores {
        match out.last_mut() {
            Some((b, n)) if f64::from_bits(*b) == s => *n += 1,
            _ => out.push((s.to_bits(), 1)),
        }
    }
    out
}

/// IEEE-754 bits as 16 lowercase hex digits (same format as ranker-ts `bitsHex`).
pub fn bits_hex(x: f64) -> String {
    format!("{:016x}", x.to_bits())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn char_at_out_of_range_is_none() {
        let s = [0x61u16, 0x62];
        assert_eq!(char_at(&s, -1), None);
        assert_eq!(char_at(&s, 2), None);
        assert_eq!(char_at(&s, 1), Some(0x62));
        // "" === "" in JS: two out-of-range charAt compare equal
        assert_eq!(char_at(&s, -1), char_at(&s, 5));
    }

    #[test]
    fn slice_clamps() {
        let s: Vec<u16> = "a/b/c".encode_utf16().collect();
        assert_eq!(count_in_slice(&s, 0, 5, is_gap), 2);
        assert_eq!(count_in_slice(&s, 3, 1, is_gap), 0); // end < start -> ""
        assert_eq!(count_in_slice(&s, 2, 2, is_gap), 0); // empty
        assert_eq!(count_in_slice(&s, 1, 99, is_gap), 2); // end clamped
        assert_eq!(count_in_slice(&s, 9, 12, is_gap), 0); // start beyond length
    }

    #[test]
    fn index_of_units() {
        let s: Vec<u16> = "a😀b".encode_utf16().collect();
        assert_eq!(index_of(&s, 0xde00, 0), Some(2));
        assert_eq!(index_of(&s, 0x61, 1), None);
        assert_eq!(index_of(&s, 0x61, 10), None);
    }
}
