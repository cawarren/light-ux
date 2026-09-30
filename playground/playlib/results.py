"""Results files: one JSONL per session (a header line, then one line per answered trial).

Schema (also documented in playground/README.md): every line has
  schema = "ladder.playground/1" and record = "session" | "trial".
Nothing personal is recorded: no user name, host name, IP or full user-agent string.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path

from .config import BLIND_PAIRS, RUNG_NUMBER, RUNGS, REPO, load_prompts
from .schedule import Staircase, blind_schedule, jnd_constant_schedule

SCHEMA = "ladder.playground/1"
KINDS = ("blind", "jnd")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def rung_build_ids() -> dict:
    out = {}
    for r in RUNGS.values():
        f = r.dist / "index.html"
        out[r.id] = {"label": r.label, "dist": str(r.dist.relative_to(REPO)),
                     "build_id": hashlib.sha256(f.read_bytes()).hexdigest()[:16] if f.is_file() else None}
    return out


# ----------------------------------------------------------------------------- validation
_TRIAL_REQUIRED = {
    "schema": str, "record": str, "session_id": str, "kind": str, "seed": str, "trial_index": int,
    "sitting": int, "pair_name": str, "order": str, "rungs": list, "added_ms": list, "dataset_size": str,
    "prompt": str, "typed": list, "no_difference": bool, "rt_ms": (int, float), "intervals": list,
    "t_answer": str,
}
_HEADER_REQUIRED = {"schema": str, "record": str, "session_id": str, "kind": str, "seed": str,
                    "created_at": str, "config": dict}


def validate(rec: dict) -> list[str]:
    """Return a list of schema problems (empty when the record is valid)."""
    errs = []
    req = _HEADER_REQUIRED if rec.get("record") == "session" else _TRIAL_REQUIRED
    for k, t in req.items():
        if k not in rec:
            errs.append(f"missing {k}")
        elif not isinstance(rec[k], t) or (t is int and isinstance(rec[k], bool)):
            errs.append(f"{k}: expected {t}, got {type(rec[k]).__name__}")
    if rec.get("schema") != SCHEMA:
        errs.append(f"schema must be {SCHEMA}")
    if rec.get("kind") not in KINDS:
        errs.append("kind must be blind or jnd")
    if rec.get("record") == "trial":
        if rec.get("answer") not in (1, 2, None):
            errs.append("answer must be 1, 2 or null")
        if rec.get("answer") is None and not rec.get("no_difference"):
            errs.append("answer null only with no_difference")
        if rec.get("confidence") not in (1, 2, 3, None):
            errs.append("confidence must be 1..3 or null")
        if len(rec.get("rungs", [])) != 2 or len(rec.get("intervals", [])) != 2:
            errs.append("rungs and intervals must have two entries")
        if rec.get("correct") not in (True, False, None):
            errs.append("correct must be bool or null")
    return errs


# ----------------------------------------------------------------------------- sessions
class Session:
    """A blind or JND session backed by an append-only JSONL file (resumable)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        lines = [json.loads(s) for s in self.path.read_text().splitlines() if s.strip()]
        if not lines or lines[0].get("record") != "session":
            raise ValueError(f"{path}: no session header")
        self.header = lines[0]
        self.trials = {}
        for rec in lines[1:]:
            if rec.get("record") == "trial":
                self.trials.setdefault(rec["trial_index"], rec)  # first answer wins
        c = self.header["config"]
        self.id, self.kind, self.seed = self.header["session_id"], self.header["kind"], self.header["seed"]
        prompts = c["prompts"]
        self.staircase = None
        if self.kind == "blind":
            pairs = [tuple(p) for p in c["pairs"]]
            self.schedule = blind_schedule(self.seed, c["trials_per_pair"], prompts, pairs)
        elif c.get("method") == "staircase":
            self.schedule = None
            self.staircase = Staircase(self.seed, prompts, c["levels"], c["max_trials"], c["max_reversals"])
        else:
            self.schedule = jnd_constant_schedule(self.seed, c["reps"], prompts, c["levels"])
        self.sitting = max((t.get("sitting", 1) for t in self.trials.values()), default=0) + 1

    # -------------------------------------------------------------- creation
    @classmethod
    def create(cls, results_dir: Path, kind: str, *, seed: str | None = None, size: str = "10k",
               trials_per_pair: int = 40, pairs=BLIND_PAIRS, method: str = "constant", reps: int = 20,
               levels=None, max_trials: int = 60, max_reversals: int = 12, delay_mode: str = "defer",
               allow_no_difference: bool = False, break_every: int = 20, prompts=None,
               allow_editing: bool = False, marker_hidden: bool = True,
               timing: dict | None = None) -> "Session":
        from .config import JND_LEVELS, PROMPT_CLASSES
        if kind not in KINDS:
            raise ValueError(kind)
        seed = seed or secrets.token_hex(4)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        sid = f"{kind}-{stamp}-{secrets.token_hex(2)}"
        # Editing (backspace) is disabled in blind rung pairs by default: R1's cmdk mis-orders the list
        # after a backspace, a content tell. R3 vs R3 (JND) has no such tell, so editing stays on there.
        config = {"dataset_size": size, "allow_no_difference": allow_no_difference, "break_every": break_every,
                  "allow_editing": True if kind == "jnd" else bool(allow_editing), "marker_hidden": bool(marker_hidden),
                  "prompts": list(prompts or load_prompts()), "prompt_classes": list(PROMPT_CLASSES),
                  "timing": timing or {}}
        if kind == "blind":
            config.update(trials_per_pair=trials_per_pair, pairs=[list(p) for p in pairs])
            total = trials_per_pair * len(pairs)
        else:
            lv = list(levels or JND_LEVELS)
            config.update(method=method, delay_mode=delay_mode, levels=lv, rung="r3")
            if method == "staircase":
                config.update(max_trials=max_trials, max_reversals=max_reversals)
                total = None
            else:
                config.update(reps=reps)
                total = reps * len(lv)
        header = {"schema": SCHEMA, "record": "session", "session_id": sid, "kind": kind, "seed": seed,
                  "created_at": now_iso(), "config": config, "total_trials": total, "rungs": rung_build_ids()}
        results_dir = Path(results_dir)
        results_dir.mkdir(parents=True, exist_ok=True)
        path = results_dir / f"{sid}.jsonl"
        path.write_text(json.dumps(header) + "\n")
        return cls(path)

    # -------------------------------------------------------------- progress
    @property
    def config(self) -> dict:
        return self.header["config"]

    @property
    def total(self) -> int | None:
        if self.schedule is not None:
            return len(self.schedule)
        return None

    def _stair_answers(self) -> list[bool]:
        return [bool(self.trials[i].get("correct")) for i in sorted(self.trials)]

    def next_trial(self) -> dict | None:
        if self.schedule is not None:
            for t in self.schedule:
                if t["index"] not in self.trials:
                    return t
            return None
        return self.staircase.next_trial(self._stair_answers())

    def progress(self) -> dict:
        done = len(self.trials)
        total = self.total
        if total is None:  # staircase: upper bound
            total = self.config["max_trials"]
            if self.next_trial() is None:
                total = done
        return {"done": done, "total": total, "finished": self.next_trial() is None}

    # -------------------------------------------------------------- answers
    def record(self, index: int, payload: dict) -> dict:
        """Build, validate and append the trial record. The rung assignment comes from the
        server-side schedule; the page only supplies what the owner did and what it measured."""
        if index in self.trials:  # double submit: first answer wins
            return self.trials[index]
        t = self.next_trial()
        if t is None or t["index"] != index:
            raise ValueError(f"trial {index} is not the pending trial")
        answer = payload.get("answer")
        no_diff = bool(payload.get("no_difference"))
        if no_diff and not self.config.get("allow_no_difference"):
            raise ValueError("no-difference answers are disabled for this session")
        if no_diff:
            answer = None
        elif answer not in (1, 2):
            raise ValueError("answer must be 1 or 2")
        expected = None
        if self.kind == "blind":
            a, b = t["rungs"]
            if RUNG_NUMBER[a] != RUNG_NUMBER[b]:
                expected = 1 if RUNG_NUMBER[a] > RUNG_NUMBER[b] else 2
        elif t["level_ms"] > 0:
            expected = 1 if t["added_ms"][0] == 0 else 2
        correct = None if expected is None or answer is None else answer == expected
        intervals = []
        page_iv = payload.get("intervals") or [{}, {}]
        for i in range(2):
            iv = dict(page_iv[i]) if i < len(page_iv) and isinstance(page_iv[i], dict) else {}
            iv["interval"] = i + 1
            iv["rung"] = t["rungs"][i]
            iv["added_ms"] = t["added_ms"][i]
            intervals.append(iv)
        p50 = [((iv.get("latency") or {}).get("p50")) for iv in intervals]
        chose_lower = None
        if answer is not None and None not in p50 and p50[0] != p50[1]:
            chose_lower = answer == (1 if p50[0] < p50[1] else 2)
        rec = {
            "schema": SCHEMA, "record": "trial", "session_id": self.id, "kind": self.kind, "seed": self.seed,
            "trial_index": index, "sitting": self.sitting,
            "pair_name": t["pair_name"], "order": t["order"], "rungs": t["rungs"], "added_ms": t["added_ms"],
            "dataset_size": self.config["dataset_size"], "prompt": t["prompt"],
            "typed": [str((iv.get("typed") or "")) for iv in intervals],
            "answer": answer, "no_difference": no_diff, "confidence": payload.get("confidence"),
            "rt_ms": float(payload.get("rt_ms") or 0), "expected_faster_interval": expected,
            "chosen_rung": t["rungs"][answer - 1] if answer else None, "correct": correct,
            "chose_lower_measured_p50": chose_lower, "intervals": intervals,
            "t_trial_start": payload.get("t_trial_start") or None, "t_answer": now_iso(),
            "client": _client(payload.get("client")),
        }
        if self.kind == "blind":
            rec["pair"] = t["pair"]
        else:
            rec["level_ms"] = t["level_ms"]
            rec["delay_mode"] = self.config["delay_mode"]
        for iv in intervals:
            iv.pop("typed", None)
        errs = validate(rec)
        if errs:
            raise ValueError("; ".join(errs))
        with self.path.open("a") as f:
            f.write(json.dumps(rec) + "\n")
        self.trials[index] = rec
        return rec


def _client(c) -> dict:
    """Keep only non-identifying client facts."""
    c = c if isinstance(c, dict) else {}
    keep = ("browser", "cross_origin_isolated", "frame_ms", "dpr", "viewport")
    return {k: c.get(k) for k in keep if k in c}


def list_sessions(results_dir: Path) -> list[Session]:
    out = []
    for p in sorted(Path(results_dir).glob("*.jsonl")):
        try:
            out.append(Session(p))
        except (ValueError, KeyError, json.JSONDecodeError):
            continue
    return out


def read_records(paths) -> tuple[list[dict], list[dict]]:
    """Headers and trials from files and/or directories of *.jsonl."""
    files = []
    for p in paths:
        p = Path(p)
        files.extend(sorted(p.glob("*.jsonl")) if p.is_dir() else [p])
    headers, trials = [], []
    for f in files:
        seen = set()
        for line in f.read_text().splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec.get("record") == "session":
                headers.append(rec)
            elif rec.get("record") == "trial":
                key = (rec["session_id"], rec["trial_index"])
                if key not in seen:
                    seen.add(key)
                    trials.append(rec)
    return headers, trials
