# Spikes for workstream 05

Throwaway experiments behind the findings in [05-software-foundations.md](../05-software-foundations.md). They are not production code.

- `bench.mjs`: runs cmdk's `commandScore` over 50k synthetic items to measure scoring cost and how large the groups of tied scores are.
- `pathdep.mjs`: real cmdk 1.1.1 on React 19 in jsdom. Shows that result order depends on typing history (a pasted query and a query typed key by key rank tied items differently).
- `gen_fixture.mjs`: writes `fixture.json` (not committed; regenerate it), which holds items, queries, V8 `Math.pow(0.999,k)` bit patterns and cmdk score bits.
- `cmdkscore/`: Rust port of the scorer, checked against `fixture.json`. It is bit-exact with the V8 `pow` table and gives 596 mismatches in 500k pairs with `powf`.
- `rs/`: small check of `powf` against V8 `pow`, with its output in `rs.txt`.

Run with `npm install && node gen_fixture.mjs && (cd cmdkscore && cargo run --release)`.
