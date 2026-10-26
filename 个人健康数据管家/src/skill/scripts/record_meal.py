#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
record_meal.py —— 餐食录入（照片识别结果 → 落库）

定位：本脚本**不做识别**。识别由 AI（多模态）完成，本脚本只负责把识别结果
     结构化写入 `meal` 表，并维护个人份量库 `portion_library`。

核心约定（见 docs/04-决策记录.md D-02 / D-07 / D-08）：
  1. **热量一律以区间呈现**。只给点值时会自动展开 ±15%，并标注为「点值展开」。
  2. 纤维等照片判不出的指标不录；改用 `--veg`（蔬菜份数）这类定性指标。
  3. 常吃的菜走份量库 `--from-library`，越用越准——这比提升单张识别精度更有效。
  4. 照片只用于取数，**默认取完即删**（只删 photos/inbox 内的文件）。

用法：
    # AI 识读后写入（区间；从对话附件读图，不传 --photo）
    python scripts/record_meal.py --meal-type lunch --desc "家做西红柿炒蛋+米饭一碗+一拳青菜" \
        --place 家 --kcal-low 420 --kcal-high 560 --protein-low 18 --protein-high 24 \
        --veg 1 --carb mid --confidence mid --blob-id <sha256>

    # 走份量库（复用历史估算值，并累计使用次数）
    python scripts/record_meal.py --meal-type breakfast --desc "水煮蛋" --from-library "水煮蛋"

    # 把这餐存进份量库备以后复用
    python scripts/record_meal.py --meal-type lunch --desc "公司食堂青椒炒肉" \
        --kcal-low 320 --kcal-high 460 --protein-low 20 --protein-high 24 --save-to-library

    # 查看
    python scripts/record_meal.py --show
    python scripts/record_meal.py --day 2026-10-11
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date as Date, datetime
from pathlib import Path

import yaml

try:
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
LANDING = Path(CFG["sources"]["photos"]["landing_dir"])

MEAL_TYPES = {"breakfast": "早餐", "lunch": "午餐", "dinner": "晚餐", "snack": "加餐"}
LEVELS = {"low": "低", "mid": "中", "high": "高"}
CONF = {"low": "低", "mid": "中", "high": "高"}
PLACE_OPTS = ("家", "食堂", "外食", "外卖", "其他")

# 目标（用于当日汇总对照）
GOALS = CFG["goals"]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="餐食录入（热量区间 + 定性指标）",
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--date", help="YYYY-MM-DD，默认今天")
    p.add_argument("--time", help="HH:MM，默认当前时间")
    p.add_argument("--meal-type", choices=list(MEAL_TYPES), help="早餐/午餐/晚餐/加餐")
    p.add_argument("--desc", help="吃了什么（一句话，越具体越好）")
    p.add_argument("--place", choices=PLACE_OPTS, help="就餐地点")
    p.add_argument("--kcal-low", type=int, help="热量区间下界 kcal")
    p.add_argument("--kcal-high", type=int, help="热量区间上界 kcal")
    p.add_argument("--kcal", type=int, help="只给点值时的热量（自动展开 ±15%%）")
    p.add_argument("--protein-low", type=float, help="蛋白质下界 g")
    p.add_argument("--protein-high", type=float, help="蛋白质上界 g")
    p.add_argument("--protein", type=float, help="蛋白质点值（自动展开 ±15%%）")
    p.add_argument("--calcium-low", type=float, help="钙下界 mg（2026-10-22 起追踪，见 D-47）")
    p.add_argument("--calcium-high", type=float, help="钙上界 mg")
    p.add_argument("--veg", type=float, help="蔬菜份数（以「拳」计）")
    p.add_argument("--carb", choices=list(LEVELS), help="碳水档位")
    p.add_argument("--confidence", choices=list(CONF), default="mid", help="估算置信度")
    p.add_argument("--from-library", dest="from_lib", metavar="名称", help="从份量库取基准值")
    p.add_argument("--save-to-library", action="store_true", help="同时存入份量库")
    p.add_argument("--photo", help="照片路径；默认取完数即删（仅限 inbox 内）")
    p.add_argument("--blob-id", dest="blob_id",
                   help="源图 blob_id（WorkBuddy 附件指纹）。用于「漏记巡检」对账，"
                        "从对话附件录数时请带上（见 docs/04-决策记录.md D-16）")
    p.add_argument("--photo-name", dest="photo_name", help="源图原始文件名（可选，便于人眼核对）")
    p.add_argument("--keep-photo", action="store_true", help="保留源照片（默认删除）")
    p.add_argument("--note", help="备注")
    p.add_argument("--show", action="store_true", help="显示最近记录与近 7 日汇总")
    p.add_argument("--day", help="显示指定日（YYYY-MM-DD）的明细与汇总")
    return p.parse_args()


