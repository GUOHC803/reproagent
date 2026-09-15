"""Download the arXiv PDFs used by the paper-extraction tasks (not committed: size/licensing).

    python evals/fixtures/papers/fetch.py

Ground-truth values in evals/tasks/paper_*.yaml were read from these exact versions.
"""

from __future__ import annotations

import sys
import time
import urllib.request
from pathlib import Path

PAPERS = {
    "1706.03762": "Attention Is All You Need (v7)",
    "2106.09685": "LoRA: Low-Rank Adaptation of Large Language Models (v2)",
    "1512.03385": "Deep Residual Learning for Image Recognition (v1)",
    "2504.16054": "pi0.5: a Vision-Language-Action Model with Open-World Generalization (v1)",
}
HERE = Path(__file__).parent


def fetch(arxiv_id: str, retries: int = 6) -> Path:
    dst = HERE / f"{arxiv_id}.pdf"
    if dst.exists() and dst.stat().st_size > 100_000:
        return dst
    url = f"https://arxiv.org/pdf/{arxiv_id}"
    for i in range(retries):
        try:
            urllib.request.urlretrieve(url, dst)
            if dst.stat().st_size > 100_000:
                return dst
        except Exception as e:  # noqa: BLE001
            print(f"retry {i + 1} for {arxiv_id}: {e}", file=sys.stderr)
            time.sleep(3)
    raise RuntimeError(f"could not download {arxiv_id}")


if __name__ == "__main__":
    for pid, title in PAPERS.items():
        p = fetch(pid)
        print(f"{pid}  {p.stat().st_size // 1024} KB  {title}")
