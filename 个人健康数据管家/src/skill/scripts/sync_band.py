#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
sync_band.py —— 手环数据搬运：MCP 源库 → 主库 health.db

数据链路分三段，本脚本负责第 ②→③ 段：
  ① 小米云端 hlth.io.mi.com
        │  mi-fitness-mcp sync（vendor 里的独立 venv）
        ▼
  ② MCP 源库 mi_fitness.db（规范化后的 8 类数据）
        │  本脚本
        ▼
  ③ 主库 health.db（唯一分析真源）

用法：
    python scripts/sync_band.py                      # 拉最近 7 天并写入主库
    python scripts/sync_band.py --days 14
    python scripts/sync_band.py --start 2026-10-04 --end 2026-10-10
    python scripts/sync_band.py --no-pull            # 不联网，只把源库现有数据搬进主库
    python scripts/sync_band.py --dry-run            # 只报会写什么，不落库
    python scripts/sync_band.py --json               # 机器可读结果

设计约束（与 docs/04-决策记录.md 一致）：
  · 幂等——重复跑不产生重复行（全部 UPSERT / INSERT-OR-IGNORE）
  · 活动 / 睡眠 / 心率 / 血氧 / 压力 / 运动：源库没有的字段留 NULL，不用 0 或估算值填充
  · **体重（body_metrics）**：源库的 body_measurements 视为**体脂秤**数据（2026-10-12 接入），
    按 D-21 方案 A + D-29 时间窗搬运——
      ① 只收**晨起窗（默认 05:00–11:00）内、当日首条**
      ② **先查后写，绝不覆盖**：该日期主库已有记录（照片 / 口述 / 秤）一律不动
    其余时段的称重**不入主表**（body_metric 以 date 为主键，一天一条，见 D-21）。
  · 设备来源不可判——MCP 解析时丢弃了 sid，源库无法区分「手机记的」还是「手环记的」，
    故 source 统一标 mi_fitness；原始行另存 data/raw/mi_fitness/ 供追溯（见 D-17）
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

try:  # Windows 控制台默认 GBK，中文输出需要显式切 UTF-8
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
RAW_DIR = ROOT / "data" / "raw" / "mi_fitness"
CONFIG = ROOT / "config" / "config.yaml"

VENDOR = ROOT / "vendor" / "mi-fitness-mcp-cn"
MCP_EXE = VENDOR / ".venv" / "Scripts" / "mi-fitness-mcp.exe"
MCP_EXE_POSIX = VENDOR / ".venv" / "bin" / "mi-fitness-mcp"

DEFAULT_SRC_DB = (
    Path.home() / "AppData" / "Local" / "mi-fitness-mcp" / "mi-fitness-mcp" / "mi_fitness.db"
)

SOURCE_TAG = "mi_fitness"

# 源库数据类别 → 本脚本是否搬运
PULL_TYPES = [
    "daily_activity",
    "sleep",
    "heart_rate",
    "spo2",
    "stress",
    "workouts",
    "body_measurements",   # 体脂秤（2026-10-12 接入）
]

# 合理性红线：超过这个量级多半是「同一天多个设备各自上报被累加」，打标提示而非静默接受
SANITY_STEPS_MAX = 60000
SANITY_DISTANCE_KM_MAX = 60.0

# ---- 体脂秤搬运口径（D-21 方案 A + D-29）----
# 只有落在这个「晨起窗」内的称重才被视为当日晨起首条；其余时段一律不入主表。
SCALE_WINDOW = ("05:00", "11:00")
SCALE_TZ = timezone(timedelta(hours=8))   # 北京时间（源库时间戳混有 UTC 与 +08:00 两种表示）
SCALE_SOURCE = "scale_mcp"                # body_metric.source 标签（与 record_body.py 的 SOURCES 一致）


# ----------------------------------------------------------------------------- 基础


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def load_cfg() -> dict:
    try:
        import yaml
    except ImportError:
        return {}
    if not CONFIG.exists():
        return {}
    try:
        return yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def resolve_src_db(cfg: dict) -> Path:
    """源库路径：config > 环境变量 > 默认。"""
    p = (cfg.get("sources", {}).get("band", {}) or {}).get("source_db")
    if p:
        return Path(p)
    env = os.environ.get("MI_FITNESS_DB")
    if env:
        return Path(env)
    return DEFAULT_SRC_DB


