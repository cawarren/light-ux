// Frozen tables generated in the pinned Chromium by scripts/gen-tables.ts.
// See parity/tables/*.json for provenance metadata.
import powJson from '../../tables/pow0999.json' with { type: 'json' };
import lowerJson from '../../tables/lowercase.json' with { type: 'json' };

function fromBitsHex(h: string): number {
  const dv = new DataView(new ArrayBuffer(8));
  dv.setBigUint64(0, BigInt('0x' + h));
  return dv.getFloat64(0);
}

/** POW_0999[n] === Math.pow(0.999, n) as evaluated by the pinned Chromium, n = 0..POW_MAX. */
export const POW_0999: readonly number[] = Object.freeze(powJson.bitsHex.map(fromBitsHex));
export const POW_MAX: number = powJson.nMax;
export const TABLE_ENGINE = powJson.engine;

export const LOWERCASE_TABLE = lowerJson as unknown as {
  map: [number, number[]][];
  sigma: { preCased: [number, number][]; preIgnorable: [number, number][]; folCased: [number, number][]; folIgnorable: [number, number][] };
  jsWhitespace: number[];
  engine: { chromium: string };
};
