#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
record_sleep.py —— 睡眠录入（手环数据口述 / 补录）

为什么需要它（见 docs/04-决策记录.md D-22）：
  睡眠目前**没有可用的自动通道**。搬运脚本 sync_band.py 本身支持 sleep，
  但上游 MCP（mi-fitness-mcp-cn）用 key=sleep 向小米云端请求时返回 0 条；
  英文上游 kubulashvili/mi-fitness-mcp 更直接声明 sleep「not yet supported」
  （端点与 payload 契约未确认）→ 云端拿不到，只能先手工录入。

归属日期口径（与 docs/01-数据规范.md 第三节一致）：
  **date = 起床日**。即「10-11 夜 → 10-12 晨」的睡眠记为 date=2026-10-12。
  与小米运动健康 App 睡眠卡片的日期口径一致，便于逐条核对。

用法：
    # 最简：只报总时长（分钟）
    python scripts/record_sleep.py --total-min 435

    # 完整：入睡 / 起床时间（HH:MM，跨夜自动推算） + 分期 + 评分
    python scripts/record_sleep.py --start 23:30 --end 07:10 --duration 7:05 \
        --deep 95 --light 250 --rem 80 --awake 25 --score 82

    # 补录到指定归属日
    python scripts/record_sleep.py --date 2026-10-12 --total-min 435

    # 查看最近记录
    python scripts/record_sleep.py --show

说明：
  · 所有时长单位为**分钟**。`--duration` 接受 H:MM / HH:MM。
  · start/end 只接受 HH:MM（跨夜自动处理）或完整时间戳（YYYY-MM-DD HH:MM）。
  · **不臆造**：没给的字段一律写 NULL，不填 0、不估算。
  · 只报 start/end 不报时长时，total_min 留空 —— 卧床时长 ≠ 睡眠时长，不替用户下结论。
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date as Date, datetime, timedelta
from pathlib import Path

import yaml

try:  # Windows 控制台默认 GBK，中文输出需要显式切 UTF-8
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from _intake_log import log_intake

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
SLEEP_TARGET = CFG["goals"].get("sleep_target_min")
SLEEP_MIN_MINUTES = CFG["thresholds"].get("sleep_min_minutes", 360)

# 合理性区间：一次睡眠 30 分钟（含小睡）到 17 小时，超出多半是录错
TOTAL_MIN_RANGE = (30, 1020)
SOURCE_CHOICES = ("manual", "band", "photo", "demo")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="睡眠录入：写入主库 sleep_session（归属日 = 起床日）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--date", help="归属日期 YYYY-MM-DD（= 起床日），默认今天")
    p.add_argument("--start", help="入睡时间 HH:MM（跨夜自动算前一天）或 YYYY-MM-DD HH:MM")
    p.add_argument("--end", help="起床时间 HH:MM 或 YYYY-MM-DD HH:MM")
    p.add_argument("--duration", help="睡眠时长 H:MM / HH:MM（= 总睡眠，不含清醒）")
    p.add_argument("--total-min", dest="total_min", type=int, help="睡眠时长（分钟）")
    p.add_argument("--deep", type=int, help="深睡（分钟）")
    p.add_argument("--light", type=int, help="浅睡（分钟）")
    p.add_argument("--rem", type=int, help="REM（分钟）")
    p.add_argument("--awake", type=int, help="夜间清醒（分钟）")
    p.add_argument("--wake-count", dest="wake_count", type=int, help="夜醒次数")
    p.add_argument("--score", type=int, help="睡眠评分 0-100（仅作参考）")
    p.add_argument("--source", default="manual", choices=SOURCE_CHOICES, help="数据来源，默认 manual")
    p.add_argument("--blob-id", dest="blob_id", help="源图 sha256（若从截图录入）")
    p.add_argument("--note", help="备注（如「手环口述」「测量条件异常」）")
    p.add_argument("--dry-run", action="store_true", help="只显示将写入什么，不落库")
    p.add_argument("--show", action="store_true", help="只显示历史记录")
    p.add_argument("--force", action="store_true", help="跳过范围校验强制写入")
    return p.parse_args()


