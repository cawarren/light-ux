// Engine probe: run identically in Chromium (page.evaluate) and in Node.
// Must be self-contained (it is serialized with Function.prototype.toString).
export function engineProbe(powMax: number) {
  const hex = (x: number) => {
    const dv = new DataView(new ArrayBuffer(8));
    dv.setFloat64(0, x);
    return dv.getBigUint64(0).toString(16).padStart(16, '0');
  };
  const pow: string[] = [];
  for (let n = 0; n <= powMax; n++) pow.push(hex(Math.pow(0.999, n)));

  const units = (s: string) => {
    const u: number[] = [];
    for (let i = 0; i < s.length; i++) u.push(s.charCodeAt(i));
    return u;
  };
  // Context-free lowercase map: String.fromCodePoint(c).toLowerCase() where != identity.
  const lower: [number, number[]][] = [];
  for (let c = 0; c <= 0x10ffff; c++) {
    const s = String.fromCodePoint(c);
    const l = s.toLowerCase();
    if (l !== s) lower.push([c, units(l)]);
  }
  // Final_Sigma context classes, measured empirically (ICU semantics: a code point
  // that is case-ignorable is skipped even if it is also cased).
  //   pre: "c Σ"   -> ς  => c counts as a cased letter before Σ
  //        "A c Σ" -> ς (and not above) => c is skipped (case-ignorable) before Σ
  //   fol: "A Σ c"   -> σ  => c counts as a cased letter after Σ
  //        "A Σ c A" -> σ (and not above) => c is skipped after Σ
  const SIG = 0x3c3, FSIG = 0x3c2;
  const ranges = () => ({ list: [] as [number, number][], add(c: number) {
    const l = this.list; const last = l[l.length - 1];
    if (last && last[1] === c - 1) last[1] = c; else l.push([c, c]);
  } });
  const preCased = ranges(), preIgn = ranges(), folCased = ranges(), folIgn = ranges();
  for (let c = 0; c <= 0x10ffff; c++) {
    const s = String.fromCodePoint(c);
    let r = (s + 'Σ').toLowerCase();
    if (r.charCodeAt(r.length - 1) === FSIG) preCased.add(c);
    else {
      r = ('A' + s + 'Σ').toLowerCase();
      if (r.charCodeAt(r.length - 1) === FSIG) preIgn.add(c);
    }
    r = ('AΣ' + s).toLowerCase();
    if (r.charCodeAt(1) === SIG) folCased.add(c);
    else {
      r = ('AΣ' + s + 'A').toLowerCase();
      if (r.charCodeAt(1) === SIG) folIgn.add(c);
    }
  }
  // JS RegExp \s over single UTF-16 code units (non-unicode mode, as cmdk uses it).
  const ws: number[] = [];
  const re = /\s/;
  for (let c = 0; c <= 0xffff; c++) if (re.test(String.fromCharCode(c))) ws.push(c);
  // Sanity samples of multi-character contexts for the final-sigma rule.
  const sigmaSamples = ['ΑΣ', 'ΑΣΑ', 'Σ', 'ΣΑ', "Α'Σ", "Α'Σ'", "Α'Σ'Α", 'Α.Σ', 'ΑΣ.Α', 'ΑΣ Α', 'ΣΣ', 'ΑΣΣ', 'ΆΣ', 'İΣ', 'Α­Σ', '1Σ', 'ΑΣ1']
    .map((s) => [s, s.toLowerCase()]);
  return {
    pow, lower,
    sigma: { preCased: preCased.list, preIgnorable: preIgn.list, folCased: folCased.list, folIgnorable: folIgn.list },
    jsWhitespace: ws,
    sigmaSamples,
  };
}
