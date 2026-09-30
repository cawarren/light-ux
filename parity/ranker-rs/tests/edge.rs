//! Hand-written edge goldens shared with ranker-ts (parity/goldens/edge-cases.json).
use ladder_rank::*;
use serde_json::Value;

fn units(v: &Value) -> Vec<u16> {
    match v {
        Value::String(s) => s.encode_utf16().collect(),
        Value::Object(o) => o["units"].as_array().unwrap().iter().map(|x| x.as_u64().unwrap() as u16).collect(),
        _ => panic!("bad string {v}"),
    }
}

fn golden() -> Value {
    let p = concat!(env!("CARGO_MANIFEST_DIR"), "/../goldens/edge-cases.json");
    serde_json::from_str(&std::fs::read_to_string(p).unwrap()).unwrap()
}

#[test]
fn edge_scores() {
    let g = golden();
    let mut n = 0;
    for c in g["cases"].as_array().unwrap() {
        let r = score(&units(&c["item"]), &units(&c["q"]));
        if let Some(e) = c.get("error") {
            assert_eq!(r, Err(ScoreError::JsRangeError), "{}", c["id"]);
            assert_eq!(e, "RangeError");
        } else {
            assert_eq!(bits_hex(r.unwrap()), c["bits"].as_str().unwrap(), "case {} ({})", c["id"], c["expr"]);
        }
        n += 1;
    }
    assert!(n >= 60);
}

#[test]
fn edge_ranks() {
    let g = golden();
    for r in g["rankCases"].as_array().unwrap() {
        let items: Vec<Vec<u16>> = r["items"].as_array().unwrap().iter().map(units).collect();
        let ds = Dataset::new(items);
        let out = rank(&ds, &units(&r["q"])).unwrap();
        let want: Vec<u32> = r["ids"].as_array().unwrap().iter().map(|x| x.as_u64().unwrap() as u32).collect();
        assert_eq!(out.ids, want, "{}", r["id"]);
        assert_eq!(out.selected, r["selected"].as_u64().map(|x| x as u32), "{}", r["id"]);
        if let Some(tg) = r.get("tieGroups") {
            let got: Vec<Value> = tie_groups(out.scores.as_ref().unwrap())
                .into_iter()
                .map(|(b, n)| serde_json::json!([format!("{b:016x}"), n]))
                .collect();
            assert_eq!(&Value::Array(got), tg, "{}", r["id"]);
        }
        if r["q"] == "" {
            assert!(out.scores.is_none());
        }
    }
}

#[test]
fn js_whitespace_matches_chromium_table() {
    let t = js_whitespace_table();
    for c in 0..=0xffffu16 {
        assert_eq!(is_js_whitespace(c), t.binary_search(&c).is_ok(), "U+{c:04X}");
    }
    // and differs from Rust's notion exactly where documented
    assert!(char::from_u32(0x85).unwrap().is_whitespace() && !is_js_whitespace(0x85));
    assert!(!char::from_u32(0xfeff).unwrap().is_whitespace() && is_js_whitespace(0xfeff));
}

#[test]
fn lowercase_basics() {
    let l = |s: &str| String::from_utf16(&js_to_lower_case(&s.encode_utf16().collect::<Vec<_>>())).unwrap();
    assert_eq!(l("ΑΣ"), "ας");
    assert_eq!(l("ΑΣΑ"), "ασα");
    assert_eq!(l("Σ"), "σ");
    assert_eq!(l("Α'Σ"), "α'ς");
    assert_eq!(l("ΑΣ'Α"), "ασ'α");
    assert_eq!(l("ΑΣ Α"), "ας α");
    assert_eq!(l("İ"), "i\u{307}");
    assert_eq!(l("\u{10400}"), "\u{10428}");
    assert_eq!(l("ẞ"), "ß");
    assert_eq!(l("ÖFFNEN"), "öffnen");
    // lone surrogates pass through
    assert_eq!(js_to_lower_case(&[0xd800, 0x41]), vec![0xd800, 0x61]);
}

#[test]
fn too_long_is_an_error_not_a_panic() {
    let item: Vec<u16> = std::iter::repeat_n(0x61, POW_MAX + 1).collect();
    assert_eq!(score(&item, &[0x61]), Err(ScoreError::TooLong));
    let item: Vec<u16> = std::iter::repeat_n(0x61, POW_MAX).collect();
    assert!(score(&item, &[0x61]).is_ok());
}

#[test]
fn pow_table_vs_powf_is_informational() {
    // The whole point of the table: f64::powf disagrees with Chromium's Math.pow somewhere.
    let diffs = (0..=POW_MAX).filter(|&n| 0.999f64.powf(n as f64) != pow_0999(n).unwrap()).count();
    eprintln!("f64::powf differs from Chromium POW_0999 on {diffs} of {} exponents", POW_MAX + 1);
    assert_eq!(pow_0999(0), Some(1.0));
    assert_eq!(pow_0999(1), Some(0.999));
}