def consume_photo(photo: str | None, keep: bool) -> None:
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
        print(f"⚠️  该照片不在 inbox 内，为安全起见不删除：{p}")
        return
    p.unlink()
    print(f"🗑  源照片已删除（不留档）：{p.name}")


def expand(v: float | None, lo: float | None, hi: float | None) -> tuple[float | None, float | None, bool]:
    """返回 (下界, 上界, 是否由点值展开)。"""
    if v is not None and lo is None and hi is None:
        return round(v * 0.85, 1), round(v * 1.15, 1), True
    return lo, hi, False


def lookup_library(con: sqlite3.Connection, name: str) -> dict | None:
    r = con.execute("SELECT * FROM portion_library WHERE name = ?", (name,)).fetchone()
    if not r:
        # 退化匹配：包含关系
        r = con.execute("SELECT * FROM portion_library WHERE name LIKE ? LIMIT 1", (f"%{name}%",)).fetchone()
    return dict(r) if r else None


def upsert_library(con: sqlite3.Connection, name: str, unit: str, lo, hi, protein, note, source) -> None:
    con.execute(
        """
        INSERT INTO portion_library (name, unit, kcal_low, kcal_high, protein_g, times_used, last_used, confirmed, note, source)
        VALUES (?,?,?,?,?,1,?,0,?,?)
        ON CONFLICT(name) DO UPDATE SET
          unit      = COALESCE(excluded.unit, portion_library.unit),
          kcal_low  = COALESCE(excluded.kcal_low, portion_library.kcal_low),
          kcal_high = COALESCE(excluded.kcal_high, portion_library.kcal_high),
          protein_g = COALESCE(excluded.protein_g, portion_library.protein_g),
          times_used = portion_library.times_used + 1,
          last_used = excluded.last_used,
          note      = COALESCE(excluded.note, portion_library.note)
        """,
        (name, unit, lo, hi, protein, (Date.today()).isoformat(), note, source),
    )


def bump_library(con: sqlite3.Connection, name: str) -> None:
    con.execute("UPDATE portion_library SET times_used = times_used + 1, last_used = ? WHERE name = ?",
                (Date.today().isoformat(), name))