def parse_hhmm(s: str) -> tuple[int, int]:
    parts = s.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"时间格式应为 HH:MM，收到：{s}")
    h, m = int(parts[0]), int(parts[1])
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"时间超出范围：{s}")
    return h, m


def parse_duration(s: str) -> int:
    """H:MM / HH:MM → 分钟。"""
    h, m = parse_hhmm(s)
    return h * 60 + m


def resolve_ts(
    raw: str | None, belong_date: str, is_start: bool, wake_raw: str | None
) -> str | None:
    """
    把时间入力解析成 ISO 字符串。
      · 带日期的完整形式 → 原样规范化
      · 只有 HH:MM → 先挂到归属日；入睡时刻若晚于起床时刻，则视为前一天的晚上（跨夜）
    """
    if not raw:
        return None
    s = raw.strip().replace("/", "-")
    if len(s) > 5:  # 视为完整日期时间
        try:
            dt = datetime.fromisoformat(s)
        except ValueError as exc:
            raise ValueError(f"时间戳无法解析：{raw}") from exc
        return dt.isoformat(timespec="minutes")
    h, m = parse_hhmm(s)
    d = Date.fromisoformat(belong_date)
    if is_start and wake_raw and len(wake_raw.strip()) <= 5:
        eh, em = parse_hhmm(wake_raw)
        if (h, m) >= (eh, em):  # 入睡时刻不早于起床时刻 → 跨夜，归前一天
            d = d - timedelta(days=1)
    return datetime(d.year, d.month, d.day, h, m).isoformat(timespec="minutes")


def fmt_minutes(v: int | None) -> str:
    if v is None:
        return "—"
    return f"{v // 60} h {v % 60:02d} min（{v} min）"


def show_recent(con: sqlite3.Connection, n: int = 10) -> None:
    print("=== 最近睡眠记录（date = 起床日）===")
    rows = list(con.execute(
        "SELECT date, start_ts, end_ts, total_min, deep_min, light_min, rem_min, "
        "awake_min, wake_count, score, source FROM sleep_session "
        "ORDER BY date DESC LIMIT ?", (n,)
    ))
    if not rows:
        print("  （暂无记录）")
        print("  提示：睡眠目前没有自动通道，手动录入见 --help。")
        return
    print(f"  {'起床日':<12}{'入睡':<18}{'起床':<18}{'总睡眠':>8}{'深睡':>7}{'浅睡':>7}{'REM':>7}{'清醒':>7}{'评分':>6}  来源")
    for r in rows:
        g = lambda v, w=7: ("—" if v is None else str(v)).rjust(w)
        print(f"  {r['date']:<12}{(r['start_ts'] or '—'):<18}{(r['end_ts'] or '—'):<18}"
              f"{g(r['total_min'], 8)}{g(r['deep_min'])}{g(r['light_min'])}{g(r['rem_min'])}"
              f"{g(r['awake_min'])}{g(r['score'], 6)}  {r['source'] or ''}")


