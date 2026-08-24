"""Safe JSON/YAML interchange with upstream and common worldbook exports."""

from __future__ import annotations

import json
import time
from typing import Any

import yaml

from .models import EntryValidationError, WorldTreeEntry

MAX_IMPORT_BYTES = 2 * 1024 * 1024


def load_entries_from_bytes(data: bytes, filename: str) -> list[dict[str, Any]]:
    """Read a bounded JSON/YAML lorebook without trusting its object shape."""

    if not data:
        raise EntryValidationError("导入文件为空")
    if len(data) > MAX_IMPORT_BYTES:
        raise EntryValidationError("导入文件不能超过 2 MiB")
    suffix = filename.rsplit(".", 1)[-1].casefold() if "." in filename else ""
    if suffix not in {"json", "yaml", "yml"}:
        raise EntryValidationError("只支持 .json、.yaml 或 .yml 文件")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise EntryValidationError("导入文件必须使用 UTF-8 编码") from exc
    try:
        payload = json.loads(text) if suffix == "json" else yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise EntryValidationError(f"无法解析导入文件：{exc}") from exc
    return _extract_entries(payload)


def dump_entries(entries: list[WorldTreeEntry], export_format: str) -> bytes:
    """Create a portable WorldTree Lore v1 document."""

    export_format = export_format.casefold()
    if export_format not in {"json", "yaml", "yml"}:
        raise EntryValidationError("导出格式只能是 json 或 yaml")
    document = {
        "format": "worldtree-lore/v1",
        "exported_at": int(time.time()),
        "entries": [entry.to_dict() for entry in entries],
    }
    if export_format == "json":
        return json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8")
    return yaml.safe_dump(document, allow_unicode=True, sort_keys=False).encode("utf-8")


def _extract_entries(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [_normalise_source_item(item, index) for index, item in enumerate(payload, 1)]
    if not isinstance(payload, dict):
        raise EntryValidationError("文件顶层必须是条目列表或包含 entries 的对象")

    candidates: Any
    if "entries" in payload:
        candidates = payload["entries"]
    elif "world_info" in payload:
        candidates = payload["world_info"]
    elif "content" in payload:
        candidates = [payload]
    else:
        raise EntryValidationError("未找到 entries 或 world_info 字段")

    if isinstance(candidates, dict):
        candidates = list(candidates.values())
    if not isinstance(candidates, list):
        raise EntryValidationError("entries 必须是列表或对象")
    return [_normalise_source_item(item, index) for index, item in enumerate(candidates, 1)]


def _normalise_source_item(item: Any, index: int) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise EntryValidationError(f"第 {index} 项不是对象")
    result = dict(item)

    # WorldTree and upstream Worldbook use name/keywords. Common
    # SillyTavern-style files use comment/key/order/disable instead.
    if not result.get("name"):
        raw_key = result.get("key") or result.get("keys") or []
        if isinstance(raw_key, str):
            fallback = raw_key.split(",", 1)[0].strip()
        elif isinstance(raw_key, list) and raw_key:
            fallback = str(raw_key[0]).strip()
        else:
            fallback = ""
        result["name"] = str(result.get("comment") or fallback or f"导入条目 {index}")
    if "keywords" not in result:
        result["keywords"] = result.get("key", result.get("keys", []))
    if "template" not in result and result.get("__template_key"):
        result["template"] = result["__template_key"]
    if "priority" not in result:
        result["priority"] = result.get("order", result.get("insertion_order", 50))
    if "enabled" not in result and "disable" in result:
        result["enabled"] = not bool(result["disable"])
    result.setdefault("enabled", True)
    result.setdefault("content", "")

    # Upstream Worldbook treats every keyword as a regex. Preserve that only
    # for its recognisable template-bearing records; new WorldTree entries use
    # safe literal matching unless users explicitly write re:<pattern>.
    if "keyword_mode" not in result and (
        "template" in result or "__template_key" in result
    ):
        result["keyword_mode"] = "legacy_regex"
    return result
