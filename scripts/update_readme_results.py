"""Copy the latest evals/results/<stamp>/summary.md into the README result sections.

    python scripts/update_readme_results.py [evals/results/<stamp>]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def latest_results() -> Path:
    dirs = sorted(p for p in (ROOT / "evals" / "results").iterdir() if (p / "summary.md").exists())
    if not dirs:
        raise SystemExit("no evaluation results found")
    return dirs[-1]


def main() -> None:
    res = Path(sys.argv[1]) if len(sys.argv) > 1 else latest_results()
    body = (res / "summary.md").read_text(encoding="utf-8")
    body = re.sub(r"^# .*\n", "", body, count=1).strip()
    body = re.sub(r"^## ", "#### ", body, flags=re.MULTILINE)
    rel = res.relative_to(ROOT).as_posix()
    block = f"来源 / source: `{rel}/summary.md`（rows.jsonl 里有每次运行的明细）\n\n{body}\n"
    for name in ("README.md", "README_en.md"):
        p = ROOT / name
        s = p.read_text(encoding="utf-8")
        s = re.sub(r"<!-- RESULTS:BEGIN -->.*?<!-- RESULTS:END -->",
                   "<!-- RESULTS:BEGIN -->\n" + block + "<!-- RESULTS:END -->", s, flags=re.DOTALL)
        p.write_text(s, encoding="utf-8")
        print(f"updated {name} from {rel}")


if __name__ == "__main__":
    main()
