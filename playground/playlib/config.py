"""Paths, rungs and defaults. Nothing here reaches the page except through server.py."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

PLAYGROUND = Path(__file__).resolve().parent.parent
REPO = PLAYGROUND.parent
STATIC = PLAYGROUND / "static"
SHARED = REPO / "rungs" / "shared"
DEFAULT_RESULTS = PLAYGROUND / "results"


@dataclass(frozen=True)
class Rung:
    id: str  # "r1" | "r2" | "r3": what the results file records
    label: str  # shown in open mode only
    dist: Path  # static production build

    @property
    def available(self) -> bool:
        return (self.dist / "index.html").is_file()


# R1 is the Vite variant: identical React/cmdk code to the Next variant, but a static build the
# playground can serve itself (rungs/r1-vite/README.md). The Next variant needs `next start`.
RUNGS: dict[str, Rung] = {
    "r1": Rung("r1", "R1 Typical (Vite + shadcn/cmdk)", REPO / "rungs" / "r1-vite" / "dist"),
    "r2": Rung("r2", "R2 Diligent (React, virtualized)", REPO / "rungs" / "r2-diligent" / "dist"),
    "r3": Rung("r3", "R3 No framework (DOM + worker)", REPO / "rungs" / "r3-no-framework" / "dist"),
}
RUNG_NUMBER = {"r1": 1, "r2": 2, "r3": 3}

SIZES = ("1k", "10k", "50k")

# §5.2: pairs R1–R3, R1–R2, R2–R3 and the R3–R3 placebo. Each pair is (slower, faster) by rung
# number: the "faster" rung is the higher rung number (lower measured latency, Gate A premise).
BLIND_PAIRS = (("r1", "r3"), ("r1", "r2"), ("r2", "r3"), ("r3", "r3"))

# §5.3 comparison levels; 0 is the catch (placebo) level, making 8 levels × 20 = 160 trials.
JND_LEVELS = (0, 8, 17, 25, 33, 50, 67, 100)

# Prompt classes: natural prefixes are easy to type; typo classes (transposition, doubled-letter)
# are awkward to type on purpose, and no-match shows an empty list.
PROMPT_CLASSES = ("word-prefix", "multi-word-prefix")


def pair_name(pair) -> str:
    a, b = pair
    return f"R{RUNG_NUMBER[a]}-R{RUNG_NUMBER[b]}"


def dataset_dir() -> Path | None:
    """The dataset bytes served to every rung (identical for all, §5.2 leakage checks)."""
    for r in RUNGS.values():
        d = r.dist / "dataset"
        if (d / "10k" / "items.json").is_file():
            return d
    return None


def dataset_consistency() -> list[str]:
    """Warn if the prepared copies in the rungs' dist/ differ (they must be identical bytes)."""
    warnings = []
    for size in SIZES:
        digests = {}
        for r in RUNGS.values():
            f = r.dist / "dataset" / size / "items.json"
            if f.is_file():
                digests[r.id] = hashlib.sha256(f.read_bytes()).hexdigest()
        if len(set(digests.values())) > 1:
            warnings.append(f"dataset {size} differs between rung builds: {digests}; re-run prepare.mjs")
    return warnings


def load_prompts(classes=PROMPT_CLASSES, min_len=5, max_len=8) -> list[str]:
    """Typeable 5–8 character queries from the dataset's query set (§5.2)."""
    d = dataset_dir()
    candidates = [d / "queries.json"] if d else []
    candidates.append(REPO / "dataset" / "out" / "dev-1" / "queries.json")
    for f in candidates:
        if f.is_file():
            q = json.loads(f.read_text())["queries"]
            out = []
            for item in q:
                s = item["q"]
                if (item.get("typeable") and item.get("class") in classes and s == s.strip()
                        and min_len <= len(s) <= max_len and s not in out):
                    out.append(s)
            if out:
                return out
    # Fallback so the tool still runs without a prepared dataset (documented in README).
    return ["config", "install", "search", "format", "terminal", "settings"]