def to_iso(v) -> str | None:
    """把源库里的时间值统一成 ISO 字符串。"""
    if v is None:
        return None
    s = str(v).strip()
    return s or None


# ----------------------------------------------------------------------------- 拉取


def pull_from_cloud(start: str, end: str, verbose: bool = True) -> tuple[bool, str]:
    """调用 MCP CLI 把云端数据同步进源库。返回 (是否成功, 说明)。"""
    exe = MCP_EXE if MCP_EXE.exists() else (MCP_EXE_POSIX if MCP_EXE_POSIX.exists() else None)
    if exe is None:
        return False, f"找不到 MCP 可执行文件（找过 {MCP_EXE}）"

    cmd = [str(exe), "sync", "--start-date", start, "--end-date", end]
    if verbose:
        print(f"  → 拉取云端：{start} ~ {end}")
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
            cwd=str(VENDOR),
        )
    except subprocess.TimeoutExpired:
        return False, "MCP 同步超时（>600s）"
    except Exception as e:
        return False, f"MCP 同步异常：{e}"

    out = (r.stdout or "") + (r.stderr or "")
    if verbose:
        for line in out.splitlines():
            if line.strip():
                print(f"    {line.strip()}")
    if r.returncode != 0:
        return False, f"MCP 同步返回码 {r.returncode}"
    return True, "ok"


# ----------------------------------------------------------------------------- 读源


