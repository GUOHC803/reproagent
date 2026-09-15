"""patch_file: the only write path into the workdir.

Two modes:
* ``replace``  - exact-substring replacement (old -> new), must match exactly once;
* ``create``   - write a whole (new) file.

Every call returns the unified diff it produced, so the trace holds a full
audit of what the agent changed; ``git_diff`` from the original plan is
subsumed by this return value.
"""

from __future__ import annotations

import difflib
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .base import Tool, ToolContext, ToolResult

_PROTECTED_GLOBS = ("tests/**", "test_*.py", "*_test.py", "conftest.py")


class PatchFileParams(BaseModel):
    path: str = Field(..., description="File path relative to the workdir.")
    mode: Literal["replace", "create"] = Field(..., description="replace: substring edit; create: write whole file.")
    old: str | None = Field(None, description="replace: exact text to find (must occur exactly once).")
    new: str | None = Field(None, description="replace: replacement text. create: full file content.")
    content: str | None = Field(None, description="create: full file content (alias of `new`).")

    @model_validator(mode="after")
    def _check(self):
        if self.mode == "replace" and (self.old is None or self.new is None):
            raise ValueError("replace requires `old` and `new`")
        if self.mode == "create" and self.content is None and self.new is None:
            raise ValueError("create requires `content`")
        return self


class PatchFileTool(Tool):
    name = "patch_file"
    description = (
        "Create or edit a file inside the workdir. mode='replace' needs `old` (exact text, occurring exactly once) "
        "and `new`; mode='create' needs `content`. Returns a unified diff. Test files under tests/ are protected "
        "unless the task allows editing them. Keep edits minimal; read the surrounding lines first."
    )
    Params = PatchFileParams
    read_only = False

    def run(self, p: PatchFileParams, ctx: ToolContext) -> ToolResult:  # type: ignore[override]
        fp = ctx.resolve(p.path)
        rel = fp.relative_to(ctx.workdir.resolve()).as_posix()
        if not ctx.extra.get("allow_test_edits") and _is_protected(rel):
            return ToolResult(status="blocked", stderr=f"{rel} is a protected test file",
                              next_hint="Fix the implementation, not the tests.")
        before = fp.read_text(encoding="utf-8") if fp.exists() else ""
        if p.mode == "create":
            after = p.content if p.content is not None else (p.new or "")
            action = "modify" if fp.exists() else "create"
        else:
            old, new = p.old or "", p.new or ""
            if not fp.exists():
                return ToolResult(status="error", stderr=f"no such file: {rel}", next_hint="Use mode='create' for new files.")
            count = before.count(old)
            if count == 0:
                # help the model: show the closest line
                close = difflib.get_close_matches(old.strip().splitlines()[0] if old.strip() else "", before.splitlines(), n=1, cutoff=0.5)
                hint = f"`old` not found in {rel}. Closest line: {close[0]!r}" if close else f"`old` not found in {rel}; read the file and copy the text exactly."
                return ToolResult(status="error", stderr="old text not found", next_hint=hint)
            if count > 1:
                return ToolResult(status="error", stderr=f"`old` occurs {count} times in {rel}",
                                  next_hint="Include more surrounding lines so the match is unique.")
            after = before.replace(old, new, 1)
            action = "modify"
        if after == before:
            return ToolResult(status="partial", stdout="no change", data={"path": rel, "diff": ""},
                              next_hint="The edit produced no change.")
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(after, encoding="utf-8")
        diff = "".join(
            difflib.unified_diff(before.splitlines(True), after.splitlines(True), fromfile=f"a/{rel}", tofile=f"b/{rel}")
        )
        # keep the diff bounded for the model; full diff is in data for the trace
        shown = diff if len(diff) < 4000 else diff[:4000] + "\n... (diff truncated)"
        return ToolResult(status="ok", stdout=shown,
                          data={"path": rel, "action": action, "diff": diff, "lines_changed": diff.count("\n@@")})


def _is_protected(rel: str) -> bool:
    from fnmatch import fnmatch

    name = rel.rsplit("/", 1)[-1]
    return rel.startswith("tests/") or fnmatch(name, "test_*.py") or fnmatch(name, "*_test.py") or name == "conftest.py"