def day_summary(con: sqlite3.Connection, d: str) -> None:
    rows = list(con.execute(
        "SELECT meal_type, time, description, place, kcal_low, kcal_high, "
        "protein_g_low, protein_g_high, veg_servings, carb_level, confidence, note "
        "FROM meal WHERE date = ? ORDER BY time", (d,)
    ))
    if not rows:
        print(f"  {d}：无记录")
        return
    print(f"  {d} 明细：")
    for r in rows:
        mt = MEAL_TYPES.get(r["meal_type"], r["meal_type"] or "—")
        kcal = (f"{r['kcal_low']}–{r['kcal_high']} kcal"
                if r["kcal_low"] and r["kcal_high"] else "—")
        pr = (f"{r['protein_g_low']:g}–{r['protein_g_high']:g} g"
              if r["protein_g_low"] and r["protein_g_high"] else "—")
        veg = f"{r['veg_servings']:g} 拳" if r["veg_servings"] else "—"
        print(f"    {r['time'] or '--:--'}  {mt:<4} {r['description'] or ''}")
        print(f"          热量 {kcal} | 蛋白 {pr} | 蔬菜 {veg} | "
              f"碳水 {LEVELS.get(r['carb_level'], '—')} | 置信度 {CONF.get(r['confidence'], '—')}"
              + (f" | {r['place']}" if r["place"] else ""))
        if r["note"]:
            print(f"          备注 {r['note']}")

    s = con.execute(
        "SELECT COUNT(*) n, SUM(kcal_low) kl, SUM(kcal_high) kh, "
        "SUM(protein_g_low) pl, SUM(protein_g_high) ph, SUM(veg_servings) v "
        "FROM meal WHERE date = ?", (d,)
    ).fetchone()
    print()
    print(f"  合计：{s['n']} 餐 | 热量 {s['kl'] or 0}–{s['kh'] or 0} kcal "
          f"| 蛋白 {s['pl'] or 0:g}–{s['ph'] or 0:g} g | 蔬菜 {s['v'] or 0:g} 拳")


def show_recent(con: sqlite3.Connection) -> None:
    print("=== 最近 7 天饮食汇总 ===")
    rows = list(con.execute(
        "SELECT date, COUNT(*) n, SUM(kcal_low) kl, SUM(kcal_high) kh, "
        "SUM(protein_g_low) pl, SUM(protein_g_high) ph, SUM(veg_servings) v "
        "FROM meal GROUP BY date ORDER BY date DESC LIMIT 7"
    ))
    if not rows:
        print("  （暂无记录）")
    else:
        print(f"  {'日期':<12}{'餐数':>5}{'热量区间(kcal)':>20}{'蛋白(g)':>14}{'蔬菜(拳)':>9}")
        for r in rows:
            kcal = f"{r['kl'] or 0}–{r['kh'] or 0}"
            prot = f"{r['pl'] or 0:g}–{r['ph'] or 0:g}"
            print(f"  {r['date']:<12}{r['n']:>5}{kcal:>20}{prot:>14}{r['v'] or 0:>9g}")
    print()
    print("=== 个人份量库（越用越准）===")
    lib = list(con.execute(
        "SELECT name, unit, kcal_low, kcal_high, protein_g, times_used, last_used "
        "FROM portion_library ORDER BY times_used DESC LIMIT 12"
    ))
    if not lib:
        print("  （空——第一次记录常吃的菜时加 --save-to-library）")
    for r in lib:
        print(f"  {r['name']:<28} {r['kcal_low']}–{r['kcal_high']} kcal/{r['unit'] or '份'}"
              f"  蛋白 {r['protein_g'] or '—':g} g  用过 {r['times_used']} 次（最近 {r['last_used'] or '—'}）")