def read_source(src_db: Path) -> dict[str, list[dict]]:
    """一次性把源库需要的数据读进内存。"""
    con = sqlite3.connect(f"file:{src_db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    data: dict[str, list[dict]] = {}
    queries = {
        "daily_activity": "select * from daily_activity order by date",
        "sleep": "select * from sleep_sessions order by start_at",
        "heart_rate": "select * from heart_rate_samples order by timestamp",
        "spo2": "select * from spo2_samples order by timestamp",
        "stress": "select * from stress_samples order by timestamp",
        "workouts": "select * from workouts order by start_at",
        "body_measurements": "select * from body_measurements order by timestamp",
    }
    for k, q in queries.items():
        try:
            data[k] = [dict(r) for r in con.execute(q)]
        except sqlite3.Error:
            data[k] = []
    con.close()
    return data


def in_range(val: str | None, start: str, end: str) -> bool:
    """粗筛：日期字符串落在 [start, end] 内。"""
    if not val:
        return False
    d = str(val)[:10]
    return start <= d <= end


# ----------------------------------------------------------------------------- 写主库


def _exists(con, table: str, where: str, params: tuple) -> bool:
    return con.execute(f"SELECT 1 FROM {table} WHERE {where} LIMIT 1", params).fetchone() is not None


def write_activity(con, rows: list[dict], start: str, end: str, stamp: str, warnings: list):
    n_add = n_upd = 0
    for r in rows:
        d = r.get("date")
        if not in_range(d, start, end):
            continue
        steps = int(r.get("steps") or 0)
        dist_m = float(r.get("distance_m") or 0)
        kcal = r.get("active_kcal")
        kcal = int(round(float(kcal))) if kcal not in (None, "") else None
        dist_km = round(dist_m / 1000.0, 2) if dist_m else None

        if steps > SANITY_STEPS_MAX or (dist_km or 0) > SANITY_DISTANCE_KM_MAX:
            warnings.append(
                f"{d} 活动量异常偏大（{steps} 步 / {dist_km} km）——"
                f"疑似同一天多设备各自上报被累加，请人工确认"
            )

        existed = _exists(con, "activity_daily", "date = ?", (d,))
        con.execute(
            """INSERT INTO activity_daily
                 (date, steps, distance_km, active_kcal, active_minutes, stand_hours, source, fetched_at)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(date) DO UPDATE SET
                 steps          = excluded.steps,
                 distance_km    = COALESCE(excluded.distance_km, activity_daily.distance_km),
                 active_kcal    = COALESCE(excluded.active_kcal, activity_daily.active_kcal),
                 source         = excluded.source,
                 fetched_at     = excluded.fetched_at""",
            (d, steps, dist_km, kcal, None, None, SOURCE_TAG, stamp),
        )
        n_upd += 1 if existed else 0
        n_add += 0 if existed else 1
    return n_add, n_upd


def _stage_minutes(stages_raw) -> dict[str, int]:
    """把 MCP 的 stages JSON 汇总成 deep/light/rem/awake 分钟数。"""
    out = {"deep": 0, "light": 0, "rem": 0, "awake": 0}
    if not stages_raw:
        return out
    try:
        stages = json.loads(stages_raw) if isinstance(stages_raw, str) else stages_raw
    except Exception:
        return out
    for seg in stages or []:
        try:
            name = str(seg.get("stage", "")).lower()
            mins = int(seg.get("minutes") or 0)
        except Exception:
            continue
        if name in out:
            out[name] += mins
    return out


def write_sleep(con, rows: list[dict], start: str, end: str, stamp: str):
    """睡眠入库。2026-10-24 修「同日夜间 + 午睡互相覆盖」bug（10-20 / 10-23 两次实锤）：

    · 源库 is_nap 不可信（实测全 0），判不出午睡 → 改用启发式：
      同一归属日若有多条会话（夜间主睡眠 + 午睡），只入库
      duration_minutes 最长的一条作为主睡眠，其余丢弃。
    · 已有记录时**整行替换**，不再 COALESCE 混搭——旧的混搭会出现
      total=45 而 light=387 的行内自相矛盾。
    """
    best: dict[str, tuple] = {}   # 归属日 -> (duration, 源记录)
    for r in rows:
        start_ts = to_iso(r.get("start_at"))
        end_ts = to_iso(r.get("end_at"))
        if not start_ts:
            continue
        # 归属规则：按「起床日」归属（= 小米运动健康 App 睡眠卡片的日期口径，
        # 见 docs/01-数据规范.md 第三节）。缺 end_ts 时退化为入睡日。
        d = (end_ts or start_ts)[:10]
        if not in_range(d, start, end):
            continue
        dur = int(r["duration_minutes"]) if r.get("duration_minutes") is not None else 0
        cur = best.get(d)
        if cur is None or dur > cur[0]:
            best[d] = (dur, r)
    n_add = n_upd = 0
    for d, (dur, r) in best.items():
        start_ts = to_iso(r.get("start_at"))
        end_ts = to_iso(r.get("end_at"))
        st = _stage_minutes(r.get("stages"))
        existed = _exists(con, "sleep_session", "date = ?", (d,))
        con.execute(
            """INSERT INTO sleep_session
                 (date, start_ts, end_ts, total_min, deep_min, light_min, rem_min,
                  awake_min, wake_count, score, source, fetched_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(date) DO UPDATE SET
                 start_ts   = excluded.start_ts,
                 end_ts     = excluded.end_ts,
                 total_min  = excluded.total_min,
                 deep_min   = excluded.deep_min,
                 light_min  = excluded.light_min,
                 rem_min    = excluded.rem_min,
                 awake_min  = excluded.awake_min,
                 score      = excluded.score,
                 source     = excluded.source,
                 fetched_at = excluded.fetched_at""",
            (
                d,
                start_ts,
                end_ts,
                dur,
                st["deep"] or None,
                st["light"] or None,
                st["rem"] or None,
                int(r["time_awake_minutes"]) if r.get("time_awake_minutes") is not None else None,
                None,
                int(r["sleep_score"]) if r.get("sleep_score") is not None else None,
                SOURCE_TAG,
                stamp,
            ),
        )
        n_upd += 1 if existed else 0
        n_add += 0 if existed else 1
    return n_add, n_upd


def write_heart_rate(con, rows: list[dict], start: str, end: str, stamp: str):
    n_add = n_upd = 0
    for r in rows:
        ts = to_iso(r.get("timestamp"))
        if not ts or not in_range(ts, start, end):
            continue
        bpm = r.get("bpm")
        if bpm in (None, ""):
            continue
        cur = con.execute(
            """INSERT OR IGNORE INTO heart_rate (ts, bpm, hr_type, source)
               VALUES (?,?,?,?)""",
            (ts, int(bpm), r.get("sample_type") or "passive", SOURCE_TAG),
        )
        if cur.rowcount:
            n_add += 1
        else:
            n_upd += 1
    return n_add, n_upd


def write_spo2(con, rows: list[dict], start: str, end: str, stamp: str):
    n_add = n_upd = 0
    for r in rows:
        ts = to_iso(r.get("timestamp"))
        if not ts or not in_range(ts, start, end):
            continue
        v = r.get("spo2_pct")
        if v in (None, ""):
            continue
        cur = con.execute(
            """INSERT OR IGNORE INTO spo2 (ts, value, ctx, source) VALUES (?,?,?,?)""",
            (ts, int(v), "spot", SOURCE_TAG),
        )
        if cur.rowcount:
            n_add += 1
        else:
            n_upd += 1
    return n_add, n_upd


def write_stress(con, rows: list[dict], start: str, end: str, stamp: str):
    n_add = n_upd = 0
    for r in rows:
        ts = to_iso(r.get("timestamp"))
        if not ts or not in_range(ts, start, end):
            continue
        v = r.get("stress_score")
        if v in (None, ""):
            continue
        cur = con.execute(
            """INSERT OR IGNORE INTO stress (ts, value, source) VALUES (?,?,?)""",
            (ts, int(v), SOURCE_TAG),
        )
        if cur.rowcount:
            n_add += 1
        else:
            n_upd += 1
    return n_add, n_upd


def write_workouts(con, rows: list[dict], start: str, end: str, stamp: str):
    n_add = n_upd = 0
    for r in rows:
        start_ts = to_iso(r.get("start_at"))
        if not start_ts or not in_range(start_ts, start, end):
            continue
        ext = r.get("workout_id") or r.get("source_record_id")
        if not ext:
            continue
        dist_m = r.get("distance_m")
        dist_km = round(float(dist_m) / 1000.0, 3) if dist_m else None
        kcal = r.get("calories_kcal")
        kcal = int(round(float(kcal))) if kcal not in (None, "") else None
        existed = _exists(con, "workout", "ext_id = ?", (str(ext),))
        con.execute(
            """INSERT INTO workout
                 (ext_id, start_ts, end_ts, sport, duration_min, distance_km, kcal, avg_hr, max_hr, source)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(ext_id) DO UPDATE SET
                 start_ts     = excluded.start_ts,
                 end_ts       = excluded.end_ts,
                 sport        = excluded.sport,
                 duration_min = COALESCE(excluded.duration_min, workout.duration_min),
                 distance_km  = COALESCE(excluded.distance_km, workout.distance_km),
                 kcal         = COALESCE(excluded.kcal, workout.kcal),
                 avg_hr       = COALESCE(excluded.avg_hr, workout.avg_hr),
                 max_hr       = COALESCE(excluded.max_hr, workout.max_hr),
                 source       = excluded.source""",
            (
                str(ext),
                start_ts,
                to_iso(r.get("end_at")),
                r.get("activity_type") or "workout",
                float(r["duration_minutes"]) if r.get("duration_minutes") is not None else None,
                dist_km,
                kcal,
                int(r["avg_heart_rate_bpm"]) if r.get("avg_heart_rate_bpm") is not None else None,
                int(r["max_heart_rate_bpm"]) if r.get("max_heart_rate_bpm") is not None else None,
                SOURCE_TAG,
            ),
        )
        n_upd += 1 if existed else 0
        n_add += 0 if existed else 1
    return n_add, n_upd


# ----------------------------------------------------------------------------- 体重（体脂秤）
#
# 口径见 D-21（方案 A）与 D-29（晨起时间窗）。核心两条：
#   1. 只收「晨起窗内、当日首条」
#   2. 先查后写，绝不覆盖——主库该日期已有记录（照片 / 口述 / 秤）一律跳过
# 这样即使某天先有一次下午称重、次日又补录，也绝不会把基准冲掉。


def _num(v) -> float | None:
    """数值归一：None / 空 / 0 一律视为「没测出来」→ None（体成分 0 = 没测到）。"""
    if v in (None, ""):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f != 0 else None


def _local_dt(ts) -> datetime | None:
    """把源库时间戳统一成北京时间。源库混有 `+00:00`（App 手录）与 `+08:00`（秤）两种表示。"""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=SCALE_TZ)
    return dt.astimezone(SCALE_TZ)


