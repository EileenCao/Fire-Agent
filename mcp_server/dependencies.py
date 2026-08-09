"""Runtime checks for the external a-stock-data Agent Skill."""

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Union


DEFAULT_MINIMUM_VERSION = "3.6.0"
_VERSION_RE = re.compile(r"^\s*(\d+)(?:\.(\d+))?(?:\.(\d+))?.*$")


class AStockDataSkillError(RuntimeError):
    """Raised when the required external data Skill is missing or invalid."""


@dataclass(frozen=True)
class AStockDataSkill:
    path: Path
    name: str
    version: str


def discover_a_stock_data_skill(
    explicit_path: Optional[Union[str, Path]] = None,
    home: Optional[Union[str, Path]] = None,
    environ: Optional[dict] = None,
) -> AStockDataSkill:
    """Find and validate the installed ``a-stock-data`` SKILL.md."""
    env = environ if environ is not None else os.environ
    configured = explicit_path or env.get("A_STOCK_DATA_SKILL_PATH")
    candidates = list(_candidate_paths(configured, home))
    if not candidates:
        raise _missing_skill_error()

    for candidate in candidates:
        if not candidate.exists():
            continue
        return _read_skill(candidate)
    raise _missing_skill_error(candidates)


def require_a_stock_data_skill(
    explicit_path: Optional[Union[str, Path]] = None,
    minimum_version: str = DEFAULT_MINIMUM_VERSION,
    home: Optional[Union[str, Path]] = None,
    environ: Optional[dict] = None,
) -> AStockDataSkill:
    """Require an installed Skill of at least ``minimum_version``."""
    skill = discover_a_stock_data_skill(
        explicit_path=explicit_path, home=home, environ=environ
    )
    if _version_tuple(skill.version) < _version_tuple(minimum_version):
        raise AStockDataSkillError(
            "a-stock-data Skill 版本过低：当前 {}，要求至少 {}。"
            "请更新 SKILL.md 后重试。".format(skill.version, minimum_version)
        )
    return skill


def _candidate_paths(
    configured: Optional[Union[str, Path]], home: Optional[Union[str, Path]]
) -> Iterable[Path]:
    if configured:
        value = Path(configured).expanduser()
        yield value / "SKILL.md" if value.is_dir() else value
        return

    user_home = Path(home).expanduser() if home else Path.home()
    for root in (".codex", ".agents", ".claude"):
        yield user_home / root / "skills" / "a-stock-data" / "SKILL.md"


def _read_skill(path: Path) -> AStockDataSkill:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AStockDataSkillError("无法读取 a-stock-data Skill：{}".format(exc)) from exc

    frontmatter = _frontmatter(content)
    name = frontmatter.get("name", "").strip()
    version = frontmatter.get("version", "").strip()
    if name != "a-stock-data":
        raise AStockDataSkillError(
            "Skill {} 的名称不是 a-stock-data。".format(path)
        )
    if not _VERSION_RE.match(version):
        raise AStockDataSkillError(
            "a-stock-data Skill 缺少有效版本号：{}。".format(path)
        )
    return AStockDataSkill(path=path, name=name, version=version)


def _frontmatter(content: str) -> dict:
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        raise AStockDataSkillError("a-stock-data Skill 缺少 YAML frontmatter。")
    values = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return values
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    raise AStockDataSkillError("a-stock-data Skill 的 frontmatter 未闭合。")


def _version_tuple(version: str):
    match = _VERSION_RE.match(version)
    if not match:
        return (0, 0, 0)
    return tuple(int(value or 0) for value in match.groups())


def _missing_skill_error(candidates: Optional[Iterable[Path]] = None):
    paths = ", ".join(str(path) for path in (candidates or ()))
    suffix = " 已检查：{}。".format(paths) if paths else ""
    return AStockDataSkillError(
        "未找到 a-stock-data Skill。{}请先安装 SKILL.md，并安装 "
        "mootdx、requests、pandas、stockstats；也可以设置 "
        "A_STOCK_DATA_SKILL_PATH 指向 SKILL.md。".format(suffix)
    )