def main() -> int:
    a = parse_args()
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row

    if a.show:
        show_recent(con)
        return 0
    if a.day:
        d = a.day
        try:
            Date.fromisoformat(d)
        except ValueError:
            print(f"❌ 日期格式不对：{d}")
            return 1
        day_summary(con, d)
        return 0

    if not a.desc:
        print("❌ 需要 --desc（吃了什么）。")
        return 1
    mt = a.meal_type or "snack"
    d = a.date or Date.today().isoformat()
    t = a.time or datetime.now().strftime("%H:%M")

    kl, kh = a.kcal_low, a.kcal_high
    pl, ph = a.protein_low, a.protein_high
    lib_name = None

    if a.from_lib:
        row = lookup_library(con, a.from_lib)
        if not row:
            print(f"⚠️  份量库里没有「{a.from_lib}」，本次按新数据记录。")
        else:
            lib_name = row["name"]
            kl = kl if kl is not None else row["kcal_low"]
            kh = kh if kh is not None else row["kcal_high"]
            pl = pl if pl is not None else round(row["protein_g"] * 0.9, 1) if row["protein_g"] else pl
            ph = ph if ph is not None else round(row["protein_g"] * 1.1, 1) if row["protein_g"] else ph
            if not a.note and row["note"]:
                a.note = row["note"]
            print(f"📚 命中份量库「{row['name']}」：{row['kcal_low']}–{row['kcal_high']} kcal"
                  f"，蛋白 {row['protein_g'] or '—'} g（已用 {row['times_used']} 次）")

    kl, kh, expanded_k = expand(a.kcal, kl, kh)
    pl, ph, expanded_p = expand(a.protein, pl, ph)

    note = a.note
    if expanded_k or expanded_p:
        tag = "点值自动展开 ±15%"
        note = f"{note}；{tag}" if note else tag

    con.execute(
        """
        INSERT INTO meal (date, time, meal_type, photo_path, description, place,
                          kcal_low, kcal_high, protein_g_low, protein_g_high,
                          veg_servings, carb_level, confidence, verified_by_user,
                          note, created_at, source, calcium_mg_low, calcium_mg_high)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (d, t, mt, None, a.desc, a.place, kl, kh, pl, ph,
         a.veg, a.carb, a.confidence, 0, note,
         datetime.now().isoformat(timespec="seconds"), "photo",
         a.calcium_low, a.calcium_high),
    )

    if lib_name:
        bump_library(con, lib_name)
    if a.save_to_library:
        upsert_library(con, a.desc.strip(), "份", kl, kh,
                       round((pl + ph) / 2, 1) if pl is not None and ph is not None else pl,
                       note, "photo")
    con.commit()

    # ---- 照片处置 ----
    consume_photo(a.photo, a.keep_photo)

    # ---- 录入台账（供 audit_entries.py 对账「发了但没录」）----
    bid = a.blob_id or blob_id_from_path(a.photo)
    log_intake("meal", d, f"{MEAL_TYPES.get(mt, mt)} {a.desc}", blob_id=bid,
               photo_name=a.photo_name or (Path(a.photo).name if a.photo else None),
               bodytime=t)

    # ---- 摘要 ----
    print()
    print(f"✅ 已记录 {MEAL_TYPES.get(mt, mt)}  {d} {t}")
    print(f"   {a.desc}" + (f"（{a.place}）" if a.place else ""))
    print(f"   热量 {kl if kl is not None else '—'}–{kh if kh is not None else '—'} kcal"
          f"（置信度：{CONF.get(a.confidence, '—')}）")
    if pl is not None:
        print(f"   蛋白 {pl:g}–{ph:g} g")
    if a.veg is not None:
        print(f"   蔬菜 {a.veg:g} 拳")
    if a.calcium_low is not None:
        print(f"   钙 {a.calcium_low:g}–{a.calcium_high:g} mg（参考 800 mg/天）")
    if a.carb:
        print(f"   碳水档位 {LEVELS.get(a.carb, a.carb)}")
    if a.save_to_library:
        print(f"   📚 已存入份量库，下次可用 --from-library \"{a.desc.strip()}\" 复用")
    if a.note:
        print(f"   备注 {a.note}")

    print()
    print("📊 当日累计（区间口径，不要读成点值）")
    s = con.execute(
        "SELECT COUNT(*) n, SUM(kcal_low) kl, SUM(kcal_high) kh, "
        "SUM(protein_g_low) pl, SUM(protein_g_high) ph, SUM(veg_servings) v, "
        "SUM(calcium_mg_low) cal, SUM(calcium_mg_high) cah "
        "FROM meal WHERE date = ?", (d,)
    ).fetchone()
    print(f"   {s['n']} 餐 | 热量 {s['kl'] or 0}–{s['kh'] or 0} kcal | "
          f"蛋白 {s['pl'] or 0:g}–{s['ph'] or 0:g} g | 蔬菜 {s['v'] or 0:g} 拳"
          + (f" | 钙 {s['cal']:g}–{s['cah']:g} mg" if s['cal'] else ""))

    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
