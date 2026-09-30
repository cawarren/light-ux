"""Per-trial correctness spot checks against the reference ranker (phase-a §4.5, A9).

Each attributed marker flip carries the query it reflects, the rung's result count and the
text of up to 50 displayed rows starting at the first result (`top50`, read by the probe;
virtualized rungs may show fewer). The reference is `rank(items, q)` from parity/: the Rust
`rank-cli ranked` when built (parity/ranker-rs/target/release/rank-cli), else ranker-ts under
Node 22. Output is streamed and reduced per query, so 50k-item result lists never pile up.

Levels (decided 2026-09-30):
  strict  R2 and above, every key: the displayed prefix equals the reference ids, count equal.
  tie     R1 forward keys (A.first_key, A.seq forward): same score at every displayed rank,
          no duplicates, count equal (05 §0.1 tie-insensitive, restricted to the prefix).
  set     R1 backspace keys (A.clear, A.seq backspace): every displayed item is in the
          reference result set, no duplicates, count equal (stock cmdk mis-orders
          re-appearing items; recorded as an R1 finding, not a failure).
A coalesced key is judged at the level of the key whose state the flip shows.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
RUST_CLI = os.path.join(REPO, "parity", "ranker-rs", "target", "release", "rank-cli")
TS_CLI = os.path.join(REPO, "parity", "ranker-ts", "scripts", "rank-cli.ts")
SIZES = {"1k": 1000, "10k": 10000, "50k": 50000}


def level_for(rung, direction):
    if rung.startswith("r1"):
        return "tie" if direction == "fwd" else "set"
    return "strict"


def ranker_cmd(items_path, queries_path):
    if os.path.exists(RUST_CLI):
        return [RUST_CLI, "ranked", "--items", items_path, "--queries", queries_path], "ladder-rank (rust)"
    node = shutil.which("node")
    if node and os.path.exists(TS_CLI):
        return [node, TS_CLI, "ranked", "--items", items_path, "--queries", queries_path], "ranker-ts (node)"
    return None, None


def reference(items_path, needs):
    """needs: {query: set(candidate ids)}. Returns ({query: ref}, impl) where ref has count,
    top ids/scores (first 50) and the score (or None = not a result) of each candidate id."""
    qlist = sorted(needs)
    tmp = tempfile.mkdtemp(prefix="ladder-rank-")
    qpath = os.path.join(tmp, "queries.json")
    with open(qpath, "w") as f:
        json.dump({"schema": 1, "queries": [{"qid": k, "q": q} for k, q in enumerate(qlist)]}, f)
    cmd, impl = ranker_cmd(items_path, qpath)
    if cmd is None:
        shutil.rmtree(tmp, ignore_errors=True)
        return None, None
    out = {}
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1 << 20)
    for line in proc.stdout:
        if not line.strip():
            continue
        r = json.loads(line)
        q = qlist[int(r["qid"])]
        if "error" in r:
            out[q] = {"error": r["error"]}
            continue
        ids, scores = r["ids"], r.get("scores")
        pos = {i: k for k, i in enumerate(ids)} if needs[q] else {}
        out[q] = {"count": r["count"], "top_ids": ids[:50],
                  "top_scores": scores[:50] if scores is not None else None,
                  "cand": {c: ((scores[pos[c]] if scores is not None else "all") if c in pos else None)
                           for c in needs[q]}}
    proc.wait()
    shutil.rmtree(tmp, ignore_errors=True)
    if proc.returncode not in (0, None):
        raise RuntimeError("ranker failed (%s): %s" % (impl, proc.stderr.read()[-500:]))
    return out, impl


def verdict(flip, ref, id_of, level):
    """(ok, reason) for one flip at one level."""
    if ref is None:
        return None, "no_reference"
    if "error" in ref:
        return None, "reference_" + ref["error"]
    top = flip.get("top50") or []
    cand = [id_of.get(t) for t in top]
    if any(c is None for c in cand):
        return False, "row_not_in_dataset"
    if len(set(cand)) != len(cand):
        return False, "duplicate_rows"
    count = flip.get("count")
    if count is not None and count != ref["count"]:
        return False, "count %s != %s" % (count, ref["count"])
    n = len(cand)
    if n > ref["count"]:
        return False, "more_rows_than_results"
    if ref["count"] > 0 and n == 0:
        return False, "no_rows_displayed"
    if level == "strict":
        return (cand == ref["top_ids"][:n]), ("ok" if cand == ref["top_ids"][:n] else "order")
    if level == "set" or ref["top_scores"] is None:       # q == "": every item, no scores
        bad = [c for c in cand if ref["cand"].get(c) is None]
        return (not bad), ("ok" if not bad else "not_in_result_set")
    for k, c in enumerate(cand):
        if ref["cand"].get(c) != ref["top_scores"][k]:
            return False, "score_at_rank_%d" % k
    return True, "ok"


def check_rows(rows, flips_by_seg, dataset_dir):
    """Fill row['correct'], ['correctness_level'], ['correctness_reason']; upgrade status to
    wrong_result. flips_by_seg: {(block_id, seg): [flip, ...]}. Returns a summary dict."""
    by_size = {}
    flip_index = {}
    for (bid, seg), fl in flips_by_seg.items():
        for f in fl:
            flip_index[(bid, seg, f["seq"])] = f
    for r in rows:
        if r.get("flip_seq") is None:
            continue
        by_size.setdefault(r["dataset_size"], []).append(r)
    summary = {"impl": None, "checked": 0, "wrong": 0, "unchecked": 0, "by_level": {}}
    for size, rs in by_size.items():
        items_path = os.path.join(dataset_dir, str(SIZES[size]), "items.json")
        with open(items_path, encoding="utf-8") as f:
            items = json.load(f)["items"]
        id_of = {t: i for i, t in enumerate(items)}
        needs = {}
        for r in rs:
            f = flip_index.get((r["block_id"], r["seg"], r["flip_seq"]))
            if f is None:
                continue
            needs.setdefault(f["query"], set()).update(i for i in (id_of.get(t) for t in f.get("top50") or []) if i is not None)
        refs, impl = reference(items_path, needs)
        summary["impl"] = impl
        row_by_key = {(r["block_id"], r["seg"], r["key_idx"]): r for r in rs}
        for r in rs:
            f = flip_index.get((r["block_id"], r["seg"], r["flip_seq"]))
            if f is None or refs is None:
                summary["unchecked"] += 1
                continue
            owner = row_by_key.get((r["block_id"], r["seg"], r["key_idx"] + (r.get("attributed_offset") or 0)), r)
            lvl = level_for(r["rung"], owner["direction"])
            ok, why = verdict(f, refs.get(f["query"]), id_of, lvl)
            r["correct"], r["correctness_level"], r["correctness_reason"] = ok, lvl, why
            summary["by_level"][lvl] = summary["by_level"].get(lvl, 0) + 1
            if ok is None:
                summary["unchecked"] += 1
                continue
            summary["checked"] += 1
            if not ok:
                summary["wrong"] += 1
                if r["status"] in ("ok", "coalesced"):
                    r["status"] = "wrong_result"
    return summary
