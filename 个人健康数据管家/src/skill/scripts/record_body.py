#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
record_body.py —— 体重 / 体脂 / 腰围 录入

单位约定（见 docs/04-决策记录.md D-13）：
  - 输入单位由 config.yaml 的 profile.weight_unit 决定（kg / jin），可用 --unit 临时覆盖。
  - **数据库一律存 kg**，换算只发生在输入与显示两处。
  - 1 斤 = 0.5 kg。

照片策略（见 D-14）：
  - 照片只用于「取数」，**取完即删**，不留档。
  - 只删 photos/inbox（config 的 landing_dir）下的文件；别处的文件不动。
  - 需要保留时加 --keep-photo。

用法：
    # 拍秤面照片录入（从对话附件读图，不传 --photo；默认斤，自动换算成 kg 存库）
    python scripts/record_body.py --weight 144.6 --blob-id <sha256>

    # 顺带录腰围（cm）
    python scripts/record_body.py --weight 144.6 --waist 88.5

    # 纯打字
    python scripts/record_body.py --weight 144.6 --source manual

    # 临时按公斤录入（覆盖 config）
    python scripts/record_body.py --weight 72.3 --unit kg

    # 补录 / 查看
    python scripts/record_body.py --date 2026-10-09 --weight 145.2
    python scripts/record_body.py --show

--blob-id 是 WorkBuddy 附件的 sha256 指纹，写在录入台账里，
供 audit_entries.py 对账「发了但没录」（见 docs/04-决策记录.md D-16）。
从对话附件（.workbuddy/blobs/<ab>/<sha256>.<ext>）录入时，不传也会自动从路径反推。
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date as Date, datetime
from pathlib import Path

import yaml

try:  # Windows 控制台默认 GBK，中文输出需要显式切 UTF-8
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from _intake_log import log_intake, blob_id_from_path

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
CFG = yaml.safe_load((ROOT / "config" / "config.yaml").read_text(encoding="utf-8"))
DB_PATH = ROOT / CFG["paths"]["db"]
HEIGHT_CM = float(CFG["profile"]["height_cm"])
DEFAULT_UNIT = str(CFG["profile"].get("weight_unit", "kg")).lower()
GOAL_W_KG = CFG["goals"].get("weight_kg")
GOAL_WAIST = CFG["goals"].get("waist_cm")
ABDO_WAIST = CFG["thresholds"].get("waist_abdominal_obesity_cm", 90)
LANDING = Path(CFG["sources"]["photos"]["landing_dir"])

SOURCES = ("photo", "manual", "scale_mcp")
JIN_PER_KG = 2.0  # 1 kg = 2 斤

# 换算成 kg 之后的合理范围
KG_LIMITS = {"weight": (25.0, 250.0), "bodyfat": (3.0, 60.0), "muscle": (10.0, 100.0)}
WAIST_LIMIT = (45.0, 160.0)
LABEL = {"weight": "体重", "bodyfat": "体脂率", "muscle": "肌肉量", "waist": "腰围"}


def to_kg(v: float, unit: str) -> float:
    return v / JIN_PER_KG if unit == "jin" else v


def from_kg(v: float, unit: str) -> float:
    return v * JIN_PER_KG if unit == "jin" else v


def fmt_weight(kg: float, unit: str) -> str:
    """按习惯单位显示体重，斤时附带 kg 原文。"""
    if unit == "jin":
        return f"{from_kg(kg, unit):.1f} 斤（{kg:.1f} kg）"
    return f"{kg:.1f} kg"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="体重/体脂/腰围录入（输入单位按 config 的 weight_unit，默认斤）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--date", help="YYYY-MM-DD，默认今天")
    p.add_argument("--weight", type=float, help=f"体重（单位按 --unit，默认 {DEFAULT_UNIT}）")
    p.add_argument("--bodyfat", type=float, help="体脂率百分比（可选；绝对值不可信，仅看趋势）")
    p.add_argument("--muscle", type=float, help="肌肉量 kg（可选，不参与单位换算）")
    p.add_argument("--waist", type=float, help="腰围 cm（可选，每周 1-2 次即可）")
    p.add_argument("--unit", choices=["kg", "jin"], help=f"体重输入单位，默认 {DEFAULT_UNIT}")
    p.add_argument("--photo", help="照片路径；默认取完数即删（仅限 inbox 内）")
    p.add_argument("--blob-id", dest="blob_id",
                   help="源图 blob_id（WorkBuddy 附件指纹）。用于「漏记巡检」对账，"
                        "从对话附件录数时请带上（见 docs/04-决策记录.md D-16）")
    p.add_argument("--photo-name", dest="photo_name", help="源图原始文件名（可选，便于人眼核对）")
    p.add_argument("--keep-photo", action="store_true", help="保留源照片（默认删除）")
    p.add_argument("--source", default="photo", choices=SOURCES, help="数据来源，默认 photo")
    p.add_argument("--note", help="备注")
    p.add_argument("--force", action="store_true", help="跳过范围校验强制写入")
    p.add_argument("--show", action="store_true", help="只显示历史记录，不写入")
    return p.parse_args()


