#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
db_status.py —— 数据源状态巡检 / 清理测试行

用法：
    python scripts/db_status.py                # 只看来源分布与时间覆盖
    python scripts/db_status.py --purge-demo   # 清掉所有 source='demo' 的演示数据
    python scripts/db_status.py --purge-date 2000-01-01   # 清掉指定日期的所有行
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

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
DB = ROOT / "data" / "health.db"

TABLES = [
    "activity_daily", "heart_rate", "sleep_session", "spo2", "stress",
    "workout", "body_metric", "waist", "meal", "portion_library", "daily_review",
]
DATE_COL = {
    "activity_daily": "date", "heart_rate": "ts", "sleep_session": "date",
    "spo2": "ts", "stress": "ts", "workout": "start_ts",
    "body_metric": "date", "waist": "date", "meal": "date",
    "portion_library": None, "daily_review": "date",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--purge-demo", action="store_true", help="删除所有 source='demo' 的行")
    ap.add_argument("--purge-date", help="删除指定日期的所有行（YYYY-MM-DD）")
    ap.add_argument("--purge-library", action="store_true", help="清空个人份量库（portion_library）")
    a = ap.parse_args()

    con = sqlite3.connect(DB)

    if a.purge_library:
        n = con.execute("DELETE FROM portion_library").rowcount
        con.commit()
        print(f"  清空 portion_library：{n} 行\n")

    if a.purge_demo or a.purge_date:
        total = 0
        for t in TABLES:
            if a.purge_demo:
                n = con.execute(f"DELETE FROM {t} WHERE source='demo'").rowcount
            else:
                col = DATE_COL[t]
                if not col:
                    continue
                n = con.execute(f"DELETE FROM {t} WHERE substr({col},1,10)=?", (a.purge_date,)).rowcount
            if n:
                print(f"  删除 {t:<18} {n} 行")
                total += n
        con.commit()
        print(f"\n共删除 {total} 行")
        print()

    print("=== 各表行数与来源分布 ===")
    for t in TABLES:
        if t == "portion_library":
            n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            print(f"  {t:<18} {n:>5} 行")
            continue
        rows = con.execute(
            f"SELECT COALESCE(source,'(空)') s, COUNT(*) n FROM {t} GROUP BY s ORDER BY n DESC"
        ).fetchall()
        n = sum(r[1] for r in rows)
        dist = "  ".join(f"{s}:{c}" for s, c in rows) or "（空）"
        print(f"  {t:<18} {n:>5} 行   {dist}")

    print()
    print("=== 时间覆盖 ===")
    for t, col in DATE_COL.items():
        if not col:
            continue
        try:
            lo, hi = con.execute(
                f"SELECT MIN(substr({col},1,10)), MAX(substr({col},1,10)) FROM {t}"
            ).fetchone()
            if lo:
                print(f"  {t:<18} {lo}  →  {hi}")
        except Exception:
            pass

    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
