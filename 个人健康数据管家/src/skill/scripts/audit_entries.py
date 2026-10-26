#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
audit_entries.py —— 「发了但没录」巡检

背景（见 docs/04-决策记录.md D-16）：
  照片不落盘，AI 直读对话附件取数。好处是链路短、隐私面小；
  代价是**没人能保证每一张都被处理过**——会话被岔开、窗口被关掉、网络抖动，
  都会让一张照片"到了但没录"，而库里看不出任何异常（只是少了一条）。

  本脚本就是把这条缝补上：不查文件系统（照片本来就不落盘），而是查
  **WorkBuddy 自己的附件台账**——`~/.workbuddy/projects/*/*.jsonl` 里每条
  带图消息都有 `image_blob_ref`（blob_id / 原始文件名 / 精确时间戳）。
  这是权威的「你发过什么」，绕不过去也丢不掉。

  再拿它去对账 `data/intake_log.jsonl`（每次录入时写的台账）+ `audit_ignored.jsonl`
  （你说了不用记的），差额就是「漏记候选」。

为什么能补录：WorkBuddy 把发过的图片按 sha256 存在 `~/.workbuddy/blobs/` 里，
文件还在 → 漏掉的那张可以直接重读识读补录，不必你重发。

用法：
    python scripts/audit_entries.py                     # 巡检昨天
    python scripts/audit_entries.py --date 2026-10-10    # 指定某天
    python scripts/audit_entries.py --days 3             # 巡检最近 3 天
    python scripts/audit_entries.py --json               # 额外输出机器可读 JSON
    python scripts/audit_entries.py --dismiss <blob_id>  # 标记某张为「不用记」
    python scripts/audit_entries.py --dismiss-date 2026-10-10   # 该日全部候选标记为不用记
    python scripts/audit_entries.py --list-ignored        # 看已忽略清单
    python scripts/audit_entries.py --all                # 连已录/已忽略都列出来（排查用）

退出码：0 = 无漏记；2 = 有漏记候选待确认。
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import sqlite3
import sys
from datetime import date as Date, datetime, timedelta
from pathlib import Path

import yaml

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
CFG = yaml.safe_load((ROOT / "config" / "config.yaml").read_text(encoding="utf-8"))
DB_PATH = ROOT / CFG["paths"]["db"]
INTAKE_LOG = ROOT / "data" / "intake_log.jsonl"
IGNORED_LOG = ROOT / "data" / "audit_ignored.jsonl"
LOG_DIR = ROOT / "logs"

# WorkBuddy 本机数据目录（可用环境变量覆盖）
WB_HOME = Path(os.environ.get("WORKBUDDY_HOME", Path.home() / ".workbuddy"))
PROJECTS = WB_HOME / "projects"
BLOBS = WB_HOME / "blobs"

# 时段 → 大概是什么（纯提示，用来给人一眼判断"缺的是哪一餐"，不作为事实）
SLOTS = [
    ("05:00", "09:30", "晨起秤面 / 早餐"),
    ("09:30", "14:30", "午餐"),
    ("14:30", "17:00", "午后加餐"),
    ("17:00", "21:30", "晚餐"),
]

# 「例行非健康数据」文件名模式（config 可覆盖）。
# 目的：日常工作里发的截图不该每天来烦你一遍——它们不算漏记候选，只报一行计数。
DEFAULT_IGNORE_PATTERNS = ("Clipboard_Screenshot*", "Screen*", "screenshot*", "截图*")
IGNORE_PATTERNS = tuple(CFG.get("audit", {}).get("ignore_name_patterns") or DEFAULT_IGNORE_PATTERNS)


def slot_of(hm: str) -> str:
    for lo, hi, label in SLOTS:
        if lo <= hm < hi:
            return label
    return "其他时段"


def looks_like_screenshot(name: str | None, mime: str | None) -> bool:
    n = (name or "").lower()
    if "screenshot" in n or "clipboard" in n or n.startswith("screen"):
        return True
    return False


def is_routine_ignored(name: str | None) -> bool:
    """文件名命中「例行非健康数据」模式 → 不算漏记候选。"""
    if not name:
        return False
    return any(fnmatch.fnmatch(name, pat) for pat in IGNORE_PATTERNS)


