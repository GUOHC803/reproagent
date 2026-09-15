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
    "Tool",
    "ToolContext",
    "ToolRegistry",
    "ToolResult",
    "ReadPdfTool",
    "InspectRepoTool",
    "PatchFileTool",
    "RunTestsTool",
    "RunExperimentTool",
    "SaveArtifactTool",
    "default_registry",
]