def validate(name: str, kg_val: float | None, unit: str, force: bool) -> bool:
    """按 kg 值做范围校验。"""
    if kg_val is None:
        return True
    lo, hi = KG_LIMITS[name]
    if kg_val < lo or kg_val > hi:
        shown = f"{from_kg(kg_val, unit):.1f} {unit}" if name == "weight" else f"{kg_val:g}"
        print(f"❌ {LABEL[name]} = {shown}（= {kg_val:.1f} kg）超出合理范围 [{lo}, {hi}] kg，拒绝写入。")
        if name == "weight" and unit == "kg" and kg_val >= 100:
            print(f"   若秤是「斤」，该值应为 {from_kg(kg_val, 'jin'):.1f} 斤。用 --unit jin 重新录入。")
        return False
    if name == "weight" and unit == "kg" and kg_val >= 100 and not force:
        print(f"⚠️  体重 {kg_val:g} kg 偏大，疑似以「斤」为单位。")
        print(f"    若是斤，应为 {from_kg(kg_val, 'jin'):.1f} 斤。请改用 --unit jin，或确认无误加 --force。")
        return False
    return True


def consume_photo(photo: str | None, keep: bool) -> None:
    """取数后处置照片：默认删除，且只删 inbox 内的文件。"""
    if not photo:
        return
    p = Path(photo)
    if not p.is_absolute():
        p = ROOT / p
    if not p.exists():
        print(f"⚠️  照片不存在，跳过：{p}")
        return
    if keep:
        print(f"📁 照片保留：{p}")
        return
    try:
        inside = p.resolve().is_relative_to(LANDING.resolve())
    except Exception:
        inside = False
    if not inside:
        print(f"⚠️  该照片不在 inbox（{LANDING.name}）内，为安全起见不删除：{p}")
        return
    p.unlink()
    print(f"🗑  源照片已删除（不留档）：{p.name}")


def show_recent(con: sqlite3.Connection, unit: str, n: int = 12) -> None:
    print("=== 最近体重记录 ===")
    rows = list(con.execute(
        "SELECT date, weight_kg, bmi, body_fat_pct, waist_cm, source "
        "FROM v_body_trend ORDER BY date DESC LIMIT ?", (n,)
    ))
    if not rows:
        print("  （暂无记录）")
    else:
        u = "斤" if unit == "jin" else "kg"
        print(f"  {'日期':<12}{'体重(' + u + ')':>10}{'体重kg':>9}{'BMI':>7}{'体脂%':>8}{'腰围cm':>8}  来源")
        for r in rows:
            g = lambda v, w=8, p=1: ("—" if v is None else f"{v:.{p}f}").rjust(w)
            wt = "—".rjust(10) if r["weight_kg"] is None else f"{from_kg(r['weight_kg'], unit):.1f}".rjust(10)
            print(f"  {r['date']:<12}{wt}{g(r['weight_kg'], 9)}{g(r['bmi'], 7, 2)}"
                  f"{g(r['body_fat_pct'], 8)}{g(r['waist_cm'], 8)}  {r['source'] or ''}")
    print()
    print("=== 腰围记录 ===")
    wrows = list(con.execute("SELECT date, waist_cm FROM waist ORDER BY date DESC LIMIT 8"))
    if not wrows:
        print("  （暂无记录）")
    for r in wrows:
        flag = f"  ⚠️ 腹型肥胖区（≥{ABDO_WAIST}）" if r["waist_cm"] and r["waist_cm"] >= ABDO_WAIST else ""
        print(f"  {r['date']}  {r['waist_cm']:g} cm{flag}")


