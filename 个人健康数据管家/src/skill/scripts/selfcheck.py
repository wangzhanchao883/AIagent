#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""自检 · 配好了没、能不能正常干活。

输出一张清单：每项标 ✅（正常）/ ⚠️（能跑但要注意）/ ❌（挡住主流程，必须先修）。
退出码：0 = 无 ❌；1 = 有 ❌。

用法：
    python selfcheck.py --root ~/health-data
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNTIME = ".runtime.json"
MARKER = ".setup_done.json"

# 建库脚本应建出的表（少一张就算没建全）
TABLES = ["meta", "activity_daily", "heart_rate", "sleep_session", "spo2",
          "stress", "workout", "body_metric", "waist", "meal",
          "portion_library", "daily_review"]

# 报告渲染强依赖的列（缺了报告会报错）
CRITICAL_COLS = {
    "body_metric": ["date", "weight_kg"],
    "meal": ["kcal_low", "kcal_high"],
    "activity_daily": ["date", "steps"],
    "workout": ["start_ts", "duration_min"],
    "sleep_session": ["total_min"],
}

PLACEHOLDERS = {
    "name": "你的名字",
    "height_cm": 172,
    "birth_year": 1985,
}


class Checker:
    def __init__(self) -> None:
        self.fails = 0
        self.warns = 0

    def ok(self, title: str, detail: str = "") -> None:
        print(f"  ✅ {title}" + (f" — {detail}" if detail else ""))

    def warn(self, title: str, detail: str = "") -> None:
        self.warns += 1
        print(f"  ⚠️  {title}" + (f" — {detail}" if detail else ""))

    def bad(self, title: str, detail: str = "") -> None:
        self.fails += 1
        print(f"  ❌ {title}" + (f" — {detail}" if detail else ""))


def main() -> int:
    ap = argparse.ArgumentParser(description="健康数据管家 · 环境自检")
    ap.add_argument("--root", default=str(Path.home() / "health-data"))
    args = ap.parse_args()
    root = Path(args.root).expanduser().resolve()
    c = Checker()

    print("═" * 56)
    print(" 健康数据管家 · 环境自检")
    print(f" 项目目录：{root}")
    print("═" * 56)

    # ── 1. 运行时 ──
    print("\n[1] 运行时")
    if sys.version_info >= (3, 9):
        c.ok("Python", "%d.%d.%d" % sys.version_info[:3])
    else:
        c.bad("Python 版本过低", "%d.%d.%d，需要 3.9+" % sys.version_info[:3])

    try:
        import yaml  # noqa: F401
        c.ok("PyYAML 可导入")
    except ImportError:
        c.bad("PyYAML 缺失", f"{sys.executable} -m pip install pyyaml")

    rt = root / RUNTIME
    if rt.exists():
        try:
            info = json.loads(rt.read_text(encoding="utf-8"))
            py = info.get("python", "")
            c.ok("解释器已记录", py or "（空）")
            if py and Path(py).exists() is False:
                c.warn("记录的解释器路径已不存在", "换过 Python？重跑 bootstrap.py")
        except Exception as e:  # noqa: BLE001
            c.warn(".runtime.json 读不了", str(e))
    else:
        c.warn("没见过 .runtime.json", "通常说明 bootstrap.py 没跑过，先跑一次")

    # ── 2. 目录与权限 ──
    print("\n[2] 目录")
    for d in ("data", "config", "reports"):
        p = root / d
        if not p.is_dir():
            c.bad(f"目录缺失：{d}", "重跑 bootstrap.py")
            continue
        probe = p / ".write_test"
        try:
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            c.ok(f"{d}/ 可写")
        except OSError as e:
            c.bad(f"{d}/ 不可写", str(e))

    # ── 3. 配置 ──
    print("\n[3] 配置")
    cfg_path = root / "config" / "config.yaml"
    cfg = None
    if not cfg_path.exists():
        c.bad("config/config.yaml 不存在", "重跑 bootstrap.py")
    else:
        try:
            import yaml
            cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            c.ok("config.yaml 可解析")
        except Exception as e:  # noqa: BLE001
            c.bad("config.yaml 解析失败", str(e))
    if isinstance(cfg, dict):
        prof = cfg.get("profile") or {}
        for key, ph in PLACEHOLDERS.items():
            v = prof.get(key)
            if v is None:
                c.bad(f"profile.{key} 缺失", "报告算不出基础代谢")
            elif v == ph:
                c.warn(f"profile.{key} 还是模板默认值", f"当前 {v}，改成你自己的")
            else:
                c.ok(f"profile.{key}", str(v))
        goals = cfg.get("goals") or {}
        if not goals.get("weight_kg"):
            c.warn("goals.weight_kg 未设置", "报告不做「离目标还差多少」判断")
        else:
            c.ok("目标体重", str(goals.get("weight_kg")))
        if not (cfg.get("weight_loss") or {}).get("intake_floor_kcal"):
            c.warn("weight_loss.intake_floor_kcal 缺失", "缺口安全边界判不了，按 1500 兜底")

    # ── 4. 数据库 ──
    print("\n[4] 数据库")
    db = root / "data" / "health.db"
    if not db.exists():
        c.bad("data/health.db 不存在", "重跑 bootstrap.py（会建库）")
    else:
        try:
            con = sqlite3.connect(db)
            have = {r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            miss = [t for t in TABLES if t not in have]
            if miss:
                c.bad(f"缺 {len(miss)} 张表", "、".join(miss))
            else:
                c.ok("表结构完整", f"{len(TABLES)} 张")
            for table, cols in CRITICAL_COLS.items():
                if table not in have:
                    continue
                hc = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
                miss_c = [x for x in cols if x not in hc]
                if miss_c:
                    c.warn(f"{table} 缺列", "、".join(miss_c))
            con.close()
            c.ok("库文件可打开")
        except sqlite3.Error as e:
            c.bad("库打不开", str(e))

    # ── 5. 可选增强 ──
    print("\n[5] 可选增强（不影响主流程）")
    band = ((cfg or {}).get("sources") or {}).get("band") or {}
    if not band.get("enabled"):
        c.warn("活动 / 睡眠数据源未接入", "想接小米手环就说一声「装一下手环同步」")
    else:
        sdb = band.get("source_db") or ""
        if sdb and Path(sdb).expanduser().exists():
            c.ok("手环源库可达", sdb)
        else:
            c.warn("手环已启用但源库找不到", "跑 mi-fitness-mcp doctor 看真实路径")

    # ── 汇总 ──
    print("\n" + "═" * 56)
    if c.fails:
        print(f" ❌ 有 {c.fails} 项挡住主流程，{c.warns} 项提醒 —— 修好 ❌ 再用")
        print("═" * 56)
        return 1
    if c.warns:
        print(f" ✅ 可以正常使用（{c.warns} 项提醒，不挡事）")
    else:
        print(" ✅ 全部通过，可以正常使用")
    print("═" * 56)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
