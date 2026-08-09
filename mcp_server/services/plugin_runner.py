"""Isolated execution for reviewed Python strategy plugins."""

import ast
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterable, Optional


class PluginExecutionError(RuntimeError):
    pass


class PythonStrategyPluginRunner:
    def __init__(
        self,
        timeout_seconds: float = 5.0,
        allowed_dependencies: Optional[Iterable[str]] = None,
    ):
        self.timeout_seconds = timeout_seconds
        self.allowed_dependencies = set(allowed_dependencies or {"json", "math", "statistics"})

    def run(self, plugin_path, context):
        path = Path(plugin_path)
        if not path.is_file():
            raise PluginExecutionError("策略插件不存在：{}".format(path))
        self._validate_source(path)
        with tempfile.TemporaryDirectory(prefix="fireagent-plugin-") as directory:
            root = Path(directory)
            input_path = root / "input.json"
            output_path = root / "output.json"
            input_path.write_text(json.dumps(context, ensure_ascii=False), encoding="utf-8")
            command = [
                sys.executable,
                "-I",
                "-c",
                _launcher_source(),
                str(path),
                str(input_path),
                str(output_path),
            ]
            try:
                completed = subprocess.run(
                    command,
                    cwd=str(root),
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise PluginExecutionError("策略插件执行超时") from exc
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout or "未知错误").strip()
                raise PluginExecutionError("策略插件执行失败：{}".format(detail[-500:]))
            if not output_path.exists():
                raise PluginExecutionError("策略插件没有返回标准输出")
            try:
                result = json.loads(output_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise PluginExecutionError("策略插件输出不是有效 JSON") from exc
            if not isinstance(result, list) or not all(isinstance(item, dict) for item in result):
                raise PluginExecutionError("策略插件必须返回动作对象列表")
            return result

    def _validate_source(self, path: Path):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError) as exc:
            raise PluginExecutionError("策略插件无法解析：{}".format(exc)) from exc
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name.split(".", 1)[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module.split(".", 1)[0]] if node.module else []
            else:
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open":
                    raise PluginExecutionError("策略插件只能通过标准上下文输入输出，禁止直接打开文件")
                continue
            forbidden = [name for name in names if name not in self.allowed_dependencies]
            if forbidden:
                raise PluginExecutionError(
                    "策略插件依赖不在白名单：{}".format(", ".join(sorted(set(forbidden))))
                )


def _launcher_source():
    return (
        "import importlib.util, json, sys\n"
        "plugin_path, input_path, output_path = sys.argv[1:4]\n"
        "spec = importlib.util.spec_from_file_location('fireagent_plugin', plugin_path)\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(module)\n"
        "context = json.load(open(input_path, encoding='utf-8'))\n"
        "actions = module.run(context)\n"
        "json.dump(actions, open(output_path, 'w', encoding='utf-8'), ensure_ascii=False)\n"
    )
