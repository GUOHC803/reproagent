"""inspect_repo: read-only navigation of the task workdir (tree / grep / read)."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .base import Tool, ToolContext, ToolResult

_SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules", ".pytest_cache", ".mypy_cache", ".ruff_cache",
              ".reproagent"}
_TEXT_EXT = {".py", ".md", ".txt", ".yaml", ".yml", ".toml", ".json", ".cfg", ".ini", ".sh", ".csv", ".rst", ".env.example", ".html", ".js", ".ts", ".c", ".cpp", ".h", ".java", ".go", ".rs"}


class InspectRepoParams(BaseModel):
    action: Literal["tree", "grep", "read"] = Field(..., description="tree: list files; grep: regex search; read: file content.")
    path: str = Field(".", description="Directory (tree/grep) or file (read), relative to workdir.")
    pattern: str | None = Field(None, description="Regex for grep.")
    max_depth: int = Field(3, ge=1, le=8, description="tree depth.")
    start_line: int = Field(1, ge=1, description="read: first line (1-based).")
    end_line: int | None = Field(None, description="read: last line inclusive. Default: start+200.")
    max_results: int = Field(60, ge=1, le=400, description="grep: cap on matching lines.")

    @model_validator(mode="after")
    def _check(self):
        if self.action == "grep" and not self.pattern:
            raise ValueError("grep requires `pattern`")
        return self


class InspectRepoTool(Tool):
    name = "inspect_repo"
    description = (
        "Navigate the repository without modifying it. action='tree' lists files (with sizes), "
        "action='grep' searches a regex across text files and returns file:line:text, "
        "action='read' returns a line range of one file with line numbers. "
        "Start with tree, then grep for entry points (if __name__, argparse, def main), then read."
    )
    Params = InspectRepoParams

    def run(self, p: InspectRepoParams, ctx: ToolContext) -> ToolResult:  # type: ignore[override]
        root = ctx.resolve(p.path)
        if not root.exists():
            return ToolResult(status="error", stderr=f"no such path: {p.path}",
                              next_hint="List the parent directory with action='tree' first.")
        if p.action == "tree":
            return self._tree(root, ctx.workdir, p.max_depth)
        if p.action == "grep":
            return self._grep(root, ctx.workdir, p.pattern or "", p.max_results)
        return self._read(root, ctx.workdir, p.start_line, p.end_line)

    def _tree(self, root: Path, workdir: Path, max_depth: int) -> ToolResult:
        if root.is_file():
            return ToolResult(status="ok", stdout=f"{root.relative_to(workdir)} ({root.stat().st_size} B)")
        lines: list[str] = []
        n_files = 0
        base_depth = len(root.parts)
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".egg"))
            depth = len(Path(dirpath).parts) - base_depth
            if depth >= max_depth:
                dirnames[:] = []
            rel = Path(dirpath).relative_to(workdir)
            indent = "  " * depth
            lines.append(f"{indent}{rel.as_posix() if str(rel) != '.' else '.'}/")
            for f in sorted(filenames):
                fp = Path(dirpath) / f
                try:
                    size = fp.stat().st_size
                except OSError:
                    size = -1
                lines.append(f"{indent}  {f} ({size} B)")
                n_files += 1
                if n_files > 400:
                    lines.append(f"{indent}  ... (truncated)")
                    return ToolResult(status="partial", stdout="\n".join(lines), data={"files": n_files},
                                      next_hint="Too many files; inspect a subdirectory.")
        return ToolResult(status="ok", stdout="\n".join(lines), data={"files": n_files})

    def _grep(self, root: Path, workdir: Path, pattern: str, max_results: int) -> ToolResult:
        try:
            rx = re.compile(pattern)
        except re.error as e:
            return ToolResult(status="error", stderr=f"bad regex: {e}", next_hint="Escape special characters.")
        hits: list[str] = []
        files = [root] if root.is_file() else _iter_text_files(root)
        n_files = 0
        for fp in files:
            n_files += 1
            try:
                for i, line in enumerate(fp.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    if rx.search(line):
                        hits.append(f"{fp.relative_to(workdir).as_posix()}:{i}:{line.strip()[:200]}")
                        if len(hits) >= max_results:
                            return ToolResult(status="partial", stdout="\n".join(hits),
                                              data={"matches": len(hits), "files_scanned": n_files},
                                              next_hint="Hit max_results; narrow the pattern or path.")
            except OSError:
                continue
        if not hits:
            return ToolResult(status="ok", stdout="", data={"matches": 0, "files_scanned": n_files},
                              next_hint=f"No match for {pattern!r} in {n_files} files. Try a looser pattern.")
        return ToolResult(status="ok", stdout="\n".join(hits), data={"matches": len(hits), "files_scanned": n_files})

    def _read(self, fp: Path, workdir: Path, start: int, end: int | None) -> ToolResult:
        if fp.is_dir():
            return ToolResult(status="error", stderr=f"{fp.relative_to(workdir)} is a directory",
                              next_hint="Use action='tree' for directories.")
        if fp.suffix.lower() == ".pdf":
            return ToolResult(status="error", stderr="binary PDF", next_hint="Use read_pdf for PDF files.")
        try:
            lines = fp.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as e:
            return ToolResult(status="error", stderr=str(e))
        total = len(lines)
        end = end or min(total, start + 199)
        end = min(end, total)
        if start > total:
            return ToolResult(status="error", stderr=f"start_line {start} > file length {total}")
        body = "\n".join(f"{i:5d}| {lines[i - 1]}" for i in range(start, end + 1))
        hint = None if end >= total else f"Showing {start}-{end} of {total} lines; continue with start_line={end + 1}."
        return ToolResult(status="ok", stdout=body, data={"lines": total, "shown": [start, end]}, next_hint=hint)


def _iter_text_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for f in filenames:
            fp = Path(dirpath) / f
            if fp.suffix.lower() in _TEXT_EXT or f in ("Makefile", "Dockerfile", "requirements.txt"):
                try:
                    if fp.stat().st_size < 2_000_000:
                        yield fp
                except OSError:
                    pass
