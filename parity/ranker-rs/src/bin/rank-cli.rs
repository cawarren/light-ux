//! rank-cli: JSONL front end for ladder-rank (mirrors parity/ranker-ts/scripts/rank-cli.ts).
//!
//!   rank-cli scores  --items items.json --queries queries.json   per query: all item score bits
//!   rank-cli ranked  --items items.json --queries queries.json   per query: full ranked ids + score bits
//!   rank-cli golden  --items items.json --queries queries.json   per query: golden line (§2.3)
//!   rank-cli bench   --items items.json --queries queries.json [--repeat N]
//!   rank-cli fuzz-serve                                          stdin {"i":[u16],"q":[u16]} -> stdout {"s":hex}|{"err":..}
//!
//! Output goes to stdout (or --out FILE).
use ladder_rank::{bits_hex, rank_with, tie_groups, Dataset, Prepared, Query, Scorer};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::io::{BufRead, BufWriter, Write};
use std::time::Instant;

fn arg(args: &[String], name: &str) -> Option<String> {
    args.iter().position(|a| a == name).and_then(|i| args.get(i + 1).cloned())
}

fn load_items(path: &str) -> Vec<String> {
    let v: Value = serde_json::from_str(&std::fs::read_to_string(path).expect("read items")).expect("items json");
    assert_eq!(v["schema"], 1, "items.json schema must be 1");
    v["items"].as_array().expect("items array").iter().map(|x| x.as_str().expect("item string").to_owned()).collect()
}

fn load_queries(path: &str) -> Vec<(Value, String)> {
    let v: Value = serde_json::from_str(&std::fs::read_to_string(path).expect("read queries")).expect("queries json");
    assert_eq!(v["schema"], 1, "queries.json schema must be 1");
    v["queries"]
        .as_array()
        .expect("queries array")
        .iter()
        .map(|x| (x["qid"].clone(), x["q"].as_str().expect("q string").to_owned()))
        .collect()
}

fn ids_sha256(ids: &[u32]) -> String {
    let mut h = Sha256::new();
    for (k, id) in ids.iter().enumerate() {
        if k > 0 {
            h.update(b",");
        }
        h.update(id.to_string().as_bytes());
    }
    h.finalize().iter().map(|b| format!("{b:02x}")).collect()
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let mode = args.first().cloned().unwrap_or_default();
    let stdout = std::io::stdout();
    let mut out: Box<dyn Write> = match arg(&args, "--out") {
        Some(p) => Box::new(BufWriter::new(std::fs::File::create(p).expect("create out"))),
        None => Box::new(BufWriter::new(stdout.lock())),
    };

    if mode == "fuzz-serve" {
        let mut scorer = Scorer::new();
        for line in std::io::stdin().lock().lines() {
            let line = line.expect("stdin");
            if line.is_empty() {
                continue;
            }
            let v: Value = serde_json::from_str(&line).expect("fuzz json");
            let units = |k: &str| -> Vec<u16> { v[k].as_array().unwrap().iter().map(|x| x.as_u64().unwrap() as u16).collect() };
            let item = Prepared::new(units("i"));
            let q = Query::new(units("q"));
            let lower = ladder_rank::js_to_lower_case(&item.s);
            let r = match scorer.score(&item, &q) {
                Ok(s) => json!({"s": bits_hex(s), "l": lower}),
                Err(e) => json!({"err": e.to_string(), "l": lower}),
            };
            writeln!(out, "{r}").unwrap();
        }
        out.flush().unwrap();
        return;
    }

    let items_path = arg(&args, "--items").expect("--items");
    let queries_path = arg(&args, "--queries").expect("--queries");
    let items = load_items(&items_path);
    let queries = load_queries(&queries_path);
    let t0 = Instant::now();
    let ds = Dataset::from_strs(&items);
    let prep_ms = t0.elapsed().as_secs_f64() * 1e3;
    let mut scorer = Scorer::new();

    match mode.as_str() {
        "scores" => {
            for (qid, q) in &queries {
                let q = Query::from_text(q);
                let mut bits = Vec::with_capacity(ds.len());
                let mut err = None;
                for it in &ds.items {
                    match scorer.score(it, &q) {
                        Ok(s) => bits.push(bits_hex(s)),
                        Err(e) => {
                            err = Some(e);
                            break;
                        }
                    }
                }
                let line = match err {
                    Some(e) => json!({"qid": qid, "error": e.to_string()}),
                    None => json!({"qid": qid, "scores": bits}),
                };
                writeln!(out, "{line}").unwrap();
            }
        }
        "ranked" | "golden" => {
            for (qid, q) in &queries {
                let r = match rank_with(&mut scorer, &ds, &Query::from_text(q)) {
                    Ok(r) => r,
                    Err(e) => {
                        writeln!(out, "{}", json!({"qid": qid, "error": e.to_string()})).unwrap();
                        continue;
                    }
                };
                let line = if mode == "ranked" {
                    json!({
                        "qid": qid, "count": r.ids.len(), "selected": r.selected, "ids": r.ids,
                        "scores": r.scores.as_ref().map(|s| s.iter().map(|&x| bits_hex(x)).collect::<Vec<_>>()),
                    })
                } else {
                    let top: Vec<Value> = r
                        .ids
                        .iter()
                        .take(100)
                        .enumerate()
                        .map(|(k, &id)| json!([id, r.scores.as_ref().map(|s| bits_hex(s[k]))]))
                        .collect();
                    let tg: Vec<Value> = r
                        .scores
                        .as_ref()
                        .map(|s| tie_groups(s).into_iter().map(|(b, n)| json!([format!("{b:016x}"), n])).collect())
                        .unwrap_or_default();
                    json!({"qid": qid, "count": r.ids.len(), "idsSha256": ids_sha256(&r.ids), "selected": r.selected, "top": top, "tieGroups": tg})
                };
                writeln!(out, "{line}").unwrap();
            }
        }
        "bench" => {
            let repeat: usize = arg(&args, "--repeat").map(|s| s.parse().unwrap()).unwrap_or(1);
            let mut times = Vec::new();
            let mut matched = 0usize;
            for _ in 0..repeat {
                for (_, q) in &queries {
                    let t = Instant::now();
                    let q = Query::from_text(q);
                    // upstream-RangeError queries are not timed (same as the TS bench)
                    if let Ok(r) = rank_with(&mut scorer, &ds, &q) {
                        matched += r.ids.len();
                        times.push(t.elapsed().as_secs_f64() * 1e3);
                    }
                }
            }
            times.sort_by(|a, b| a.partial_cmp(b).unwrap());
            let mean = times.iter().sum::<f64>() / times.len() as f64;
            let pct = |p: f64| times[((times.len() - 1) as f64 * p).round() as usize];
            let res = json!({
                "impl": "ladder-rank (Rust, release)", "items": ds.len(), "queries": queries.len(), "repeat": repeat,
                "prepareMs": prep_ms, "meanMs": mean, "p50Ms": pct(0.5), "p95Ms": pct(0.95), "maxMs": pct(1.0), "matched": matched
            });
            writeln!(out, "{res}").unwrap();
        }
        _ => {
            eprintln!("usage: rank-cli <scores|ranked|golden|bench|fuzz-serve> --items F --queries F [--out F]");
            std::process::exit(2);
        }
    }
    out.flush().unwrap();
}
