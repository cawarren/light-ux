// Generates Rust constants from the committed tables in ../tables (generated in the
// pinned Chromium by parity/ranker-ts/scripts/gen-tables.ts).
use std::{env, fmt::Write as _, fs, path::PathBuf};

fn ranges(v: &serde_json::Value) -> String {
    let mut s = String::from("&[");
    for r in v.as_array().unwrap() {
        write!(s, "({},{}),", r[0].as_u64().unwrap(), r[1].as_u64().unwrap()).unwrap();
    }
    s.push(']');
    s
}

fn main() {
    let dir = PathBuf::from(env::var("CARGO_MANIFEST_DIR").unwrap()).join("../tables");
    let pow_path = dir.join("pow0999.json");
    let low_path = dir.join("lowercase.json");
    println!("cargo:rerun-if-changed={}", pow_path.display());
    println!("cargo:rerun-if-changed={}", low_path.display());
    let pow: serde_json::Value = serde_json::from_str(&fs::read_to_string(&pow_path).unwrap()).unwrap();
    let low: serde_json::Value = serde_json::from_str(&fs::read_to_string(&low_path).unwrap()).unwrap();

    let mut out = String::new();
    let bits = pow["bitsHex"].as_array().unwrap();
    assert_eq!(bits.len() as u64, pow["nMax"].as_u64().unwrap() + 1);
    writeln!(out, "/// Chromium version the tables were generated in.").unwrap();
    writeln!(out, "pub const TABLE_CHROMIUM: &str = {:?};", pow["engine"]["chromium"].as_str().unwrap()).unwrap();
    writeln!(out, "pub const POW_MAX: usize = {};", bits.len() - 1).unwrap();
    write!(out, "pub static POW_0999_BITS: [u64; {}] = [", bits.len()).unwrap();
    for b in bits {
        write!(out, "0x{},", b.as_str().unwrap()).unwrap();
    }
    out.push_str("];\n");

    let map = low["map"].as_array().unwrap();
    let mut prev = None;
    write!(out, "pub static LOWER_MAP: [(u32, &[u16]); {}] = [", map.len()).unwrap();
    for e in map {
        let c = e[0].as_u64().unwrap();
        assert!(prev.is_none_or(|p| p < c), "lowercase map must be sorted");
        prev = Some(c);
        let units: Vec<String> = e[1].as_array().unwrap().iter().map(|u| u.as_u64().unwrap().to_string()).collect();
        write!(out, "({},&[{}]),", c, units.join(",")).unwrap();
    }
    out.push_str("];\n");
    for (name, key) in [("PRE_CASED", "preCased"), ("PRE_IGNORABLE", "preIgnorable"), ("FOL_CASED", "folCased"), ("FOL_IGNORABLE", "folIgnorable")] {
        writeln!(out, "pub static {}: &[(u32, u32)] = {};", name, ranges(&low["sigma"][key])).unwrap();
    }
    let ws: Vec<String> = low["jsWhitespace"].as_array().unwrap().iter().map(|u| u.as_u64().unwrap().to_string()).collect();
    writeln!(out, "pub static JS_WHITESPACE_TABLE: &[u16] = &[{}];", ws.join(",")).unwrap();

    fs::write(PathBuf::from(env::var("OUT_DIR").unwrap()).join("tables.rs"), out).unwrap();
}
