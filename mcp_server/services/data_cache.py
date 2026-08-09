"""Optional Parquet cache for historical bars; metadata stays inspectable."""

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable

from mcp_server.domain.identifiers import normalize_ticker


class ParquetDataCacheError(RuntimeError):
    pass


class ParquetDataCache:
    def __init__(self, root: Path):
        self.root = Path(root)

    def path_for(self, code: str, source_version: str) -> Path:
        try:
            normalized, _ = normalize_ticker(code)
        except ValueError:
            normalized = str(code)
        safe_source = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(source_version))
        return self.root / "{}_{}.parquet".format(normalized, safe_source)

    def write(
        self,
        code: str,
        bars: Iterable[Dict[str, Any]],
        provenance: Dict[str, Any],
    ) -> Path:
        try:
            import pandas as pd
        except ImportError as exc:
            raise ParquetDataCacheError("写入 Parquet 需要 pandas") from exc
        path = self.path_for(code, provenance.get("source_version", "unknown"))
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            pd.DataFrame(list(bars)).to_parquet(path, index=False)
        except (ImportError, ValueError, OSError) as exc:
            raise ParquetDataCacheError(
                "写入 Parquet 需要可用的 pyarrow 或 fastparquet：{}".format(exc)
            ) from exc
        path.with_suffix(".metadata.json").write_text(
            json.dumps(provenance, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return path

    def read(self, code: str, source_version: str):
        try:
            import pandas as pd
        except ImportError as exc:
            raise ParquetDataCacheError("读取 Parquet 需要 pandas") from exc
        path = self.path_for(code, source_version)
        if not path.exists():
            return []
        try:
            return pd.read_parquet(path).to_dict(orient="records")
        except (ImportError, ValueError, OSError) as exc:
            raise ParquetDataCacheError("读取 Parquet 失败：{}".format(exc)) from exc

    def read_metadata(self, code: str, source_version: str) -> Dict[str, Any]:
        path = self.path_for(code, source_version).with_suffix(".metadata.json")
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            raise ParquetDataCacheError("读取 Parquet 元数据失败：{}".format(exc)) from exc
        return payload if isinstance(payload, dict) else {}
