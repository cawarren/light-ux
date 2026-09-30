"""Seeded, recorded trial schedules (docs/phase-a/README.md §5.2, §5.3).

Schedules are a pure function of (seed, config), so a session is resumed by regenerating its
schedule from the results file header and skipping the trials already answered. The adaptive
staircase is a pure function of (seed, config, answers so far).

The schedule never leaves the server: the page only receives opaque per-interval tokens.
"""
from __future__ import annotations

import random

from .config import BLIND_PAIRS, JND_LEVELS, pair_name


def _balanced_orders(rng: random.Random, n: int) -> list[int]:
    """n order flags, half 0 and half 1 (the odd one random), in random order."""
    orders = [0, 1] * (n // 2)
    if n % 2:
        orders.append(rng.randrange(2))
    rng.shuffle(orders)
    return orders


def blind_schedule(seed: str, trials_per_pair: int, prompts: list[str], pairs=BLIND_PAIRS) -> list[dict]:
    """2AFC rung pairs, order counterbalanced within each pair, pairs interleaved at random.

    order "AB" means the pair's first rung (the slower one by rung number) is in interval 1.
    """
    if trials_per_pair < 1:
        raise ValueError("trials_per_pair must be ≥ 1")
    rng = random.Random(f"blind:{seed}")
    trials = []
    for pair in pairs:
        for o in _balanced_orders(rng, trials_per_pair):
            a, b = pair
            trials.append({"pair": [a, b], "pair_name": pair_name(pair), "order": "AB" if o == 0 else "BA",
                           "rungs": [a, b] if o == 0 else [b, a], "added_ms": [0, 0]})
    rng.shuffle(trials)
    for i, t in enumerate(trials):
        t["index"] = i
        t["prompt"] = rng.choice(prompts)
    return trials


def jnd_constant_schedule(seed: str, reps: int, prompts: list[str], levels=JND_LEVELS, rung: str = "r3") -> list[dict]:
    """Method of constant stimuli: reference N = 0 vs comparison N, `reps` trials per level.

    order "RC" = reference (no added delay) in interval 1; "CR" = comparison first. Level 0 is
    the catch trial (both intervals identical); its "reference" is only a label.
    """
    if reps < 1:
        raise ValueError("reps must be ≥ 1")
    rng = random.Random(f"jnd:{seed}")
    trials = []
    for n in levels:
        for o in _balanced_orders(rng, reps):
            trials.append(_jnd_trial(n, o, rung))
    rng.shuffle(trials)
    for i, t in enumerate(trials):
        t["index"] = i
        t["prompt"] = rng.choice(prompts)
    return trials


def _jnd_trial(n: int, o: int, rung: str) -> dict:
    return {"level_ms": n, "order": "RC" if o == 0 else "CR", "rungs": [rung, rung],
            "added_ms": [0, n] if o == 0 else [n, 0], "pair_name": f"JND-{n}"}


class Staircase:
    """1-up-3-down staircase over the comparison levels (converges on 79.4% correct, §5.3).

    Starts at the largest level; three consecutive correct → one level down; one wrong → one
    level up. Stops after `max_trials` trials or `max_reversals` reversals.
    """

    def __init__(self, seed: str, prompts: list[str], levels=None, max_trials: int = 60,
                 max_reversals: int = 12, rung: str = "r3"):
        self.levels = sorted(x for x in (levels or JND_LEVELS) if x > 0)
        self.seed, self.prompts, self.rung = seed, prompts, rung
        self.max_trials, self.max_reversals = max_trials, max_reversals

    def state(self, answers: list[bool]) -> dict:
        """Replay the answers (True = correct) and return the next level index and reversals."""
        idx = len(self.levels) - 1
        streak = 0
        direction = 0
        reversals = 0
        for correct in answers:
            new = idx
            if correct:
                streak += 1
                if streak == 3:
                    new, streak = max(0, idx - 1), 0
            else:
                new, streak = min(len(self.levels) - 1, idx + 1), 0
            d = (new > idx) - (new < idx)
            if d:
                if direction and d != direction:
                    reversals += 1
                direction = d
            idx = new
        return {"idx": idx, "reversals": reversals}

    def next_trial(self, answers: list[bool]) -> dict | None:
        i = len(answers)
        st = self.state(answers)
        if i >= self.max_trials or st["reversals"] >= self.max_reversals:
            return None
        rng = random.Random(f"stair:{self.seed}:{i}")
        t = _jnd_trial(self.levels[st["idx"]], rng.randrange(2), self.rung)
        t["index"] = i
        t["prompt"] = rng.choice(self.prompts)
        return t
