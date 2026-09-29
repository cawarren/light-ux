// Spike: bit-exact Rust port of cmdk 1.1.1 command-score.ts, operating on UTF-16 code units.
use std::collections::HashMap;

const SCORE_CONTINUE_MATCH: f64 = 1.0;
const SCORE_SPACE_WORD_JUMP: f64 = 0.9;
const SCORE_NON_SPACE_WORD_JUMP: f64 = 0.8;
const SCORE_CHARACTER_JUMP: f64 = 0.17;
const SCORE_TRANSPOSITION: f64 = 0.1;
const PENALTY_CASE_MISMATCH: f64 = 0.9999;
const PENALTY_NOT_COMPLETE: f64 = 0.99;

// JS /\s/ (ES2023) plus '-' ; Rust char::is_whitespace differs (U+0085 yes, U+FEFF no).
fn is_js_space(c: u16) -> bool {
    matches!(c, 0x09..=0x0d | 0x20 | 0xa0 | 0x1680 | 0x2000..=0x200a | 0x2028 | 0x2029 | 0x202f | 0x205f | 0x3000 | 0xfeff)
}
fn is_space(c: u16) -> bool { c == b'-' as u16 || is_js_space(c) }
fn is_gap(c: u16) -> bool { matches!(c as u32, 0x5c | 0x2f | 0x5f | 0x2b | 0x2e | 0x23 | 0x22 | 0x40 | 0x5b | 0x28 | 0x7b | 0x26) }

struct Ctx<'a> { s: &'a [u16], a: &'a [u16], ls: &'a [u16], la: &'a [u16], pow: &'a [f64], memo: HashMap<(usize, usize), f64> }

// JS charAt: out of range -> "" ; model as None
fn at(v: &[u16], i: isize) -> Option<u16> { if i < 0 || i as usize >= v.len() { None } else { Some(v[i as usize]) } }
fn index_of(v: &[u16], c: Option<u16>, from: usize) -> isize {
    // lowerString.indexOf("", from) would return from (clamped) -- only reachable if abbreviation shorter than its lowercase; spec excludes.
    let c = match c { Some(c) => c, None => return if from <= v.len() { from as isize } else { v.len() as isize } };
    for i in from..v.len() { if v[i] == c { return i as isize } } -1
}
fn count(v: &[u16], start: usize, end: isize, f: fn(u16) -> bool) -> usize {
    // String.prototype.slice(start, end) with end < start => ""
    if end <= start as isize { return 0 } v[start..(end as usize).min(v.len())].iter().filter(|&&c| f(c)).count()
}

fn inner(c: &mut Ctx, si: usize, ai: usize) -> f64 {
    if ai == c.a.len() { return if si == c.s.len() { SCORE_CONTINUE_MATCH } else { PENALTY_NOT_COMPLETE } }
    if let Some(&m) = c.memo.get(&(si, ai)) { return m }
    let ach = at(c.la, ai as isize);
    let mut index = index_of(c.ls, ach, si);
    let mut high = 0.0f64;
    while index >= 0 {
        let idx = index as usize;
        let mut score = inner(c, idx + 1, ai + 1);
        if score > high {
            if idx == si { score *= SCORE_CONTINUE_MATCH; }
            else if at(c.s, index - 1).map_or(false, is_gap) {
                score *= SCORE_NON_SPACE_WORD_JUMP;
                let n = count(c.s, si, index - 1, is_gap);
                if n > 0 && si > 0 { score *= c.pow[n]; }
            } else if at(c.s, index - 1).map_or(false, is_space) {
                score *= SCORE_SPACE_WORD_JUMP;
                let n = count(c.s, si, index - 1, is_space);
                if n > 0 && si > 0 { score *= c.pow[n]; }
            } else {
                score *= SCORE_CHARACTER_JUMP;
                if si > 0 { score *= c.pow[idx - si]; }
            }
            if at(c.s, index) != at(c.a, ai as isize) { score *= PENALTY_CASE_MISMATCH; }
        }
        let next = at(c.la, ai as isize + 1);
        if (score < SCORE_TRANSPOSITION && at(c.ls, index - 1) == next)
            || (next == at(c.la, ai as isize) && at(c.ls, index - 1) != at(c.la, ai as isize)) {
            let t = inner(c, idx + 1, ai + 2);
            if t * SCORE_TRANSPOSITION > score { score = t * SCORE_TRANSPOSITION; }
        }
        if score > high { high = score; }
        index = index_of(c.ls, ach, idx + 1);
    }
    c.memo.insert((si, ai), high);
    high
}

fn format_input(s: &str) -> Vec<u16> {
    // String.prototype.toLowerCase (full Unicode, incl. Final_Sigma) then /[\s-]/g -> ' '
    s.to_lowercase().encode_utf16().map(|c| if is_space(c) { 0x20 } else { c }).collect()
}

pub fn command_score(string: &str, abbr: &str, pow: &[f64]) -> f64 {
    let s: Vec<u16> = string.encode_utf16().collect();
    let a: Vec<u16> = abbr.encode_utf16().collect();
    let (ls, la) = (format_input(string), format_input(abbr));
    let mut c = Ctx { s: &s, a: &a, ls: &ls, la: &la, pow, memo: HashMap::new() };
    inner(&mut c, 0, 0)
}

fn main() {
    let v: serde_json::Value = serde_json::from_str(&std::fs::read_to_string("../fixture.json").unwrap()).unwrap();
    let pow: Vec<f64> = v["pow"].as_array().unwrap().iter().map(|x| f64::from_bits(x.as_str().unwrap().parse().unwrap())).collect();
    let powf: Vec<f64> = (0..pow.len()).map(|k| 0.999f64.powf(k as f64)).collect();
    let items: Vec<&str> = v["items"].as_array().unwrap().iter().map(|x| x.as_str().unwrap()).collect();
    let (mut total, mut bad_table, mut bad_powf) = (0, 0, 0);
    for (qi, q) in v["qs"].as_array().unwrap().iter().enumerate() {
        let q = q.as_str().unwrap();
        for (ii, it) in items.iter().enumerate() {
            let want = v["scores"][qi][ii].as_str().unwrap().parse::<u64>().unwrap();
            total += 1;
            if command_score(it, q, &pow).to_bits() != want { bad_table += 1; if bad_table < 4 { eprintln!("mismatch q={q:?} item={it:?}") } }
            if command_score(it, q, &powf).to_bits() != want { bad_powf += 1; }
        }
    }
    println!("pairs={total} mismatches(V8 pow table)={bad_table} mismatches(f64::powf)={bad_powf}");
}
