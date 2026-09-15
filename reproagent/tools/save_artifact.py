"""save_artifact: copy a produced file (or write text) into the run's artifact directory."""

from __future__ import annotations

import shutil

from pydantic import BaseModel, Field, model_validator

from .base import Tool, ToolContext, ToolResult


class SaveArtifactParams(BaseModel):
    name: str = Field(..., description="Artifact file name, e.g. 'results.json' or 'loss_curve.png'.")
    src_path: str | None = Field(None, description="Existing file in the workdir to copy.")
    content: str | None = Field(None, description="Text content to write instead of copying a file.")

    @model_validator(mode="after")
    def _check(self):
        if (self.src_path is None) == (self.content is None):
            raise ValueError("provide exactly one of src_path or content")
        if "/" in self.name or "\\" in self.name or self.name.startswith("."):
            raise ValueError("name must be a plain file name")
        return self


class SaveArtifactTool(Tool):
    name = "save_artifact"
    description = (
        "Persist a result file (metrics json, log, figure, extracted table) into the run's artifact store so it "
        "appears in the final report. Copy an existing file with src_path or write text with content."
    )
    Params = SaveArtifactParams
    read_only = False

    def run(self, p: SaveArtifactParams, ctx: ToolContext) -> ToolResult:  # type: ignore[override]
        ctx.artifacts_dir.mkdir(parents=True, exist_ok=True)
        dst = ctx.artifacts_dir / p.name
        if p.src_path:
            src = ctx.resolve(p.src_path)
            if not src.is_file():
                return ToolResult(status="error", stderr=f"no such file: {p.src_path}",
                                  next_hint="Check run_experiment's new_files list for the real output path.")
            if src.stat().st_size > 50_000_000:
                return ToolResult(status="error", stderr="file larger than 50MB")
            shutil.copyfile(src, dst)
        else:
            dst.write_text(p.content or "", encoding="utf-8")
        size = dst.stat().st_size
        store = ctx.extra.get("store")
        if store is not None:
            store.add_artifact(ctx.run_id, ctx.extra.get("step_idx"), p.name, str(dst))
        return ToolResult(status="ok", stdout=f"saved {p.name} ({size} B)", artifacts=[str(dst)],
                          data={"path": str(dst), "size": size})
