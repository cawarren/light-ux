#!/usr/bin/env python3
"""Entry point of the injector child process (see injector.py). Runnable as a plain script so
that `sudo -- python3 .../injector_main.py` works without PYTHONPATH (sudo resets it)."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..")))

from ladder.soft.runner.injector import child_main  # noqa: E402

if __name__ == "__main__":
    sys.exit(child_main(sys.argv[1:]))
