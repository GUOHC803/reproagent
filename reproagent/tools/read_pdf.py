"""read_pdf: extract text from a local PDF, optionally a page range or a section."""

from __future__ import annotations

import re
from functools import lru_cache

from pydantic import BaseModel, Field

from .base import Tool, ToolContext, ToolResult

_SECTION_RX = re.compile(
    r"^\s*(?:\d+(?:\.\d+)*\.?\s+)?(Abstract|Introduction|Related Work|Background|Method(?:s|ology)?|Approach|"
    r"Model|Experiments?|Experimental Setup|Results|Evaluation|Implementation Details|Training|Datasets?|"
    r"Ablation(?:s| Study)?|Conclusion|Limitations|Appendix|References)\b",
    re.IGNORECASE | re.MULTILINE,
)


@lru_cache(maxsize=32)
def _extract_pages(path: str) -> tuple[str, ...]:
    from pypdf import PdfReader

    reader = PdfReader(path)
    pages = []
    for p in reader.pages:
        try:
            pages.append(p.extract_text() or "")
        except Exception as e:  # noqa: BLE001
            pages.append(f"[page extraction failed: {e}]")
    return tuple(pages)


class ReadPdfParams(BaseModel):
    path: str = Field(..., description="PDF path relative to the task workdir.")
    pages: str | None = Field(
        None, description="Page range like '1-3' or '5' (1-based). Default: all pages, bounded by max_chars."
    )
    section: str | None = Field(
        None, description="Return only the section whose heading matches this (e.g. 'Experiments')."
    )
    grep: str | None = Field(None, description="Regex; return only lines matching it with 1 line of context.")
    max_chars: int = Field(12000, ge=500, le=60000, description="Upper bound on returned characters.")


class ReadPdfTool(Tool):
    name = "read_pdf"
    description = (
        "Extract text from a local PDF. Use `section` to jump to Method/Experiments/etc., `pages` for a range, "
        "or `grep` to find lines mentioning a term (datasets, metrics, hyper-parameters). "
        "Returned text is truncated to max_chars; the tool tells you the total page count."
    )
    Params = ReadPdfParams

    def run(self, params: ReadPdfParams, ctx: ToolContext) -> ToolResult:  # type: ignore[override]
        p = ctx.resolve(params.path)
        if not p.exists():
            return ToolResult(status="error", stderr=f"no such file: {params.path}",
                              next_hint="Check the path with inspect_repo(action='tree').")
        pages = _extract_pages(str(p))
        n = len(pages)
        sel = list(range(n))
        if params.pages:
            sel = _parse_range(params.pages, n)
            if not sel:
                return ToolResult(status="error", stderr=f"bad page range {params.pages!r}; document has {n} pages")
        text = "\n".join(f"[page {i + 1}]\n{pages[i]}" for i in sel)
        note = f"{n} pages total"

        if params.section:
            text, found = _cut_section(text, params.section)
            if not found:
                return ToolResult(status="partial", stdout=text[: params.max_chars], data={"pages": n},
                                  next_hint=f"No heading matching {params.section!r}; returned the beginning instead. "
                                            "Try grep or a page range.")
            note += f", section {params.section!r}"
        if params.grep:
            try:
                rx = re.compile(params.grep, re.IGNORECASE)
            except re.error as e:
                return ToolResult(status="error", stderr=f"bad regex: {e}")
            lines = text.splitlines()
            hits = [i for i, ln in enumerate(lines) if rx.search(ln)]
            if not hits:
                return ToolResult(status="partial", stdout="", data={"pages": n, "matches": 0},
                                  next_hint=f"No lines match {params.grep!r}. Try a broader pattern or read a section.")
            keep: set[int] = set()
            for i in hits:
                keep.update({i - 1, i, i + 1})
            text = "\n".join(lines[i] for i in sorted(k for k in keep if 0 <= k < len(lines)))
            note += f", {len(hits)} matching lines"

        truncated = len(text) > params.max_chars
        out = text[: params.max_chars]
        hint = None
        if truncated:
            hint = (f"Output truncated at {params.max_chars} chars ({len(text)} available). "
                    "Use pages/section/grep to narrow, or raise max_chars.")
        return ToolResult(status="ok", stdout=out, data={"pages": n, "chars": len(text), "note": note}, next_hint=hint)


def _parse_range(spec: str, n: int) -> list[int]:
    out: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            try:
                lo, hi = int(a), int(b)
            except ValueError:
                return []
            out.extend(range(max(1, lo) - 1, min(n, hi)))
        else:
            try:
                i = int(part)
            except ValueError:
                return []
            if 1 <= i <= n:
                out.append(i - 1)
    return sorted(set(out))


def _cut_section(text: str, name: str) -> tuple[str, bool]:
    heads = list(_SECTION_RX.finditer(text))
    target = None
    for i, m in enumerate(heads):
        if name.lower() in m.group(1).lower():
            target = i
            break
    if target is None:
        return text, False
    start = heads[target].start()
    end = heads[target + 1].start() if target + 1 < len(heads) else len(text)
    return text[start:end], True
