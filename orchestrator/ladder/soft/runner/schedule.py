"""Seeded session plan: block order, per-block trial schedules, segments (phase-a §3.3, §4.5).

Everything random derives from one recorded session seed (03 §0 principle 5). The plan is
written to plan.json before the first block runs and is the authoritative record of what was
scheduled; the injector records what actually happened next to it.

Trials (phase-a §4.5, decisions 2026-09-30):
  A.first_key  one key into the empty palette            after settle + U[50,250] ms
  A.clear      Backspace back to empty                   after settle + U[50,250] ms (*)
  A.seq        20-char typeable query at U[90,117] ms key-to-key, then 20 Backspaces at the
               same cadence (seq_pos 0..39); only seq_pos 0 waits for settle + U[50,250] ms.
  Hold (key-down to key-up) U[30,60] ms, always shorter than the auto-repeat delay (D-B 17).
(*) phase-a §4.5 puts the U[50,250] gap only after A.clear. We also put one before A.clear:
    settle is detected in a rAF callback, so pressing Backspace right at settle would lock
    A.clear to the frame phase (the aliasing 03 §2.2 warns about).

Blocks: one per (rung, size) condition and repetition; order = seeded permutation, then its
reverse (ABBA), so every condition appears in >= 2 blocks when reps >= 2 and drift cancels.
Each block starts with warm-up first_key trials (flagged warmup, excluded from stats), then
the measured units in seeded random order. A block is cut into segments of whole units (at
most max_keys keys each); the page buffers are read back and the clock re-synced between
segments, never inside one.
Standard library only (runs on the device under test).
"""
from __future__ import annotations

import hashlib
import json
import random

from . import keymap

SCHEDULE_VERSION = 1
GAP_MS = (50.0, 250.0)        # inter-trial gap, counted from the page-reported settle
CADENCE_MS = (90.0, 117.0)    # key-down to key-down inside A.seq (README S4, 03 §2.2)
HOLD_MS = (30.0, 60.0)        # key-down to key-up
SEQ_LEN = 20


def _u(r: random.Random, lo_hi) -> float:
    """Uniform continuous draw at 1 us resolution (03 §2.1: no integer-ms lattice)."""
    lo, hi = lo_hi
    return round(r.uniform(lo, hi), 3)


def load_query_pools(path: str):
    """First characters (with frequency) and 20-char typeable queries from queries.json."""
    with open(path, "rb") as f:
        raw = f.read()
    data = json.loads(raw)
    firsts, seqs = [], []
    for q in data["queries"]:
        s = q["q"]
        if not s or not q.get("typeable"):
            continue
        if s[0] in keymap.TYPEABLE:
            firsts.append(s[0])
        if q.get("class") == "ascii-20" and len(s) == SEQ_LEN and set(s) <= keymap.TYPEABLE:
            seqs.append({"qid": q["qid"], "q": s})
    if not firsts or not seqs:
        raise ValueError("query set %s has no typeable first characters or ascii-20 queries" % path)
    return {"first_chars": firsts, "seq_queries": seqs,
            "sha256": hashlib.sha256(raw).hexdigest(), "seed_id": data.get("seedId")}


def split_counts(n: int, parts: int):
    """n trials over `parts` blocks, as even as possible, larger shares first."""
    base, extra = divmod(n, parts)
    return [base + (1 if k < extra else 0) for k in range(parts)]


def block_order(conditions, reps: int, seed: int):
    r = random.Random(seed)
    perm = list(conditions)
    r.shuffle(perm)
    order = []
    for k in range(reps):
        order += perm if k % 2 == 0 else perm[::-1]
    return order


def _first_key_unit(r, uid, pools, warmup):
    ch = r.choice(pools["first_chars"])
    return {"unit": uid, "type": "first_key", "warmup": warmup, "query": ch, "keys": [
        {"kind": "A.first_key", "dir": "fwd", "seq_pos": None, "key": ch, "expect": ch,
         "after": "settle", "gap_ms": _u(r, GAP_MS), "hold_ms": _u(r, HOLD_MS)},
        {"kind": "A.clear", "dir": "back", "seq_pos": None, "key": keymap.BACKSPACE, "expect": "",
         "after": "settle", "gap_ms": _u(r, GAP_MS), "hold_ms": _u(r, HOLD_MS)},
    ]}