# ---------------------------------------------------------------- 扫描台账

def scan_sent_images(d0: Date, d1: Date, verbose: bool = False) -> list[dict]:
    """扫会话记录，取出 [d0, d1] 内用户发过的所有图片。"""
    lo = datetime(d0.year, d0.month, d0.day).timestamp()
    hi = datetime(d1.year, d1.month, d1.day).timestamp() + 86400
    # 文件级预筛：mtime 早于 d0 前一天的不可能含目标日期消息
    mtime_cut = lo - 86400
    out: list[dict] = []
    scanned = 0
    if not PROJECTS.exists():
        return out
    for proj in PROJECTS.iterdir():
        if not proj.is_dir():
            continue
        for f in proj.glob("*.jsonl"):
            try:
                if f.stat().st_mtime < mtime_cut:
                    continue
            except OSError:
                continue
            scanned += 1
            try:
                fh = f.open("r", encoding="utf-8", errors="replace")
            except OSError:
                continue
            with fh:
                for line in fh:
                    if "image_blob_ref" not in line:
                        continue
                    try:
                        o = json.loads(line)
                    except Exception:
                        continue
                    if o.get("role") != "user" or o.get("type") != "message":
                        continue
                    ts = o.get("timestamp")
                    if not ts or not (lo <= ts / 1000 < hi):
                        continue
                    content = o.get("content")
                    if not isinstance(content, list):
                        continue
                    for it in content:
                        if not isinstance(it, dict) or it.get("type") != "image_blob_ref":
                            continue
                        bp = it.get("blob_path")
                        out.append({
                            "blob_id": it.get("blob_id"),
                            "blob_path": bp,
                            "blob_exists": bool(bp and Path(bp).exists()),
                            "size": it.get("size"),
                            "filename": it.get("original_filename"),
                            "mime": it.get("mime"),
                            "ts": ts,
                            "dt": datetime.fromtimestamp(ts / 1000),
                            "session": f.parent.name,
                            "file": f.name,
                        })
    # 去重：同一天同一张图发多次只算一条（记录次数）
    merged: dict[tuple, dict] = {}
    for r in out:
        key = (r["dt"].strftime("%Y-%m-%d"), r["blob_id"])
        if key in merged:
            merged[key]["count"] += 1
            continue
        r["count"] = 1
        merged[key] = r
    rows = sorted(merged.values(), key=lambda r: r["ts"])
    if verbose:
        print(f"  （扫描 {scanned} 个会话文件，命中 {len(rows)} 张）")
    return rows


# ---------------------------------------------------------------- 台账读写

def read_log_keys(path: Path) -> set[str]:
    keys: set[str] = set()
    if not path.exists():
        return keys
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            if o.get("blob_id"):
                keys.add(o["blob_id"])
    return keys


def read_log_records(path: Path) -> list[dict]:
    recs: list[dict] = []
    if not path.exists():
        return recs
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                recs.append(json.loads(line))
            except Exception:
                continue
    return recs


def append_ignored(blob_id: str, note: str = "") -> None:
    IGNORED_LOG.parent.mkdir(parents=True, exist_ok=True)
    with IGNORED_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "blob_id": blob_id,
            "note": note,
            "at": datetime.now().isoformat(timespec="seconds"),
        }, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- 主流程

def db_context(con: sqlite3.Connection, d: str) -> dict:
    def one(sql, *a):
        try:
            return con.execute(sql, a).fetchone()
        except sqlite3.Error:
            return None
    body = one("SELECT COUNT(*) FROM body_metric WHERE date = ? AND source != 'demo'", d)
    meal = one("SELECT COUNT(*) FROM meal WHERE date = ? AND source != 'demo'", d)
    waist = one("SELECT COUNT(*) FROM waist WHERE date = ? AND source != 'demo'", d)
    return {
        "body_metric": body[0] if body else 0,
        "meal": meal[0] if meal else 0,
        "waist": waist[0] if waist else 0,
    }


