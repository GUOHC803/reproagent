from .base import Tool, ToolContext, ToolRegistry, ToolResult
from .inspect_repo import InspectRepoTool
from .patch_file import PatchFileTool
from .read_pdf import ReadPdfTool
from .run_experiment import RunExperimentTool
from .run_tests import RunTestsTool
from .save_artifact import SaveArtifactTool


def default_registry() -> ToolRegistry:
    return ToolRegistry(
        [ReadPdfTool(), InspectRepoTool(), PatchFileTool(), RunTestsTool(), RunExperimentTool(), SaveArtifactTool()]
    )


__all__ = [
    "InspectRepoTool",
    "PatchFileTool",
    "ReadPdfTool",
    "RunExperimentTool",
    "RunTestsTool",
    "SaveArtifactTool",
    "Tool",
    "ToolContext",
    "ToolRegistry",
    "ToolResult",
    "default_registry",
]
