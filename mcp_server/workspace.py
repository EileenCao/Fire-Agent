"""Independent user workspace resolution and initialization."""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union


WORKSPACE_POINTER = Path(".fireagent") / "workspace.json"


class WorkspaceError(RuntimeError):
    """Raised when the user workspace is missing or unsafe to use."""


@dataclass(frozen=True)
class Workspace:
    project_root: Path
    root: Path
    pointer_path: Path

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def parquet_dir(self) -> Path:
        return self.data_dir / "parquet"

    @property
    def calendar_path(self) -> Path:
        return self.data_dir / "trading_holidays.json"

    @property
    def strategy_dir(self) -> Path:
        return self.root / "strategies"

    @property
    def strategy_path(self) -> Path:
        return self.strategy_dir / "strategy.json"

    @property
    def latest_artifacts_dir(self) -> Path:
        return self.root / "artifacts" / "latest"

    @property
    def formal_artifacts_dir(self) -> Path:
        return self.root / "artifacts" / "formal"

    @property
    def reports_dir(self) -> Path:
        return self.root / "reports"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    @property
    def config_dir(self) -> Path:
        return self.root / "config"

    @property
    def env_path(self) -> Path:
        return self.config_dir / ".env"

    @property
    def db_path(self) -> Path:
        return self.root / "stock_research.sqlite3"


def initialize_workspace(
    project_root: Union[str, Path],
    workspace_path: Union[str, Path],
    overwrite: bool = False,
) -> Workspace:
    project = Path(project_root).resolve()
    workspace_root = _validate_root(project, workspace_path)
    pointer_path = project / WORKSPACE_POINTER

    if pointer_path.exists() and not overwrite:
        try:
            existing = json.loads(pointer_path.read_text(encoding="utf-8"))
            existing_root = Path(existing["workspace_path"]).resolve()
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise WorkspaceError(
                "工作区配置损坏，请先检查 {} 或使用 --overwrite 重建".format(pointer_path)
            ) from exc
        if existing_root != workspace_root:
            raise WorkspaceError(
                "已有工作区 {}；如需切换，请明确使用 --overwrite".format(existing_root)
            )

    workspace = Workspace(project_root=project, root=workspace_root, pointer_path=pointer_path)
    for path in (
        workspace.raw_dir,
        workspace.parquet_dir,
        workspace.strategy_dir,
        workspace.latest_artifacts_dir,
        workspace.formal_artifacts_dir,
        workspace.reports_dir,
        workspace.logs_dir,
        workspace.config_dir,
    ):
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise WorkspaceError("无法创建工作区目录 {}: {}".format(path, exc)) from exc

    pointer_path.parent.mkdir(parents=True, exist_ok=True)
    pointer_path.write_text(
        json.dumps(
            {"version": 1, "workspace_path": str(workspace.root)},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return workspace


def load_workspace(
    project_root: Union[str, Path], required: bool = True
) -> Optional[Workspace]:
    project = Path(project_root).resolve()
    configured = os.getenv("FIREAGENT_WORKSPACE")
    pointer_path = project / WORKSPACE_POINTER
    if configured:
        return _workspace_from_path(project, configured, pointer_path)
    if not pointer_path.exists():
        if required:
            raise WorkspaceError(
                "尚未配置独立工作区。请先询问用户提供工作文件夹，并运行 "
                "python -m mcp_server.cli init --workspace <路径>"
            )
        return None
    try:
        payload = json.loads(pointer_path.read_text(encoding="utf-8"))
        configured = payload["workspace_path"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise WorkspaceError("工作区配置无效：{}".format(pointer_path)) from exc
    return _workspace_from_path(project, configured, pointer_path)


def _workspace_from_path(project: Path, value: Union[str, Path], pointer_path: Path) -> Workspace:
    workspace_root = _validate_root(project, value)
    if not workspace_root.exists():
        raise WorkspaceError(
            "工作区不存在：{}。请询问用户提供新的工作文件夹".format(workspace_root)
        )
    if not workspace_root.is_dir():
        raise WorkspaceError("工作区不是目录：{}".format(workspace_root))
    return Workspace(project_root=project, root=workspace_root, pointer_path=pointer_path)


def _validate_root(project: Path, value: Union[str, Path]) -> Path:
    raw = Path(value).expanduser()
    if not raw.is_absolute():
        raise WorkspaceError("工作区必须是绝对路径，请让用户提供例如 D:\\FireAgentWorkspace")
    workspace_root = raw.resolve()
    try:
        workspace_root.relative_to(project)
    except ValueError:
        return workspace_root
    raise WorkspaceError("工作区必须是独立工作区，不能等于或位于 FireAgent 代码仓库内部")
