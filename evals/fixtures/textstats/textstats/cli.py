"""Command line: `python -m textstats.cli FILE [--top 10]`."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .stats import top_k, word_freq
from .tokenize import normalize, tokenize


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--top", type=int, default=10)
    args = ap.parse_args(argv)
    text = Path(args.file).read_text(encoding="utf-8")
    freq = word_freq(normalize(tokenize(text)))
    print(json.dumps({"tokens": sum(freq.values()), "unique": len(freq), "top": top_k(freq, args.top)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