def fmt_size(n) -> str:
    if not n:
        return "—"
    return f"{n / 1024:.0f}K" if n < 1024 * 1024 else f"{n / 1048576:.1f}M"


def main() -> int:
    p = argparse.ArgumentParser(description="「发了但没录」巡检",
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--date", help="YYYY-MM-DD，默认昨天")
    p.add_argument("--days", type=int, help="巡检最近 N 天（含今天）")
    p.add_argument("--json", action="store_true", help="额外输出机器可读 JSON 到 logs/")
    p.add_argument("--all", action="store_true", help="连已录 / 已忽略一并列出")
    p.add_argument("--include-screenshots", action="store_true",
                   help="把「例行截图/剪贴板图」也计入漏记候选（默认略过，只报计数）")
    p.add_argument("--dismiss", nargs="+", metavar="BLOB_ID", help="标记这些 blob 为「不用记」")
    p.add_argument("--dismiss-date", help="把该日全部漏记候选标记为「不用记」")
    p.add_argument("--list-ignored", action="store_true", help="列出已忽略清单")
    p.add_argument("-v", "--verbose", action="store_true")
    a = p.parse_args()

    if a.list_ignored:
        recs = read_log_records(IGNORED_LOG)
        print(f"=== 已忽略（不用记）{len(recs)} 条 ===")
        for r in recs:
            print(f"  {r.get('at', '')[:16]}  {r.get('blob_id', '')[:12]}…  {r.get('note', '')}")
        return 0

    # 日期窗口
    if a.days:
        d1 = Date.today()
        d0 = d1 - timedelta(days=a.days - 1)
    else:
        d = a.date or (Date.today() - timedelta(days=1)).isoformat()
        try:
            d0 = d1 = Date.fromisoformat(d)
        except ValueError:
            print(f"❌ 日期格式不对：{d}（应为 YYYY-MM-DD）")
            return 1

    if a.dismiss:
        for b in a.dismiss:
            append_ignored(b, "manual dismiss")
        print(f"✅ 已标记 {len(a.dismiss)} 个 blob 为「不用记」，之后不再提示。")
        return 0

    if not PROJECTS.exists():
        print(f"❌ 找不到会话记录目录：{PROJECTS}")
        print("   若 WorkBuddy 数据目录不在默认位置，设环境变量 WORKBUDDY_HOME 指过去。")
        return 1

    print("=" * 68)
    label = d0.isoformat() if d0 == d1 else f"{d0} ~ {d1}"
    print(f"漏记巡检 · {label}")
    print("=" * 68)
    print("口径：查 WorkBuddy 附件台账（会话记录里的带图消息），不查文件系统——照片本来不落盘。")
    print()

    sent = scan_sent_images(d0, d1, verbose=a.verbose)
    recorded = read_log_keys(INTAKE_LOG)
    ignored = read_log_keys(IGNORED_LOG)

    if a.dismiss_date:
        try:
            dd = Date.fromisoformat(a.dismiss_date)
        except ValueError:
            print(f"❌ --dismiss-date 日期格式不对：{a.dismiss_date}")
            return 1
        # 注意：要按 --dismiss-date 指定的那一天去扫，不能用报告窗口的日期
        sent_d = scan_sent_images(dd, dd)
        n = 0
        for r in sent_d:
            if r["blob_id"] in recorded or r["blob_id"] in ignored:
                continue
            append_ignored(r["blob_id"], f"dismiss-date {a.dismiss_date}")
            ignored.add(r["blob_id"])
            n += 1
        print(f"✅ 已把 {a.dismiss_date} 的 {n} 个候选标记为「不用记」（该日共发过 {len(sent_d)} 张图）。")
        print("   用 `--list-ignored` 可看全量忽略清单；想撤销，直接编辑 data/audit_ignored.jsonl 删行。")
        return 0

    pending = [r for r in sent
               if r["blob_id"] not in recorded and r["blob_id"] not in ignored
               and (a.include_screenshots or not is_routine_ignored(r["filename"]))]
    routine = [r for r in sent
               if r["blob_id"] not in recorded and r["blob_id"] not in ignored
               and is_routine_ignored(r["filename"]) and not a.include_screenshots]
    done = [r for r in sent if r["blob_id"] in recorded]
    skipped = [r for r in sent if r["blob_id"] in ignored]

    # ---- 台账 ----
    print(f"【一】你发过的图（附件台账）：{len(sent)} 张")
    show = sent if (a.all or a.include_screenshots) else [r for r in sent if r not in routine]
    if not show:
        print("   （该时段没有可疑图片；例行截图见下方计数）")
    else:
        print(f"   {'时间':<16}{'大小':>7}   {'原文件名':<30}{'时段判断':<16}{'入库'}")
        for r in show:
            state = ("✅ 已录" if r["blob_id"] in recorded
                     else ("· 已忽略" if r["blob_id"] in ignored else "❌ 未见录入"))
            fn = (r["filename"] or "—")[:28]
            print(f"   {r['dt'].strftime('%m-%d %H:%M:%S'):<16}{fmt_size(r['size']):>7}   "
                  f"{fn:<30}{slot_of(r['dt'].strftime('%H:%M')):<16}{state}")
            if a.all:
                print(f"        blob={r['blob_id'][:16] if r['blob_id'] else '—'}…  "
                      f"文件{'在' if r['blob_exists'] else '不在'}  {r['blob_path']}")
    if routine:
        print(f"   （另有 {len(routine)} 张「例行截图/剪贴板图」按规则略过，"
              f"加 --include-screenshots 可展开看）")

    # ---- 库内数据 ----
    con = sqlite3.connect(DB_PATH)
    print()
    print("【二】主库里这几天的记录")
    dd = d0
    while dd <= d1:
        c = db_context(con, dd.isoformat())
        print(f"   {dd.isoformat()}   体重/体脂 {c['body_metric']} 条 | 饮食 {c['meal']} 餐 | 腰围 {c['waist']} 条")
        dd += timedelta(days=1)
    con.close()

    # ---- 结论 ----
    print()
    print("【三】结论")
    if not pending:
        print(f"   ✅ 没有漏记。发送 {len(sent)} 张（其中 {len(routine)} 张例行截图略过），"
              f"已录 {len(done)}，已忽略 {len(skipped)}。")
    else:
        print(f"   ⚠️  发现 {len(pending)} 张「发了但没录」的候选：")
        for r in pending:
            print(f"      · {r['dt'].strftime('%m-%d %H:%M')}  {r['filename'] or '（无文件名）'}"
                  f"  → 大概是「{slot_of(r['dt'].strftime('%H:%M'))}」")
            print(f"        可直接重读补录：{r['blob_path']}")
            if not r["blob_exists"]:
                print("        ⚠️ 该 blob 文件已不在本机，需让用户重发")
        print()
        print("   处置方式（二选一）：")
        print("     · 是要记的 → 直接重读上面的图片识读后补录（补录时带上 --blob-id，台账才对得上）")
        print("     · 不用记   → 跑 `--dismiss <blob_id>` 或 `--dismiss-date <日期>`，之后不再提示")

    # ---- JSON 输出 ----
    if a.json:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        out = {
            "range": [d0.isoformat(), d1.isoformat()],
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "sent": [{
                "blob_id": r["blob_id"], "blob_path": r["blob_path"], "blob_exists": r["blob_exists"],
                "filename": r["filename"], "size": r["size"],
                "time": r["dt"].strftime("%Y-%m-%d %H:%M:%S"), "slot": slot_of(r["dt"].strftime("%H:%M")),
                "screenshot_hint": looks_like_screenshot(r["filename"], r["mime"]),
                "count": r["count"],
                "routine_ignored": bool(is_routine_ignored(r["filename"]) and not a.include_screenshots),
                "state": ("recorded" if r["blob_id"] in recorded
                          else ("ignored" if r["blob_id"] in ignored else "pending")),
            } for r in sent],
            "pending_count": len(pending),
        }
        fp = LOG_DIR / f"audit-{d1.isoformat()}.json"
        fp.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print()
        print(f"   机器可读结果：{fp}")

    return 2 if pending else 0


if __name__ == "__main__":
    sys.exit(main())