def main() -> int:
    a = parse_args()
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row

    if a.show:
        show_recent(con)
        return 0

    d = a.date or Date.today().isoformat()
    try:
        Date.fromisoformat(d)
    except ValueError:
        print(f"❌ 日期格式不对：{d}（应为 YYYY-MM-DD）")
        return 1

    if not any(v is not None for v in (a.start, a.end, a.duration, a.total_min)):
        print("❌ 至少要提供 --start/--end/--duration/--total-min 之一。")
        print("   例：--start 23:30 --end 07:10 --duration 7:05")
        return 1

    total = a.total_min
    if total is None and a.duration:
        total = parse_duration(a.duration)
    if total is not None and not (TOTAL_MIN_RANGE[0] <= total <= TOTAL_MIN_RANGE[1]) and not a.force:
        print(f"❌ 睡眠时长 {total} 分钟超出合理范围 "
              f"[{TOTAL_MIN_RANGE[0]}, {TOTAL_MIN_RANGE[1]}]，拒绝写入。确认无误加 --force。")
        return 1

    try:
        start_ts = resolve_ts(a.start, d, is_start=True, wake_raw=a.end)
        end_ts = resolve_ts(a.end, d, is_start=False, wake_raw=None)
    except ValueError as exc:
        print(f"❌ {exc}")
        return 1

    # 分期合计与总时长的一致性提示（不拦截，只提醒）
    parts = [a.deep, a.light, a.rem]
    if all(v is not None for v in parts) and total is not None:
        s = sum(parts)
        if abs(s - total) > 20:
            print(f"⚠️  分期合计 {s} min 与总睡眠 {total} min 相差 {abs(s - total)} min，"
                  f"请确认口径（总睡眠 = 深睡+浅睡+REM）。按输入原样写入。")

    if a.dry_run:
        print(f"[dry-run] date={d}  start={start_ts}  end={end_ts}  total={total}")
        print("         未写库。")
        return 0

    stamp = datetime.now().isoformat(timespec="seconds")
    con.execute(
        """INSERT INTO sleep_session
             (date, start_ts, end_ts, total_min, deep_min, light_min, rem_min,
              awake_min, wake_count, score, source, fetched_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(date) DO UPDATE SET
             start_ts   = COALESCE(excluded.start_ts, sleep_session.start_ts),
             end_ts     = COALESCE(excluded.end_ts, sleep_session.end_ts),
             total_min  = COALESCE(excluded.total_min, sleep_session.total_min),
             deep_min   = COALESCE(excluded.deep_min, sleep_session.deep_min),
             light_min  = COALESCE(excluded.light_min, sleep_session.light_min),
             rem_min    = COALESCE(excluded.rem_min, sleep_session.rem_min),
             awake_min  = COALESCE(excluded.awake_min, sleep_session.awake_min),
             wake_count = COALESCE(excluded.wake_count, sleep_session.wake_count),
             score      = COALESCE(excluded.score, sleep_session.score),
             source     = excluded.source,
             fetched_at = excluded.fetched_at""",
        (d, start_ts, end_ts, total, a.deep, a.light, a.rem,
         a.awake, a.wake_count, a.score, a.source, stamp),
    )
    con.commit()

    bits = [f"睡眠 {fmt_minutes(total)}"] if total is not None else []
    if start_ts and end_ts:
        bits.append(f"{start_ts[11:16]}–{end_ts[11:16]}")
    log_intake("sleep", d, "；".join(bits), blob_id=a.blob_id)

    print()
    print(f"✅ 已写入 {d}（起床日）  →  sleep_session")
    print(f"   入睡 {start_ts or '—'}    起床 {end_ts or '—'}")
    print(f"   总睡眠 {fmt_minutes(total)}")
    if any(v is not None for v in (a.deep, a.light, a.rem, a.awake)):
        print(f"   分期：深睡 {a.deep if a.deep is not None else '—'} / "
              f"浅睡 {a.light if a.light is not None else '—'} / "
              f"REM {a.rem if a.rem is not None else '—'} / "
              f"清醒 {a.awake if a.awake is not None else '—'} min")
    if a.wake_count is not None:
        print(f"   夜醒 {a.wake_count} 次")
    if a.score is not None:
        print(f"   评分 {a.score}（绝对值不可信，只看趋势）")
    if a.note:
        print(f"   备注 {a.note}")

    print()
    print("📊 对照")
    if total is not None and SLEEP_TARGET:
        diff = total - SLEEP_TARGET
        state = f"比目标少 {abs(diff)} min" if diff < 0 else f"比目标多 {diff} min"
        print(f"   本次 {total} min / 目标 {SLEEP_TARGET} min   {state}")
    hist = [r["total_min"] for r in con.execute(
        "SELECT total_min FROM sleep_session WHERE total_min IS NOT NULL "
        "ORDER BY date DESC LIMIT 7"
    )]
    if len(hist) >= 2:
        avg = sum(hist) / len(hist)
        print(f"   近 {len(hist)} 次均值 {avg:.0f} min（{avg/60:.1f} h）")
    if total is not None and total < SLEEP_MIN_MINUTES:
        print(f"   ⚠️  短于 {SLEEP_MIN_MINUTES} min 的提示线")

    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
