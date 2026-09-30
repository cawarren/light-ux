import unittest

import _util  # noqa: F401
from ladder.soft.analysis import correctness as C

ITEMS = ["alpha", "beta", "gamma", "delta", "epsilon"]
ID = {t: i for i, t in enumerate(ITEMS)}
# reference for some query: ids [3, 1, 4] with scores s1 > s2 == s3 (a tie between 1 and 4)
REF = {"count": 3, "top_ids": [3, 1, 4], "top_scores": ["a", "b", "b"], "cand": {3: "a", 1: "b", 4: "b", 0: None}}


def flip(texts, count=3):
    return {"top50": texts, "count": count}


class VerdictTest(unittest.TestCase):
    def test_strict(self):
        self.assertTrue(C.verdict(flip(["delta", "beta", "epsilon"]), REF, ID, "strict")[0])
        self.assertTrue(C.verdict(flip(["delta", "beta"]), REF, ID, "strict")[0])       # virtualized prefix
        self.assertFalse(C.verdict(flip(["delta", "epsilon", "beta"]), REF, ID, "strict")[0])

    def test_tie_insensitive(self):
        self.assertTrue(C.verdict(flip(["delta", "epsilon", "beta"]), REF, ID, "tie")[0])
        self.assertFalse(C.verdict(flip(["beta", "delta", "epsilon"]), REF, ID, "tie")[0])

    def test_set_only(self):
        self.assertTrue(C.verdict(flip(["epsilon", "beta", "delta"]), REF, ID, "set")[0])
        self.assertFalse(C.verdict(flip(["epsilon", "alpha", "delta"]), REF, ID, "set")[0])

    def test_count_duplicates_unknown(self):
        self.assertEqual(C.verdict(flip(["delta", "beta", "epsilon"], count=4), REF, ID, "set")[1], "count 4 != 3")
        self.assertFalse(C.verdict(flip(["delta", "delta"]), REF, ID, "set")[0])
        self.assertEqual(C.verdict(flip(["zeta"]), REF, ID, "set")[1], "row_not_in_dataset")
        self.assertFalse(C.verdict(flip([], count=None), REF, ID, "set")[0])
        self.assertIsNone(C.verdict(flip(["delta"]), None, ID, "strict")[0])

    def test_levels(self):
        self.assertEqual(C.level_for("r1-vite", "fwd"), "tie")
        self.assertEqual(C.level_for("r1-typical", "back"), "set")
        self.assertEqual(C.level_for("r2-diligent", "back"), "strict")
        self.assertEqual(C.level_for("r3-no-framework", "fwd"), "strict")


if __name__ == "__main__":
    unittest.main()