def _seq_unit(r, uid, pools, warmup):
    q = r.choice(pools["seq_queries"])
    text = q["q"]
    keys, value = [], ""
    presses = list(text) + [keymap.BACKSPACE] * len(text)
    for pos, k in enumerate(presses):
        value = keymap.apply(value, k)
        keys.append({"kind": "A.seq", "dir": "back" if k == keymap.BACKSPACE else "fwd", "seq_pos": pos,
                     "key": k, "expect": value,
                     "after": "settle" if pos == 0 else "prev_down",
                     "gap_ms": _u(r, GAP_MS) if pos == 0 else _u(r, CADENCE_MS),
                     "hold_ms": _u(r, HOLD_MS)})
    return {"unit": uid, "type": "seq", "warmup": warmup, "query": text, "qid": q["qid"], "keys": keys}


def plan_block(block_id, cond, n_first, n_seq, n_warmup, pools, seed, max_keys=40):
    r = random.Random(seed)
    units = [_first_key_unit(r, "w%d" % k, pools, True) for k in range(n_warmup)]
    measured = ["first_key"] * n_first + ["seq"] * n_seq
    r.shuffle(measured)
    for k, kind in enumerate(measured):
        units.append((_first_key_unit if kind == "first_key" else _seq_unit)(r, "u%d" % k, pools, False))
    # number the keys and pack whole units into segments
    segments, cur, i = [], [], 0
    for u in units:
        for key in u["keys"]:
            key["i"] = i
            key["unit"] = u["unit"]
            key["warmup"] = u["warmup"]
            i += 1
        if cur and sum(len(x["keys"]) for x in cur) + len(u["keys"]) > max_keys:
            segments.append(cur)
            cur = []
        cur.append(u)
    if cur:
        segments.append(cur)
    return {"block_id": block_id, "rung": cond["rung"], "size": cond["size"], "seed": seed,
            "counts": {"first_key": n_first, "seq": n_seq, "warmup": n_warmup, "keys": i},
            "segments": [{"seg": k, "units": [u["unit"] for u in seg],
                          "keys": [key for u in seg for key in u["keys"]]}
                         for k, seg in enumerate(segments)]}


def plan_session(seed: int, rungs, sizes, pools, reps=2, first_key=300, seq=5, warmup=50,
                 max_keys=40):
    """Full plan. `first_key` and `seq` are per condition (rung x size), split over `reps` blocks."""
    r = random.Random(seed)
    conditions = [{"rung": ru, "size": s} for ru in rungs for s in sizes]
    order_seed = r.getrandbits(63)
    order = block_order(conditions, reps, order_seed)
    fk = {(c["rung"], c["size"]): split_counts(first_key, reps) for c in conditions}
    sq = {(c["rung"], c["size"]): split_counts(seq, reps) for c in conditions}
    seen = {}
    blocks = []
    for bi, cond in enumerate(order):
        key = (cond["rung"], cond["size"])
        rep = seen.get(key, 0)
        seen[key] = rep + 1
        bseed = r.getrandbits(63)
        bid = "b%02d_%s_%s" % (bi + 1, cond["rung"], cond["size"])
        b = plan_block(bid, cond, fk[key][rep], sq[key][rep], warmup, pools, bseed, max_keys)
        b["rep"] = rep
        blocks.append(b)
    return {"schedule_version": SCHEDULE_VERSION, "seed": seed, "order_seed": order_seed,
            "params": {"rungs": list(rungs), "sizes": list(sizes), "reps": reps, "first_key": first_key,
                       "seq": seq, "warmup": warmup, "max_keys": max_keys, "gap_ms": GAP_MS,
                       "cadence_ms": CADENCE_MS, "hold_ms": HOLD_MS, "seq_len": SEQ_LEN},
            "queries": {"sha256": pools["sha256"], "seed_id": pools.get("seed_id")},
            "block_order": [b["block_id"] for b in blocks], "blocks": blocks}