def write_body_metric(con, rows: list[dict], start: str, end: str, stamp: str,
                      warnings: list, height_cm: float | None = None) -> dict:
    """体脂秤 → body_metric，返回写入/跳过统计。"""
    # 先按归属日收集晨起窗内的全部称重
    window: dict[str, list[tuple[datetime, dict]]] = {}
    n_out_window = 0
    for r in rows:
        dt = _local_dt(r.get("timestamp"))
        if dt is None:
            continue
        d = dt.date().isoformat()
        if not in_range(d, start, end):
            continue
        hhmm = dt.strftime("%H:%M")
        if not (SCALE_WINDOW[0] <= hhmm <= SCALE_WINDOW[1]):
            n_out_window += 1
            continue
        window.setdefault(d, []).append((dt, r))

    n_add = n_exists = n_dup = 0
    for d in sorted(window):
        entries = sorted(window[d], key=lambda x: x[0])
        n_dup += len(entries) - 1                      # 同日窗口内多测：只取首条
        dt, r = entries[0]
        if _exists(con, "body_metric", "date = ?", (d,)):
            n_exists += 1                              # 绝不覆盖已有记录
            continue
        w = _num(r.get("weight_kg"))
        if w is None:
            continue
        bmi = _num(r.get("bmi"))
        if bmi is None and height_cm:
            bmi = round(w / (height_cm / 100.0) ** 2, 1)
        else:
            bmi = round(bmi, 1) if bmi else None
        fat = _num(r.get("body_fat_pct"))
        muscle = _num(r.get("muscle_mass_kg"))

        # 首条缺体成分、而同日后续有 → 提示可人工补（不改写入口径）
        if fat is None and any(_num(x[1].get("body_fat_pct")) for x in entries[1:]):
            warnings.append(
                f"{d} 晨起窗内首条（{dt.strftime('%H:%M')}）只有体重、无体成分，"
                f"但同日稍后有完整体测——按方案 A 只取首条，体成分未录，可人工补"
            )

        note = (f"体脂秤自动同步（MCP）；{dt.strftime('%H:%M')} 称重，"
                f"落晨起窗 {SCALE_WINDOW[0]}–{SCALE_WINDOW[1]} 内首条（D-21）")
        con.execute(
            """INSERT INTO body_metric
                 (date, weight_kg, bmi, body_fat_pct, muscle_mass_kg, source, photo_path, note, fetched_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (d, w, bmi, fat, muscle, SCALE_SOURCE, None, note, stamp),
        )
        n_add += 1
    return {
        "added": n_add,
        "skipped_existing": n_exists,
        "skipped_out_window": n_out_window,
        "skipped_same_day_extra": n_dup,
    }


# ----------------------------------------------------------------------------- 快照


def dump_raw(data: dict, start: str, end: str) -> Path:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    out = RAW_DIR / f"{start}_{end}.json"
    payload = {
        "pulled_at": now_iso(),
        "range": {"start": start, "end": end},
        "counts": {k: len(v) for k, v in data.items()},
        "data": data,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


# ----------------------------------------------------------------------------- 主流程


STAT_ORDER = [
    ("daily_activity", "活动（日）"),
    ("sleep", "睡眠"),
    ("heart_rate", "心率样本"),
    ("spo2", "血氧"),
    ("stress", "压力"),
    ("workouts", "运动记录"),
]


def main() -> int:
    ap = argparse.ArgumentParser(
        description="手环数据搬运：MCP 源库 mi_fitness.db → 主库 health.db",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--days", type=int, default=7, help="回溯天数（默认 7）")
    ap.add_argument("--start", help="开始日期 YYYY-MM-DD（覆盖 --days）")
    ap.add_argument("--end", help="结束日期 YYYY-MM-DD（默认今天）")
    ap.add_argument("--no-pull", action="store_true", help="不联网，只搬运源库现有数据")
    ap.add_argument("--dry-run", action="store_true", help="只报会写什么，不落库")
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    ap.add_argument("--src-db", help="手工指定 MCP 源库路径")
    a = ap.parse_args()

    today = date.today()
    end = a.end or today.isoformat()
    if a.start:
        start = a.start
    else:
        start = (today - timedelta(days=max(0, a.days - 1))).isoformat()

    cfg = load_cfg()
    src_db = Path(a.src_db) if a.src_db else resolve_src_db(cfg)

    result: dict = {
        "range": {"start": start, "end": end},
        "src_db": str(src_db),
        "src_db_ok": src_db.exists(),
        "pulled": None,
        "written": {},
        "warnings": [],
        "raw_snapshot": None,
        "dry_run": a.dry_run,
    }

    if not a.json:
        print(f"手环数据搬运  {start} ~ {end}")
        print(f"  源库：{src_db}  {'✅' if src_db.exists() else '❌ 不存在'}")

    if not src_db.exists():
        msg = f"源库不存在：{src_db}"
        result["warnings"].append(msg)
        if a.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"  ❌ {msg}")
            print("     若手环尚未跑过 MCP 同步，先执行 vendor 里的 mi-fitness-mcp sync。")
        return 1

    # ① 联网拉取
    if a.no_pull:
        result["pulled"] = "skipped"
        if not a.json:
            print("  · 跳过联网拉取（--no-pull），只用源库现有数据")
    else:
        ok, note = pull_from_cloud(start, end, verbose=not a.json)
        result["pulled"] = note if ok else f"failed: {note}"
        if not ok:
            result["warnings"].append(f"云端拉取失败（{note}）——继续用源库现有数据")
            if not a.json:
                print(f"  ⚠️  云端拉取失败：{note}（继续用源库现有数据）")

    # ② 读源库
    data = read_source(src_db)
    result["src_counts"] = {k: len(v) for k, v in data.items()}

    if a.dry_run:
        result["written"] = {
            k: {"added": 0, "updated": 0, "src_rows": len(data.get(k, []))} for k, _ in STAT_ORDER
        }
        if a.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print("\n  [dry-run] 源库现有数据：")
            for k, label in STAT_ORDER:
                print(f"    {label:<10} {len(data.get(k, [])):>5} 行")
            print(f"    {'体重(秤)':<10} {len(data.get('body_measurements', [])):>5} 行"
                  f"   （窗 {SCALE_WINDOW[0]}–{SCALE_WINDOW[1]} 内首条才入主表，见 D-21/D-29）")
            print("\n  [dry-run] 未写库。")
        return 0

    # ③ 写主库
    stamp = now_iso()
    height_cm = _num((cfg.get("profile") or {}).get("height_cm"))
    con = sqlite3.connect(DB)
    try:
        n_add, n_upd = write_activity(con, data["daily_activity"], start, end, stamp, result["warnings"])
        result["written"]["daily_activity"] = {"added": n_add, "updated": n_upd}

        n_add, n_upd = write_sleep(con, data["sleep"], start, end, stamp)
        result["written"]["sleep"] = {"added": n_add, "updated": n_upd}

        n_add, n_upd = write_heart_rate(con, data["heart_rate"], start, end, stamp)
        result["written"]["heart_rate"] = {"added": n_add, "updated": n_upd}

        n_add, n_upd = write_spo2(con, data["spo2"], start, end, stamp)
        result["written"]["spo2"] = {"added": n_add, "updated": n_upd}

        n_add, n_upd = write_stress(con, data["stress"], start, end, stamp)
        result["written"]["stress"] = {"added": n_add, "updated": n_upd}

        n_add, n_upd = write_workouts(con, data["workouts"], start, end, stamp)
        result["written"]["workouts"] = {"added": n_add, "updated": n_upd}

        result["written"]["body_metric"] = write_body_metric(
            con, data["body_measurements"], start, end, stamp, result["warnings"], height_cm=height_cm
        )

        con.execute(
            "INSERT INTO meta (key, value) VALUES ('band_last_sync_at', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (stamp,),
        )
        con.commit()
    finally:
        con.close()

    # ④ 原始快照
    snap = dump_raw(data, start, end)
    result["raw_snapshot"] = str(snap)

    # ⑤ 输出
    if a.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    print("\n  写入主库：")
    for k, label in STAT_ORDER:
        w = result["written"].get(k, {})
        src_rows = len(data.get(k, []))
        print(
            f"    {label:<10} 源库 {src_rows:>5} 行 → 新增 {w.get('added',0):>4} / 更新 {w.get('updated',0):>4}"
        )

    src_body = len(data.get("body_measurements", []))
    if src_body:
        b = result["written"].get("body_metric", {})
        print(f"    {'体重(秤)':<10} 源库 {src_body:>5} 行 → 新增 {b.get('added', 0):>4}"
              f" / 窗({SCALE_WINDOW[0]}–{SCALE_WINDOW[1]})外跳过 {b.get('skipped_out_window', 0)}"
              f" / 已有跳过 {b.get('skipped_existing', 0)}")

    print(f"\n  原始快照：{snap.relative_to(ROOT)}")

    if result["warnings"]:
        print("\n  ⚠️  注意：")
        for w in result["warnings"]:
            print(f"    · {w}")

    print("\n  ℹ️  设备来源不可判：MCP 解析时丢弃了 sid，源库分不出「手机记的」还是「手环记的」，")
    print("     source 一律标 mi_fitness。手环 2026-10-10 晚才配对，此前活动数据实际来自手机。")
    print(f"     体重走体脂秤通道（source=scale_mcp），只取晨起窗 {SCALE_WINDOW[0]}–{SCALE_WINDOW[1]} "
          f"内首条，且绝不覆盖已有记录（D-21 / D-29）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
