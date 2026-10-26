#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
_intake_log.py —— 录入台账（内部模块，不单独运行）

作用：每次成功录入后追加一行 `data/intake_log.jsonl`，记录「这一条是从哪张图来的」。
用途：给 `audit_entries.py` 做「发了但没录」的对账依据（见 docs/04-决策记录.md D-16）。

台账字段：
  logged_at   写入时刻（本机时间，ISO）
  date        数据归属日期 YYYY-MM-DD
  kind        body | meal | waist
  blob_id     源图 sha256（WorkBuddy 附件指纹）—— 对账主键；纯打字录入时为 null
  photo_name  源图原始文件名（便于人眼核对）
  summary     一行摘要（如"144.6 斤 → 72.3 kg"）
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

# ── [market] 可移植根目录解析：--root > HEALTH_ROOT > ~/health-data ──
def _resolve_health_root() -> Path:
    import os as _os
    import sys as _sys
    _argv = _sys.argv
    for _i, _a in enumerate(list(_argv)):
        if _a == "--root" and _i + 1 < len(_argv):
            _v = _argv[_i + 1]
            del _argv[_i:_i + 2]
            return Path(_v).expanduser().resolve()
        if _a.startswith("--root="):
            _v = _a.split("=", 1)[1]
            del _argv[_i]
            return Path(_v).expanduser().resolve()
    _env = _os.environ.get("HEALTH_ROOT")
    if _env:
        return Path(_env).expanduser().resolve()
    return (Path.home() / "health-data").resolve()


ROOT = _resolve_health_root()
LOG_PATH = ROOT / "data" / "intake_log.jsonl"


def log_intake(
    kind: str,
    d: str,
    summary: str,
    blob_id: str | None = None,
    photo_name: str | None = None,
    bodytime: str | None = None,
) -> None:
    """追加一条录入台账。任何异常都不应影响主流程，故整体吞掉异常。"""
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        rec = {
            "logged_at": datetime.now().isoformat(timespec="seconds"),
            "date": d,
            "kind": kind,
            "blob_id": blob_id or None,
            "photo_name": photo_name or None,
            "summary": summary,
        }
        if bodytime:
            rec["time"] = bodytime
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def blob_id_from_path(photo: str | None) -> str | None:
    """从 `.../blobs/<ab>/<sha256>.jpg` 反推 blob_id；拿不到就返回 None。"""
    if not photo:
        return None
    p = Path(photo)
    if p.parent.parent.name == "blobs":
        return p.stem
    return None
