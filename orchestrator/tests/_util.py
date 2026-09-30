import os
import sys

ORCH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(ORCH)
sys.path.insert(0, ORCH)

POOLS = {
    "first_chars": list("aaabbcs1 "),
    "seq_queries": [{"qid": 1, "q": "open file settings ab"[:20]}, {"qid": 2, "q": "git branch delete 12"}],
    "sha256": "0" * 64, "seed_id": "test",
}