def main() -> int:
    a = parse_args()
    unit = (a.unit or DEFAULT_UNIT).lower()
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row

    if a.show:
        show_recent(con, unit)
        return 0

    d = a.date or Date.today().isoformat()
    try:
        Date.fromisoformat(d)
    except ValueError:
        print(f"❌ 日期格式不对：{d}（应为 YYYY-MM-DD）")
        return 1

    if not any(v is not None for v in (a.weight, a.bodyfat, a.muscle, a.waist)):
        print("❌ 至少要提供 --weight / --bodyfat / --muscle / --waist 之一。")
        print("   例：--weight 144.6 --waist 88.5")
        return 1

    # 体重按输入单位换算成 kg；其余指标单位固定
    w_kg = to_kg(a.weight, unit) if a.weight is not None else None

    if not validate("weight", w_kg, unit, a.force):
        return 1
    for k, v in (("bodyfat", a.bodyfat), ("muscle", a.muscle)):
        if not validate(k, v, unit, a.force):
            return 1
    if a.waist is not None and not (WAIST_LIMIT[0] <= a.waist <= WAIST_LIMIT[1]):
        print(f"❌ 腰围 = {a.waist} 超出合理范围 {WAIST_LIMIT}，拒绝写入。")
        return 1

    now = datetime.now().isoformat(timespec="seconds")
    wrote = []

    if any(v is not None for v in (w_kg, a.bodyfat, a.muscle)):
        bmi = round(w_kg / (HEIGHT_CM / 100) ** 2, 1) if w_kg is not None else None
        con.execute(
            """
            INSERT INTO body_metric
              (date, weight_kg, bmi, body_fat_pct, muscle_mass_kg, source, photo_path, note, fetched_at)
            VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(date) DO UPDATE SET
              weight_kg      = COALESCE(excluded.weight_kg,      body_metric.weight_kg),
              bmi            = COALESCE(excluded.bmi,            body_metric.bmi),
              body_fat_pct   = COALESCE(excluded.body_fat_pct,   body_metric.body_fat_pct),
              muscle_mass_kg = COALESCE(excluded.muscle_mass_kg, body_metric.muscle_mass_kg),
              source         = excluded.source,
              note           = COALESCE(excluded.note,           body_metric.note),
              fetched_at     = excluded.fetched_at
            """,
            (d, w_kg, bmi, a.bodyfat, a.muscle, a.source, None, a.note, now),
        )
        wrote.append("body_metric")

    if a.waist is not None:
        con.execute(
            """
            INSERT INTO waist (date, waist_cm, note, source, photo_path)
            VALUES (?,?,?,?,?)
            ON CONFLICT(date) DO UPDATE SET
              waist_cm = excluded.waist_cm,
              note     = COALESCE(excluded.note, waist.note),
              source   = excluded.source
            """,
            (d, a.waist, a.note, a.source, None),
        )
        wrote.append("waist")

    con.commit()

    # ---- 照片处置（取完数即删）----
    consume_photo(a.photo, a.keep_photo)

    # ---- 录入台账（供 audit_entries.py 对账「发了但没录」）----
    bid = a.blob_id or blob_id_from_path(a.photo)
    bits = []
    if w_kg is not None:
        bits.append(f"体重 {fmt_weight(w_kg, unit)}")
    if a.bodyfat is not None:
        bits.append(f"体脂 {a.bodyfat:g}%")
    if a.muscle is not None:
        bits.append(f"肌肉 {a.muscle:g}kg")
    if a.waist is not None:
        bits.append(f"腰围 {a.waist:g}cm")
    log_intake("body", d, "；".join(bits), blob_id=bid,
               photo_name=a.photo_name or (Path(a.photo).name if a.photo else None))

    # ---- 摘要 ----
    print()
    print(f"✅ 已写入 {d}  →  {', '.join(wrote)}")
    if w_kg is not None:
        print(f"   体重 {fmt_weight(w_kg, unit)}   BMI {bmi:g}")
    if a.bodyfat is not None:
        print(f"   体脂 {a.bodyfat:g} %（绝对值不可信，只看趋势）")
    if a.muscle is not None:
        print(f"   肌肉量 {a.muscle:g} kg")
    if a.waist is not None:
        flag = f"   ⚠️ 已进入腹型肥胖区（≥{ABDO_WAIST} cm）" if a.waist >= ABDO_WAIST else ""
        print(f"   腰围 {a.waist:g} cm{flag}")
    if a.note:
        print(f"   备注 {a.note}")

    # ---- 目标与趋势 ----
    print()
    print("📊 对照")
    if w_kg is not None and GOAL_W_KG:
        unit_lbl = "斤" if unit == "jin" else "kg"
        cur, goal = from_kg(w_kg, unit), from_kg(GOAL_W_KG, unit)
        diff = cur - goal
        state = f"还差 {diff:.1f} {unit_lbl}" if diff > 0 else f"已达成（低 {abs(diff):.1f} {unit_lbl}）"
        print(f"   体重 {cur:.1f} / 目标 {goal:.1f} {unit_lbl}   {state}")
    if a.waist is not None and GOAL_WAIST:
        diff = a.waist - GOAL_WAIST
        print(f"   腰围 {a.waist:g} / 目标 {GOAL_WAIST:g} cm   "
              f"{'还差 %.1f cm' % diff if diff > 0 else '已达成'}")

    hist = list(con.execute(
        "SELECT date, weight_kg FROM body_metric WHERE weight_kg IS NOT NULL "
        "ORDER BY date DESC LIMIT 8"
    ))
    if len(hist) >= 2:
        newest, oldest = hist[0], hist[-1]
        span = (Date.fromisoformat(newest["date"]) - Date.fromisoformat(oldest["date"])).days
        delta = newest["weight_kg"] - oldest["weight_kg"]
        arrow = "↓" if delta < 0 else ("↑" if delta > 0 else "→")
        unit_lbl = "斤" if unit == "jin" else "kg"
        print(f"   近 {span} 天：{from_kg(oldest['weight_kg'], unit):.1f} → "
              f"{from_kg(newest['weight_kg'], unit):.1f} {unit_lbl}  {arrow} {abs(from_kg(delta, unit)):.1f}")

    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
