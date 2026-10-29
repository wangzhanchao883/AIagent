#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
从 SQLite 渲染 HTML 健康日报。

用法：
    python scripts/build_report.py                    # 用库里最新一天
    python scripts/build_report.py --date 2026-10-12  # 指定日期
    python scripts/build_report.py --open             # 生成后自动打开

输出：reports/report-YYYY-MM-DD.html

────────────────────────────────────────────────────────────────────────
报告结构（2026-10-13 用户要求重排 · 见 docs/04-决策记录.md D-33）

  第一层「一眼看明白」
    ① 一句话总结（D-38 定版：一句总判断 + 判定行[饮食/睡眠/运动]
       各带「良好/需注意」+ 注意什么 + 今日建议块[结合长期目标与本周执行情况]）
       ↓ 分隔线「详细指标如下」
    ② 核心指标总览（体重/心率/步数/运动/摄入/支出/缺口）
    ③ 热量账本（支出拆解 + 缺口区间）
    ④ 饮食结构是否合理（碳水 / 蛋白 / 蔬菜）
    ⑤ 睡眠情况
    ⑥ 今日建议（运动 / 饮食 / 睡眠）
    ⑦ 昨日建议执行率（T+1 对比）
  ── 分割线：以下是明细 ──
  第二层「明细」
    ⑧ 餐食明细 ⑨ 运动明细 ⑩ 体重趋势 ⑪ 睡眠分期
    ⑫ 恢复与体征 ⑬ 个人份量库 ⑭ 数据来源与局限

  建议台账：data/advice_log.jsonl
    每次生成报告时，把「这份报告给出的今日建议」连同其**目标日（次日）**写入台账；
    下次生成次日报告时，按 target_date 取回，与本日实际数据逐条核对 → 得到执行率。
    条目自带机器可核验的 check（metric / value），不依赖人去回填。

────────────────────────────────────────────────────────────────────────
睡眠板块开关（2026-10-16 用户定 · D-41）

  22:00「当日速览」**不出睡眠**：当日报告查的是"今夜"的睡眠，结构上必然查不到
  （D-37 口径：报告日 D 展示 D 夜→D+1 晨那一觉，归属日 = D+1，当天不存在），
  那一栏只会永远显示「昨夜没有睡眠记录（可能未佩戴/未同步）」——一条必然错误的文案。
  睡眠归 10:10「昨日汇总」管，那里 D+1 已过去，数据是实的。

  控制方式：
    · 默认：--date 为今天（或未指定而取到最新一天=今天）→ 自动关闭睡眠
    · --no-sleep   对任意日期强制关闭
    · --sleep      强制打开（覆盖「当日自动关闭」）
  关闭时一并去掉：总览表睡眠行、一句话总结的睡眠判定行、今日建议的睡眠条、
  第二层「睡眠分期」整卡。判定行数与「昨天/当天」称谓均动态计算，不写死。
────────────────────────────────────────────────────────────────────────
"""
import argparse
import json
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

try:
    import yaml
except ImportError:
    yaml = None

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
DB_PATH = ROOT / "data" / "health.db"
CFG_PATH = ROOT / "config" / "config.yaml"
REPORT_DIR = ROOT / "reports"
ADVICE_LOG = ROOT / "data" / "advice_log.jsonl"

CSS = """
:root{
  --bg:#f6f6f3; --card:#ffffff; --line:#e4e4de; --ink:#1b1b1a; --muted:#6f6f6a;
  --ok:#157f4a; --warn:#b45309; --bad:#b91c1c; --accent:#2f6f4f;
  --deep:#2b3a67; --light:#7b96c9; --rem:#a07fc9; --awake:#d8d5cf;
  --zone:#eef4f0;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font-family:"PingFang SC","Microsoft YaHei",-apple-system,"Segoe UI",sans-serif;
  font-size:14px;line-height:1.65}
.wrap{max-width:880px;margin:0 auto;padding:28px 20px 56px}
h1{font-size:22px;font-weight:600;margin:0 0 4px}
h2{font-size:15px;font-weight:600;margin:0 0 12px;color:var(--ink)}
.sub{color:var(--muted);font-size:13px;margin-bottom:20px}
.banner{background:#fff8e6;border:1px solid #f0d69a;color:#6b4a09;
  border-radius:10px;padding:10px 14px;font-size:13px;margin-bottom:18px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;
  padding:18px;margin-bottom:16px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.kpi .lbl{font-size:12px;color:var(--muted);margin-bottom:6px}
.kpi .val{font-size:21px;font-weight:600;letter-spacing:-.3px}
.kpi .aux{font-size:12px;color:var(--muted);margin-top:4px}
.tag{display:inline-block;font-size:11px;padding:1px 7px;border-radius:999px;
  border:1px solid currentColor;margin-left:6px;vertical-align:2px}
.tag.ok{color:var(--ok)} .tag.warn{color:var(--warn)} .tag.bad{color:var(--bad)}
.tag.na{color:var(--muted)}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line)}
th{color:var(--muted);font-weight:500;font-size:12px}
td.num{text-align:right;font-variant-numeric:tabular-nums}
.legend{display:flex;gap:16px;flex-wrap:wrap;font-size:12px;color:var(--muted);margin-top:8px}
.legend i{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:5px;
  vertical-align:-1px}
.note{font-size:12px;color:var(--muted);margin-top:10px}
footer{color:var(--muted);font-size:12px;margin-top:26px;border-top:1px solid var(--line);
  padding-top:14px}
/* ── 一句话总结（2026-10-15 用户定版 · D-38）──
   结构：一句总判断 → 判定行（默认 饮食/睡眠/运动，各带良好/需注意 + 注意什么）
        → 今日建议块（结合长期目标 + 本周执行情况）→ 分隔线「详细指标如下」
   注：22:00 当日速览不出睡眠行（D-41），此时只剩两行，行数动态渲染。 */
.hl{background:var(--card);border:1px solid #d7e5dc;border-radius:12px;
  padding:0;margin-bottom:18px;overflow:hidden}
.hl-top{background:linear-gradient(180deg,#f2f7f4,#ffffff);
  padding:15px 18px 13px;border-bottom:1px solid var(--line)}
.hl-top .t{font-size:12px;color:var(--muted);letter-spacing:.4px}
.hl-top .lead{font-size:15px;font-weight:600;margin-top:5px;line-height:1.6}
.hl-rows{padding:6px 18px 4px}
.hrow{display:flex;align-items:flex-start;gap:10px;padding:11px 0;
  border-bottom:1px dashed var(--line)}
.hrow:last-child{border-bottom:0}
.hrow .k{font-size:14px;font-weight:600;width:78px;flex:none;
  display:flex;align-items:center;gap:6px;flex-wrap:wrap}
.hrow .v{font-size:13px;line-height:1.7;color:#3d3d3a}
.hrow .v b{font-weight:600;color:var(--ink)}
.hrow .v .dim{color:var(--muted)}
.hl-adv{background:#fbfbf8;border-top:1px solid var(--line);padding:14px 18px 16px}
.hl-adv .h{font-size:12px;color:var(--muted);margin-bottom:10px;letter-spacing:.4px}
.advrow{display:flex;gap:10px;padding:7px 0;align-items:flex-start}
.advrow .k{font-size:13px;font-weight:600;width:52px;flex:none;color:var(--accent)}
.advrow .v{font-size:13px;line-height:1.75;color:#3d3d3a}
.advrow .v b{font-weight:600;color:var(--ink)}
/* 胶囊标签（良好 / 需注意 / 数据不足） */
.pill{display:inline-block;font-size:11px;padding:1px 8px;border-radius:999px;
  font-weight:500;border:1px solid currentColor;line-height:1.6;white-space:nowrap}
.pill.ok{color:var(--ok);background:#eaf5ef}
.pill.warn{color:var(--warn);background:#fdf3e7}
.pill.na{color:var(--muted);background:#f2f2ee}
/* 「详细指标如下」下钻分隔 */
.sect.gate{color:var(--accent);font-weight:600}
.sect.gate .pill{color:var(--accent);background:var(--zone);font-weight:500}
/* ── 核心指标总览（2026-10-15 改为体检化验单样式 · D-39）──
   四列：项目 | 结论（方向词）| 数值 | 参考范围 / 说明
   规则：**结论列先给一个词**（过高/过低/偏大/偏小/达标/数据不足），
        达标项淡化处理，需注意项高亮 —— 一眼看出哪几项不正常。 */
.ov{border-collapse:separate;border-spacing:0}
.ov th{font-size:11px;color:var(--muted);font-weight:500;text-align:left;
  padding:0 10px 7px;border-bottom:1px solid var(--line)}
.ov th.c-verdict{width:118px}
.ov th.c-ref{width:38%}
.ov td{padding:9px 10px;border-bottom:1px solid var(--line);vertical-align:top}
.ov tr:last-child td{border-bottom:0}
.ov tr.row-warn td{background:#fdfaf6}
.ov tr.row-na td{background:#fafaf8}
/* 结论词：一词讲明白结果 */
.vd{display:inline-block;font-size:12px;font-weight:600;padding:2px 9px;
  border-radius:6px;letter-spacing:.3px;white-space:nowrap}
.vd.ok{color:var(--ok);background:#eaf5ef}
.vd.warn{color:var(--warn);background:#fdf0e0}
.vd.na{color:var(--muted);background:#f2f2ee}
/* 数值 */
.ov td.c-val{font-variant-numeric:tabular-nums;font-weight:600}
.ov td.c-val .u{font-weight:400;color:var(--muted);font-size:12px}
/* 参考范围 / 说明 */
.ov td.c-ref{color:var(--muted);font-size:12px;line-height:1.6}
.ov td.c-ref b{color:#4a4a46;font-weight:600}
.ov td.c-item{color:#3d3d3a;white-space:nowrap}
/* 组标题行（体检单的分类段） */
.ov tr.grp td{background:#f7f9f7;color:var(--muted);font-size:11px;
  padding:6px 10px;letter-spacing:.5px;border-bottom:1px solid var(--line)}
/* 异常高亮条 */
.ov td.flag::after{content:"▲";color:var(--warn);font-size:9px;margin-left:4px;vertical-align:2px}
/* ── 热量账本 ── */
.ledger{display:grid;grid-template-columns:1fr 1fr;gap:14px}
@media(max-width:620px){.ledger{grid-template-columns:1fr}}
.lbox{border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.lbox .h{font-size:12px;color:var(--muted);margin-bottom:8px}
.lbox .big{font-size:20px;font-weight:600}
.lbox .s{font-size:12px;color:var(--muted);margin-top:6px;line-height:1.7}
.gapbox{border:1px solid #d7e5dc;background:var(--zone);border-radius:10px;
  padding:14px;margin-top:12px}
.gapbox .big{font-size:22px;font-weight:600;color:var(--accent)}
/* ── 判定列表 ── */
.advice{list-style:none;padding:0;margin:0}
.advice li{padding:9px 0 9px 26px;border-bottom:1px dashed var(--line);position:relative}
.advice li:last-child{border-bottom:0}
.advice li::before{content:"";position:absolute;left:8px;top:17px;width:6px;height:6px;
  border-radius:50%;background:var(--accent)}
.advice .cat{display:inline-block;font-size:11px;padding:1px 7px;border-radius:999px;
  background:var(--zone);color:var(--accent);margin-right:6px;vertical-align:1px}
/* ── 执行率 ── */
.rate{font-size:13px;color:var(--muted);margin-bottom:10px}
.rate b{color:var(--ink);font-size:16px}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;
  vertical-align:1px}
.dot.done{background:var(--ok)} .dot.miss{background:var(--bad)} .dot.na{background:#c9c9c2}
/* ── 分区标题 ── */
.sect{margin:26px 0 12px;font-size:13px;color:var(--muted);display:flex;
  align-items:center;gap:10px}
.sect::after{content:"";flex:1;height:1px;background:var(--line)}
"""

# ---------------------------------------------------------------- 工具


def load_cfg() -> dict:
    if yaml is None or not CFG_PATH.exists():
        return {}
    with open(CFG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def connect() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise SystemExit("库不存在，请先运行：python scripts/init_db.py")
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def one(con, sql, args=()):
    r = con.execute(sql, args).fetchone()
    return dict(r) if r else None


def many(con, sql, args=()):
    return [dict(r) for r in con.execute(sql, args).fetchall()]


def fmt(v, suffix="", nd=0):
    if v is None:
        return "—"
    if nd:
        return f"{v:.{nd}f}{suffix}"
    return f"{v}{suffix}"


def jin(kg):
    """kg → 斤（用户习惯单位）。"""
    return None if kg is None else kg * 2


def wt(kg, cfg, nd=1, html=False):
    """按 config 的 profile.weight_unit 显示体重。

    - unit == "kg" → "75.8 kg"
    - unit == "jin" → "151.6 斤（75.8 kg）"（保留折合值，兼容旧习惯）
    html=True 时给单位套 <span class='u'>，用于总览表格。
    """
    if kg is None:
        return "—"
    unit = (cfg or {}).get("profile", {}).get("weight_unit", "jin")
    if unit == "kg":
        return f"{kg:.{nd}f} <span class='u'>kg</span>" if html else f"{kg:.{nd}f} kg"
    if html:
        return f"{kg * 2:.{nd}f} <span class='u'>斤</span>（{kg:.{nd}f} kg）"
    return f"{kg * 2:.{nd}f} 斤（{kg:.{nd}f} kg）"


def tag(cls, label=None):
    if not cls:
        return ""
    default = {"ok": "达标", "warn": "注意", "bad": "超标", "na": "无数据"}
    return f'<span class="tag {cls}">{label or default.get(cls, cls)}</span>'


# ---------------------------------------------------------------- 图表


def weight_chart(rows, goal):
    """体重趋势 + 目标线。返回 SVG。"""
    W, H = 840, 190
    L, R, T, B = 52, 18, 24, 34
    pts = [(r["date"], r["weight_kg"]) for r in rows if r["weight_kg"] is not None]
    if len(pts) < 2:
        return '<p class="note">体重数据不足两天，暂不绘制趋势。</p>'

    vals = [v for _, v in pts] + ([goal] if goal else [])
    lo, hi = min(vals) - 0.6, max(vals) + 0.6
    n = len(pts)
    iw, ih = W - L - R, H - T - B

    def X(i):
        return L + iw * i / (n - 1)

    def Y(v):
        return T + ih * (hi - v) / (hi - lo)

    line = " ".join(("M" if i == 0 else "L") + f"{X(i):.1f} {Y(v):.1f}" for i, (_, v) in enumerate(pts))
    area = f"M{X(0):.1f} {T+ih:.1f} " + " ".join(f"L{X(i):.1f} {Y(v):.1f}" for i, (_, v) in enumerate(pts)) + f" L{X(n-1):.1f} {T+ih:.1f} Z"

    grid = []
    for k in range(4):
        v = lo + (hi - lo) * k / 3
        y = Y(v)
        grid.append(f'<line x1="{L}" y1="{y:.1f}" x2="{W-R}" y2="{y:.1f}" stroke="#ececE6" stroke-width="1"/>')
        grid.append(f'<text x="{L-8}" y="{y+4:.1f}" text-anchor="end" font-size="11" fill="#9a9a94">{v:.1f}</text>')

    goal_svg = ""
    if goal:
        gy = Y(goal)
        goal_svg = (f'<line x1="{L}" y1="{gy:.1f}" x2="{W-R}" y2="{gy:.1f}" stroke="#157f4a"'
                    f' stroke-width="1" stroke-dasharray="5 4" opacity="0.75"/>'
                    f'<text x="{W-R}" y="{gy-6:.1f}" text-anchor="end" font-size="11" fill="#157f4a">目标 {goal:g} kg</text>')

    marks = []
    for i in (0, n - 1):
        marks.append(f'<circle cx="{X(i):.1f}" cy="{Y(pts[i][1]):.1f}" r="3.2" fill="#2f6f4f"/>')

    labels = []
    for i in range(0, n, max(1, n // 6)):
        labels.append(f'<text x="{X(i):.1f}" y="{H-12}" text-anchor="middle" font-size="11" fill="#9a9a94">{pts[i][0][5:]}</text>')
    labels.append(f'<text x="{X(n-1):.1f}" y="{H-12}" text-anchor="middle" font-size="11" fill="#9a9a94">{pts[-1][0][5:]}</text>')

    return f"""<svg viewBox="0 0 {W} {H}" width="100%" role="img">
  <title>体重趋势</title>
  {''.join(grid)}
  <path d="{area}" fill="#2f6f4f" opacity="0.07"/>
  <path d="{line}" fill="none" stroke="#2f6f4f" stroke-width="2" stroke-linejoin="round"/>
  {goal_svg}
  {''.join(marks)}
  {''.join(labels)}
</svg>"""


def sleep_chart(rows):
    """近 7 天睡眠分期堆叠柱。"""
    rows = rows[-7:]
    if not rows:
        return '<p class="note">暂无睡眠数据。</p>'
    W, H = 840, 190
    L, R, T, B = 52, 18, 24, 34
    iw, ih = W - L - R, H - T - B
    # 2026-10-26 修：上界用「分期之和」与「卧床时长」的较大值。
    # 原写法 total+awake 在 awake 已经含在 total 里时会重复计（10-25 夜会顶到 16.8h），
    # 把所有柱子压扁。
    cap = max(max((r.get("deep_min") or 0) + (r.get("light_min") or 0)
                  + (r.get("rem_min") or 0) + (r.get("awake_min") or 0),
                  (r["total_min"] or 0)) for r in rows)
    cap = max(cap, 480)
    n = len(rows)
    bw = min(52, iw / n * 0.55)

    def Y(m):
        return T + ih * (cap - m) / cap

    bars, labels = [], []
    for i, r in enumerate(rows):
        cx = L + iw * (i + 0.5) / n
        x = cx - bw / 2
        y = Y(0)
        segs = [("deep_min", "var(--deep)"), ("light_min", "var(--light)"),
                ("rem_min", "var(--rem)"), ("awake_min", "var(--awake)")]
        for key, col in segs:
            m = r.get(key) or 0
            if m <= 0:
                continue
            h = ih * m / cap
            y -= h
            rx = ' rx="2"' if key == "deep_min" else ""
            bars.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{h:.1f}" fill="{col}"{rx}/>')
        labels.append(f'<text x="{cx:.1f}" y="{H-12}" text-anchor="middle" font-size="11" fill="#9a9a94">{r["date"][5:]}</text>')
        # 柱高 = 卧床（含顶部灰色清醒段）；标注写实睡 = 卧床 − 清醒
        tot = max((r["total_min"] or 0) - (r["awake_min"] or 0), 0)
        bars.append(f'<text x="{cx:.1f}" y="{y-6:.1f}" text-anchor="middle" font-size="11" fill="#6f6f6a">{tot//60}h{tot%60:02d}</text>')

    grid = []
    for k in range(5):
        m = cap * k / 4
        y = Y(m)
        grid.append(f'<line x1="{L}" y1="{y:.1f}" x2="{W-R}" y2="{y:.1f}" stroke="#ececE6" stroke-width="1"/>')
        grid.append(f'<text x="{L-8}" y="{y+4:.1f}" text-anchor="end" font-size="11" fill="#9a9a94">{m/60:.0f}h</text>')

    return f"""<svg viewBox="0 0 {W} {H}" width="100%" role="img">
  <title>近 7 天睡眠分期</title>
  {''.join(grid)}{''.join(bars)}{''.join(labels)}
</svg>"""


def kcal_bar(low, high, target, lo_scale=0, hi_scale=3200):
    """当日热量区间条。"""
    if low is None and high is None:
        return '<p class="note">当日无饮食记录。</p>'
    W, H = 840, 74
    L, R = 10, 10
    iw = W - L - R
    y = 34
    barh = 20

    def X(v):
        v = max(lo_scale, min(hi_scale, v))
        return L + iw * (v - lo_scale) / (hi_scale - lo_scale)

    seg = ""
    if low is not None and high is not None:
        x1, x2 = X(low), X(high)
        seg = (f'<rect x="{x1:.1f}" y="{y}" width="{max(3, x2-x1):.1f}" height="{barh}" rx="5"'
               f' fill="#d85a30" opacity="0.85"/>'
               f'<text x="{(x1+x2)/2:.1f}" y="{y+barh+18}" text-anchor="middle" font-size="12"'
               f' fill="#8a3b1c" font-weight="500">{low}–{high} kcal</text>')
    tgt = ""
    if target:
        tx = X(target)
        tgt = (f'<line x1="{tx:.1f}" y1="{y-8}" x2="{tx:.1f}" y2="{y+barh+6}" stroke="#157f4a"'
               f' stroke-width="1.5" stroke-dasharray="4 3"/>'
               f'<text x="{tx:.1f}" y="{y-13}" text-anchor="middle" font-size="11" fill="#157f4a">目标 {target}</text>')

    ticks = []
    for v in range(0, hi_scale + 1, 800):
        x = X(v)
        ticks.append(f'<line x1="{x:.1f}" y1="{y+barh}" x2="{x:.1f}" y2="{y+barh+4}" stroke="#c9c9c2" stroke-width="1"/>')
        ticks.append(f'<text x="{x:.1f}" y="{y+barh+18}" text-anchor="middle" font-size="11" fill="#b0b0aa">{v}</text>')

    track = (f'<rect x="{L}" y="{y}" width="{iw}" height="{barh}" rx="5" fill="#f0efe9"/>')
    return f'<svg viewBox="0 0 {W} {H}" width="100%" role="img"><title>当日热量区间</title>{track}{seg}{tgt}{"".join(ticks)}</svg>'


# ---------------------------------------------------------------- 建议台账

def load_advice_log() -> list:
    if not ADVICE_LOG.exists():
        return []
    out = []
    for line in ADVICE_LOG.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


def advice_for_target(log, target_date):
    """取「目标日 == target_date」的那条建议（同日多条时取最后一条）。"""
    hits = [e for e in log if e.get("target_date") == target_date]
    return hits[-1] if hits else None


def save_advice_entry(entry):
    """写入台账；同一 report_date 已存在则覆盖（幂等）。"""
    log = [e for e in load_advice_log() if e.get("report_date") != entry.get("report_date")]
    log.append(entry)
    log.sort(key=lambda e: (e.get("report_date") or "", e.get("target_date") or ""))
    ADVICE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(ADVICE_LOG, "w", encoding="utf-8") as f:
        for e in log:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


def eval_check(check, ctx):
    """核验一条建议。返回 (status, actual_txt)，status ∈ done/miss/na。"""
    if not check:
        return ("na", "—")
    metric = check.get("metric")
    val = check.get("value")

    if metric == "steps":
        v = (ctx.get("activity") or {}).get("steps")
        if v is None:
            return ("na", "无数据")
        return ("done" if v >= val else "miss", f"{v:,} 步")

    if metric == "workout_min":
        v = ctx.get("workout_min")
        if v is None or v == 0:
            return ("na" if v is None else "miss", f"{v:g} 分" if v else "无运动记录")
        return ("done" if v >= val else "miss", f"{v:g} 分")

    if metric == "veg":
        v = ctx["nutrition"].get("veg_total")
        if v is None:
            return ("na", "无数据")
        return ("done" if v >= val else "miss", f"{v:g} 拳")

    if metric == "eating_window":
        f_, l_ = ctx.get("first_meal"), ctx.get("last_meal")
        if not f_ or not l_:
            return ("na", "无数据")
        start = ctx["cfg"].get("eating_window", {}).get("start", "12:00")
        end = ctx["cfg"].get("eating_window", {}).get("end", "20:00")
        ok = (f_ >= start) and (l_ <= end)
        return ("done" if ok else "miss", f"{f_}–{l_}")

    if metric == "sleep_min":
        sl = ctx.get("sleep")
        if not sl or not sl.get("total_min"):
            return ("na", "无数据")
        return ("done" if sl["total_min"] >= val else "miss",
                f"{sl['total_min']//60}h{sl['total_min']%60:02d}")

    if metric == "waist_weekly":
        # 周期任务：本周期（近 7 天）内是否测过
        w = ctx.get("waist")
        if not w:
            return ("na", "从未测量")
        d = date.fromisoformat(ctx["day"])
        wd = date.fromisoformat(w["date"])
        if (d - wd).days <= 6:
            return ("done", f"{w['date']} 已测 {w['waist_cm']:g} cm（本周期在轨，周频任务）")
        return ("miss", f"已 {(d - wd).days} 天未测")

    if metric == "intake_floor":
        v = ctx["nutrition"].get("kcal_low")
        if v is None:
            return ("na", "无数据")
        return ("done" if v >= val else "miss", f"下界 {v} kcal")

    return ("na", "—")


# ---------------------------------------------------------------- 计算

def calc_energy(weight_kg, cfg, activity):
    """热量账本：BMR + 当日实测活动 = 支出（口径A，D-42 用户定）。返回 dict 或 None。

    2026-10-16：口径B（BMR×activity_factor）的 tdee_low/tdee_high 已删除——
    用户定「缺口要看当天情况」，且要求删掉并排显示的系数法，避免相反结论混淆。
    config 的 weight_loss.activity_factor 现已无脚本引用（保留仅为留档）。
    """
    prof = cfg.get("profile", {})
    h = prof.get("height_cm")
    a = prof.get("age") or (datetime.now().year - prof.get("birth_year", 1983))
    if not h or not weight_kg:
        return None
    bmr = 10 * weight_kg + 6.25 * h - 5 * a + 5
    act_kcal = (activity or {}).get("active_kcal")
    out = {
        "weight_kg": weight_kg,
        "bmr": round(bmr),
        "active_kcal": act_kcal,
        "total": round(bmr + act_kcal) if act_kcal is not None else None,
        "age": a,
    }
    return out


def calc_gap(energy, nut):
    """缺口 = 支出 − 摄入（区间）。返回 dict 或 None。"""
    if not energy or energy["total"] is None:
        return None
    lo, hi = nut.get("kcal_low"), nut.get("kcal_high")
    if lo is None or hi is None:
        return None
    # 支出为点值，摄入为区间 → 缺口区间 = 支出 − 摄入（上下界互换）
    return {"low": energy["total"] - hi, "high": energy["total"] - lo,
            "mid": round(energy["total"] - (lo + hi) / 2)}


def diet_quality(nut, cfg, weight_kg):
    """饮食结构判定：蛋白 / 蔬菜 / 碳水。"""
    items = []
    ok_n = 0
    # 蛋白：减重期参考 1.2–1.6 g/kg（保肌）
    pl = nut.get("protein_low")
    if pl is None:
        items.append(("蛋白质", "数据不足", "na", ""))
    else:
        lo_t = round(1.2 * weight_kg) if weight_kg else 90
        hi_t = round(1.6 * weight_kg) if weight_kg else 124
        ok = pl >= lo_t
        items.append(("蛋白质", f"{nut['protein_low']:g}–{nut['protein_high']:g} g",
                      "ok" if ok else "warn",
                      f"参考 {lo_t}–{hi_t} g（1.2–1.6 g/kg）" + ("" if ok else " · 下界偏低")))
        ok_n += 1 if ok else 0
    # 蔬菜：≥4 拳/天
    v = nut.get("veg_total")
    if v is None:
        items.append(("蔬菜（纤维代理）", "数据不足", "na", ""))
    else:
        ok = v >= 4
        items.append(("蔬菜（纤维代理）", f"{v:g} 拳", "ok" if ok else "warn", "建议 ≥4 拳/天"))
        ok_n += 1 if ok else 0
    # 碳水：减重期宜中/低
    carb = nut.get("carb_level")
    if not carb:
        items.append(("碳水", "数据不足", "na", ""))
    else:
        ok = carb in ("low", "mid")
        items.append(("碳水", {"low": "低", "mid": "中", "high": "高"}.get(carb, carb),
                      "ok" if ok else "warn", "减重期宜中/低"))
        ok_n += 1 if ok else 0
    # 钙：中国 DRIs 2023，成人 18–49 岁 800 mg/天（50 岁以上 1000）。见 D-47。
    ca_t = (cfg.get("goals", {}) or {}).get("calcium_mg", 800)
    cl = nut.get("calcium_low")
    if cl is None:
        items.append(("钙", "数据不足", "na", "录入时带 --calcium-low/high 才会计入"))
    else:
        ch = nut.get("calcium_high") or cl
        covered = nut.get("calcium_meal_n", 0)
        total_n = nut.get("meal_n", 0)
        partial = "" if covered >= total_n else f" · 仅 {covered}/{total_n} 条记录含钙值，实为下限"
        ok = cl >= ca_t                      # 用区间下界判定：下界都不到＝确实不足
        edge = (not ok) and ch >= ca_t
        note_ca = (f"参考 {ca_t} mg/天（DRIs 2023 成人）"
                   + ("" if ok else (" · 上界才勉强够" if edge else " · 明显偏低"))
                   + partial)
        items.append(("钙", f"{cl:.0f}–{ch:.0f} mg", "ok" if ok else "warn", note_ca))
        ok_n += 1 if ok else 0
    need = len(items)
    verdict = "基本合理" if ok_n >= need - 1 else "需调整"
    if ok_n == need:
        verdict = "合理"
    return items, verdict


def build_advice(ctx) -> list:
    """规则版建议，带分类 + 机器可核验 check。

    用户要求（2026-10-13）：今日建议必须**分运动 / 饮食 / 睡眠三类都有输出**——
    达标时给「保持 + 微调」，不达标时给具体动作。不能因为合格就沉默。

    例外（2026-10-16 用户定 · D-41）：22:00 当日速览不出睡眠条——当日报告结构上
    查不到"今夜"的睡眠，出了只会显示一条必然错误的文案。睡眠归 10:10 昨日汇总。
    """
    a = []
    cfg = ctx["cfg"]
    g = cfg.get("goals", {})
    th = cfg.get("thresholds", {})

    # ── 运动 ──
    act = ctx["activity"]
    steps = (act or {}).get("steps")
    tgt_steps = g.get("steps_target", 8000)
    wmin = ctx.get("workout_min") or 0
    tgt_act = th.get("active_minutes_target", 30)
    rhr, base = ctx["rhr"], ctx["rhr_base"]
    overreached = (rhr is not None and base is not None and rhr - base >= 5)

    # 2026-10-25 加：昨夜睡眠严重不足（<5h）时，运动建议整体降级 ——
    # 缺觉时练得越狠恢复越差，减重期更不能靠「多练」补「没睡」。
    _sl_chk = None if ctx.get("no_sleep") else ctx.get("sleep")
    _sleep_short = bool(_sl_chk and _sl_chk.get("total_min")
                        and _sl_chk["total_min"] < 300)

    if overreached:
        a.append({"cat": "运动",
                  "text": f"今晨静息心率 {rhr} bpm，比近 30 天均值高 {rhr - base} bpm。"
                          f"今日降低运动强度（改成快走 / 散步即可），优先保证睡眠与水分。",
                  "check": None})
    elif _sleep_short:
        _sm = _sl_chk["total_min"]
        a.append({"cat": "运动",
                  "text": f"昨夜只睡 {_sm//60} 小时 {_sm%60} 分（严重不足），"
                          f"昨日运动虽已达标，今日仍建议<b>只做低强度</b>：散步 20–30 分钟即可，"
                          f"不做高强度、不加量——先补觉。缺觉时练得越狠，恢复越差。",
                  "check": None})
    elif steps is not None and steps < tgt_steps * 0.8:
        # 2026-10-26 加：休息日步数低属正常，不催「补走」——判定锚定星期几，不锚定步数
        _wp = cfg.get("workout_plan") or {}
        _rest = set(_wp.get("rest_weekdays") or [])
        _is_rest = bool(_rest) and date.fromisoformat(ctx["day"]).isoweekday() in _rest
        if _is_rest:
            a.append({"cat": "运动",
                      "text": f"昨日为休息恢复日，{steps:,} 步属正常范围，<b>不必补走</b>"
                              f"（散步是恢复手段，不计中高强度考核、也不算断档）。今日按计划训练即可。",
                      "check": {"metric": "steps", "value": tgt_steps}})
        else:
            a.append({"cat": "运动",
                      "text": f"昨日 {steps:,} 步，未达 {tgt_steps:,} 步目标。今日晚饭后补 20 分钟步行。",
                      "check": {"metric": "steps", "value": tgt_steps}})
    elif wmin < tgt_act:
        # 2026-10-28 修：休息日 0 分钟中高强度属计划内，不能催「今日补一次」
        _wp2 = cfg.get("workout_plan") or {}
        _rest2 = set(_wp2.get("rest_weekdays") or [])
        _is_rest2 = bool(_rest2) and date.fromisoformat(ctx["day"]).isoweekday() in _rest2
        if _is_rest2:
            a.append({"cat": "运动",
                      "text": f"昨日为休息恢复日，无中高强度运动属计划内（散步是恢复手段），"
                              f"<b>不算断档</b>。今日按计划练 {tgt_act} 分钟即可。",
                      "check": None})
        else:
            a.append({"cat": "运动",
                      "text": f"昨日中高强度运动 {wmin:g} 分，不足 {tgt_act} 分。"
                              f"今日安排一次 {tgt_act} 分钟以上中强度运动（快走 / 踏步 / 单车均可）。",
                      "check": {"metric": "workout_min", "value": tgt_act}})
    else:
        a.append({"cat": "运动",
                  "text": f"昨日 {steps:,} 步 + 中高强度运动 {wmin:g} 分，{tgt_steps:,} 步与 {tgt_act} 分双双达标。"
                          f"今日保持同样强度即可，不必加量——本项目的原则是优先慢、不优先快。",
                  "check": {"metric": "workout_min", "value": tgt_act}})

    # ── 饮食 ──
    nut = ctx["nutrition"]
    diet_hits = []
    if nut.get("veg_total") is not None and nut["veg_total"] < 4:
        a.append({"cat": "饮食",
                  "text": f"昨日蔬菜约 {nut['veg_total']:g} 拳，偏少。今日午晚两餐各补一拳蔬菜。",
                  "check": {"metric": "veg", "value": 4}})
    else:
        diet_hits.append("蔬菜")
    if nut.get("protein_low") is not None and nut["protein_low"] < 60:
        a.append({"cat": "饮食",
                  "text": f"昨日蛋白质约 {nut['protein_low']:g}–{nut['protein_high']:g} g，偏低。"
                          f"今日加一份蛋 / 豆制品 / 瘦肉。",
                  "check": None})
    else:
        diet_hits.append("蛋白")
    if nut.get("carb_level") in ("low", "mid"):
        diet_hits.append("碳水")
    if nut.get("kcal_low") is not None and nut["kcal_low"] < ctx["intake_floor"]:
        a.append({"cat": "饮食",
                  "text": f"摄入区间下界 {nut['kcal_low']} kcal 低于 {ctx['intake_floor']} kcal 下限，"
                          f"缺口有拉大的迹象。今日主食与蛋白都别减，把缺口控在 300–750 kcal。",
                  "check": {"metric": "intake_floor", "value": ctx["intake_floor"]}})
    if ctx.get("window_ok") is False:
        a.append({"cat": "饮食",
                  "text": f"昨日有进食落在窗口外（{ctx.get('first_meal')}–{ctx.get('last_meal')}）。"
                          f"今日回到 12:00–20:00 窗口内，窗口外只喝水。",
                  "check": {"metric": "eating_window", "value": 1}})
    if diet_hits and not any(x["cat"] == "饮食" for x in a):
        conf_tip = ("今日午餐拍摄建议改拍「整桌平铺全景」——能把摄入区间收窄，缺口才判得准。"
                    if nut.get("kcal_low") is not None and (nut["kcal_high"] - nut["kcal_low"]) > 400 else "")
        # 2026-10-26 加：钙不在 diet_hits 里，原句「三项均达标」会让人误以为全项达标
        ca_t = (cfg.get("goals", {}) or {}).get("calcium_mg", 800)
        ca_l = nut.get("calcium_low")
        ca_h = nut.get("calcium_high") or ca_l
        ca_tip = ""
        if ca_l is not None and ca_l < ca_t:
            if ca_h is not None and ca_h >= ca_t:
                ca_tip = (f"钙 {ca_l:.0f}–{ca_h:.0f} mg 是唯一短板——下界距 {ca_t:g} mg 还差 "
                          f"{ca_t - ca_l:.0f} mg、上界已够，今日补一杯脱脂奶 200 ml（约 210 mg）即可。")
            else:
                ca_tip = (f"钙 {ca_l:.0f}–{ca_h:.0f} mg 上下界均低于 {ca_t:g} mg——"
                          f"今日补一杯脱脂奶 200 ml + 一拳深绿叶菜（西兰花 / 芥蓝）。")
        # 2026-10-26 加：原句硬编码「晚餐走奶昔 + 蛋 + 少量剩菜」是作者旧习惯，
        # 与实际结构无关，已改成按当日碳水水平给（10-25 晚餐实为牛奶+蔬菜+炒肉，完全对不上）。
        _carb = nut.get("carb_level")
        if _carb == "high":
            _keep = "今日把主食减半、优先留到午餐，蔬菜与一拳蛋白别减。"
        elif _carb in ("low", "mid"):
            _keep = ("今日保持「蔬菜 4 拳 + 一拳蛋白」的结构，主食别再减——"
                     "昨日碳水已偏低，再减会先掉肌肉。")
        else:
            _keep = "今日保持昨日同样的蔬菜与蛋白量，主食按饱腹感正常安排。"
        a.append({"cat": "饮食",
                  "text": f"饮食结构三项（{'、'.join(diet_hits)}）均达标，昨日进食也完全落在 12:00–20:00 窗口内。"
                          + ca_tip + _keep + conf_tip,
                  "check": {"metric": "eating_window", "value": 1}})

    # ── 睡眠 ──
    # 2026-10-16 用户定：22:00 当日速览不出睡眠（结构上查不到"今夜"的数据，
    # 出了只会显示一条必然错误的"昨夜没有睡眠记录"）。睡眠归 10:10 昨日汇总。
    if ctx.get("no_sleep"):
        pass
    else:
        sl = ctx["sleep"]
        tgt_sleep = g.get("sleep_target_min", 450)
        if not sl or not sl.get("total_min"):
            a.append({"cat": "睡眠",
                      "text": "昨夜没有睡眠记录（可能未佩戴设备，或数据尚未同步）。"
                              "今晚正常佩戴入睡——减重期睡眠债会直接抬升饥饿感，这一项不能长期缺。",
                      "check": {"metric": "sleep_min", "value": tgt_sleep}})
        else:
            if sleep_fragment(sl, cfg):
                a.append({"cat": "睡眠",
                          "text": f"昨夜只记录到 {sl['start_ts'][11:16]}–{sl['end_ts'][11:16]} 这一段"
                                  f"（{sl['total_min']//60} 小时 {sl['total_min']%60} 分），"
                                  f"此前没有夜间睡眠记录 → 整夜时长不可判，本条不作「睡眠不足」结论。"
                                  f"今晚确认佩戴到位，明早先看是否补上完整夜间段。",
                          "check": {"metric": "sleep_min", "value": tgt_sleep}})
            elif sl["total_min"] < tgt_sleep - 30:
                # 2026-10-25 加：按缺口分档 —— 通宵/严重不足时「提前 30 分钟」剂量不够
                _shr = tgt_sleep - sl["total_min"]
                if _shr >= 120:
                    _adv_txt = (f"昨晚入睡 {sl['start_ts'][11:16]}、只睡了 "
                                f"{sl['total_min']//60} 小时 {sl['total_min']%60} 分，"
                                f"比目标少 {_shr} 分钟——属严重不足。今晚提前 60–90 分钟上床，"
                                f"白天补 20–30 分钟午睡（不晚于 15:00），今天不做高强度运动。")
                else:
                    _adv_txt = (f"昨晚睡了 {sl['total_min']//60} 小时 {sl['total_min']%60} 分，比目标少 "
                                f"{_shr} 分钟。今晚提前 30 分钟上床——"
                                f"先把入睡时间稳住，比补时长更有效。")
                a.append({"cat": "睡眠", "text": _adv_txt,
                          "check": {"metric": "sleep_min", "value": tgt_sleep}})
            else:
                # 2026-10-29 修：时长达标但入睡偏晚时，不能再说「维持同一入睡时间」——
                # 10-28 入睡 01:04（前夜 23:40），原文案等于让人继续 01:04 睡。
                # 阈值定在「跨过零点」：23:40 这类只差 10 分钟的不报，避免制造噪声。
                _st = (sl.get("start_ts") or "")[11:16]
                _late = len(_st) == 5 and _st[:2].isdigit() and int(_st[:2]) < 12
                _txt = (f"昨晚睡眠 {sl['total_min']//60} 小时 {sl['total_min']%60} 分，达到目标。"
                        + (f"但入睡 <b>{_st}</b> 已跨零点，时长是靠起得晚补回来的——"
                           f"今晚提前 <b>30–60 分钟</b>上床，把入睡点挪回 23:30 前。"
                           if _late else "今晚维持同一入睡时间与时长即可。"))
                a.append({"cat": "睡眠", "text": _txt,
                          "check": {"metric": "sleep_min", "value": tgt_sleep}})
            if sl.get("awaken_min") and sl["awaken_min"] >= 120:
                # 2026-10-26 加：卧床够长但清醒超过 2 小时 → 问题在"碎"不在"短"，
                # 别给"早睡"这类药不对症的建议。
                a.append({"cat": "睡眠",
                          "text": f"昨夜卧床 {sl['inbed_min']//60} 小时 {sl['inbed_min']%60} 分，"
                                  f"但其中清醒 <b>{sl['awaken_min']} 分</b>（超过 2 小时）——"
                                  f"睡着约 {sl['total_min']//60} 小时 {sl['total_min']%60} 分。"
                                  f"主问题不是上床晚，是睡眠太碎：今晚把咖啡因截止到 14:00 前、"
                                  f"睡前 1 小时远离屏幕、卧室温度调到 20–22℃；"
                                  f"若连续一周夜间清醒都超过 1 小时，建议找医生看看。",
                          "check": None})
            if sl.get("wake_count") and sl["wake_count"] >= 3:
                a.append({"cat": "睡眠",
                          "text": f"昨夜醒了 {sl['wake_count']} 次。睡前 2 小时避开咖啡因与酒精，观察一周。",
                          "check": None})

    # ── 体重 / 腰围 ──
    w = ctx["waist"]
    if w and w.get("waist_cm") and w["waist_cm"] >= th.get("waist_abdominal_obesity_cm", 90):
        a.append({"cat": "体重",
                  "text": f"最近腰围 {w['waist_cm']:g} cm，仍在腹型肥胖区间。"
                          f"保持每周固定同一时间测一次并盯趋势，不用天天看体重。",
                  "check": {"metric": "waist_weekly", "value": 1}})
    return a


def floor_only_word(value, floor, normal="正常", low="过低"):
    """只需「不低于下限」的指标（血氧、蛋白摄入下限等）：低于下限才判低。"""
    if value is None:
        return "数据不足", "na"
    return (normal, "ok") if value >= floor else (low, "warn")


def verdict_word(value, lo, hi, unit="", higher_better=True, na_text="数据不足"):
    """化验单式判定：**先给一个方向词结论**，再谈数值。

    用户要求（2026-10-15）：需注意的指标必须"一个词讲明白结果"——
    过高 / 过低 / 偏大 / 偏小 / 达标，而不是只写个 tag。

    返回 (结论词, 状态)。状态 ok / warn / na 对应达标 / 需注意 / 数据不足。
    """
    if value is None:
        return na_text, "na"
    if lo is not None and value < lo:
        return ("过低" if higher_better else "过小"), "warn"
    if hi is not None and value > hi:
        return ("过高" if higher_better else "过大"), "warn"
    return "达标", "ok"


def gap_word(gap, lo_s, hi_s):
    """热量缺口专用：缺口过小 = 没有进展，过大 = 掉肌肉风险。"""
    if not gap:
        return "数据不足", "na"
    mid = gap["mid"]
    if mid < lo_s:
        return "过小", "warn"
    if mid > hi_s:
        return "过大", "warn"
    return "达标", "ok"


def sleep_fragment(sl, cfg=None) -> bool:
    """这条睡眠是否「疑似只有片段」（数据不完整，不能当整夜判）。

    2026-10-25 加。实例：10-25 云端只返回 06:22→08:45 一段 143 分，
    源库里此前没有任何夜间段。若直接当整夜，会把「数据缺失」渲染成
    「只睡了 2 小时」，进而给出「今晚提前 30 分钟上床」这类错误建议。

    判据（保守，只抓明显异常）：起点钟点落在 04:00–12:00 —— 正常入睡应在前一夜；
    且总时长 < 5.5 小时。两条同时成立才判片段，避免误伤「晚睡但睡够」的日子。

    ⚠ 例外（2026-10-25 加）：config `sleep_confirmed_short` 里登记过的日期，
    表示用户已确认那天是**真短睡**（熬夜 / 通宵），此时不做片段判定 ——
    **口述优先于算法推断**。否则「真熬夜」会被反判成「数据缺失」。
    """
    if not sl or not sl.get("start_ts") or not sl.get("total_min"):
        return False
    d = sl.get("date")
    for it in (cfg or {}).get("sleep_confirmed_short") or []:
        key = it.get("date") if isinstance(it, dict) else it
        if key and str(key) == str(d):
            return False          # 已人工确认 = 真实短睡，按真实数据判
    try:
        hhmm = sl["start_ts"][11:16]
        mins = int(hhmm[:2]) * 60 + int(hhmm[3:5])
    except Exception:
        return False
    return (4 * 60 <= mins <= 12 * 60) and sl["total_min"] < 330


def trend_word(seq, lo, hi, higher_better=True, tol=None):
    """趋势判定：**方向词只描述方向**，好坏由 higher_better 决定。

    tol：绝对容差（与 seq 同单位）。传 None 时退回「0.5 或 2%」的相对口径。
    ⚠️ 2026-10-18 修：原文按 `|first3| × 2%` 算容差，75 kg 体重下 = 1.5 kg，
       而减重目标只有 0.4–0.8 kg/周 → **达标反被判「基本持平」**（再映射成「持平未降」）。
       体重侧改为显式传 tol=0.4。
    ⚠️ 同次修：原末行 `"连续上升" if falling else "连续下降"` 方向词与状态是反的
       （跌了却写"上升"），且体重侧传 higher_better=True 会把"下降=好"判成 warn。
       现统一为：下降→"持续下降"、上升→"持续上升"，ok/warn 由 higher_better 定。
    """
    seq = [v for v in seq if v is not None]
    if len(seq) < 3:
        return "数据不足", "na"
    first3 = sum(seq[:3]) / 3
    last3 = sum(seq[-3:]) / 3
    if tol is None:
        tol = max(0.5, abs(first3) * 0.02)
    if abs(last3 - first3) <= tol:
        # 持平在减重期 = 没有进展，属需留意（不是"达标"）
        return "基本持平", ("ok" if higher_better else "warn")
    falling = last3 < first3
    word = "持续下降" if falling else "持续上升"
    good = (not falling) if higher_better else falling
    return word, ("ok" if good else "warn")


def diet_verdict_words(dq_items):
    """饮食结构：按项给方向词（蛋白质摄入过低 / 碳水过高 / 蔬菜达标）。"""
    words = []
    for k, v, cls, note in dq_items:
        if cls == "na":
            words.append(f"{k} 数据不足")
        elif cls == "ok":
            words.append(f"{k} 达标")
        elif "蛋白" in k:
            words.append("蛋白质摄入过低")
        elif "蔬菜" in k:
            words.append("蔬菜摄入不足")
        elif "碳水" in k:
            words.append("碳水过高")
        elif "钙" in k:
            words.append("钙摄入不足")
        else:
            words.append(f"{k} 需注意")
    return words


def build_week_ctx(con, day: str, days: int = 7, workout_plan: dict | None = None) -> dict:
    """本周（近 N 天）执行情况——「今日建议」要结合长周期趋势，不能只看昨天。

    用户要求（2026-10-15）：今日建议根据运动/睡眠/饮食的**长期目标**以及
    **本周整体执行情况**给出，所以这里统计蛋白趋势、运动连续天数、
    步数趋势、饮食记录天数，供建议文案引用。

    2026-10-24 增补：
      · strength_days —— 窗口内含抗阻类运动的天数（判「零抗阻」结构性短板）
      · last_workout  —— 报告日 workout 明细（判休息日散步用）
      · workout_plan  —— config 的周计划（train_weekdays 等），供按星期几判定
    """
    d0 = date.fromisoformat(day)
    start = (d0 - timedelta(days=days - 1)).isoformat()
    out = {"days": days, "start": start,
           "protein_trend": [], "workout_streak": 0, "workout_days": 0,
           "steps": [], "meal_days": 0, "sleep_days": 0, "veg_days_ok": 0}

    # 蛋白下界逐日序列（看趋势方向）
    for r in con.execute(
            "SELECT date, SUM(protein_g_low) AS plo, SUM(veg_servings) AS veg, "
            "SUM(kcal_low) AS klo FROM meal WHERE date >= ? AND date <= ? "
            "GROUP BY date ORDER BY date", (start, day)):
        out["protein_trend"].append((r["date"], round(r["plo"] or 0)))
        out["steps"]  # 占位，保持字段顺序可读
        if (r["veg"] or 0) >= 4:
            out["veg_days_ok"] += 1
    out["meal_days"] = len(out["protein_trend"])

    # 步数逐日
    out["steps"] = [(r["date"], r["steps"]) for r in con.execute(
        "SELECT date, steps FROM activity_daily WHERE date >= ? AND date <= ? ORDER BY date",
        (start, day))]

    # 运动连续天数（从 day 往回数）
    wdays = {r["date"] for r in con.execute(
        "SELECT DISTINCT date(start_ts) AS date FROM workout "
        "WHERE date(start_ts) <= ? AND date(start_ts) >= ?", (day, start))}
    n = 0
    for i in range(days):
        if (d0 - timedelta(days=i)).isoformat() in wdays:
            n += 1
        else:
            break
    out["workout_streak"] = n
    # 窗口内「有运动的天数」——(2026-10-19 加) 休息日会让 streak 归零，
    #   只用 streak 会把「连续 6 天后正常休息 1 天」写成「节奏还没连上」。
    out["workout_days"] = len(wdays)

    # 抗阻判定（2026-10-24 加）：sport 命中以下关键词才算抗阻；
    # 爬楼机 / 步行 / 踏步机等全为有氧，不算。
    _STRENGTH_KW = ("strength", "resistance", "weight", "dumbbell", "barbell",
                    "kettlebell", "band", "pushup", "push_up", "squat", "plank",
                    "力量", "哑铃", "杠铃", "俯卧撑", "深蹲", "平板支撑")
    day_sport = [(r["date"], (r["sport"] or "").lower()) for r in con.execute(
        "SELECT DISTINCT date(start_ts) AS date, sport FROM workout "
        "WHERE date(start_ts) >= ? AND date(start_ts) <= ?", (start, day))]
    out["strength_days"] = len({d for d, s in day_sport
                                if any(k in s for k in _STRENGTH_KW)})

    # 报告日的 workout 明细（判「休息日散步」用）
    lw = con.execute(
        "SELECT sport, duration_min, avg_hr, kcal FROM workout "
        "WHERE date(start_ts) = ? ORDER BY start_ts", (day,)).fetchone()
    out["last_workout"] = dict(lw) if lw else None
    out["workout_plan"] = workout_plan or {}

    # 睡眠记录天数（D-37 新口径：报告日 D 对应库内 date = D+1，故这里跨到 day+1）
    d1 = (d0 + timedelta(days=1)).isoformat()
    out["sleep_days"] = con.execute(
        "SELECT COUNT(*) FROM sleep_session WHERE date >= ? AND date <= ?",
        (start, d1)).fetchone()[0]
    # 2026-10-29 修：原来文案把上面这个「30 天窗口记录数」写成「本周 N 天记录」，
    # 10-28 因此出现「本周 8 天记录」（一周只有 7 天）。单列一个真实的近 7 夜计数。
    s7 = (d0 - timedelta(days=5)).isoformat()
    out["sleep_days_7"] = con.execute(
        "SELECT COUNT(*) FROM sleep_session WHERE date >= ? AND date <= ?",
        (s7, d1)).fetchone()[0]
    return out


def week_trend_phrase(wk: dict, key: str, unit: str, higher_better=True) -> str:
    """把逐日序列变成一句趋势描述（供建议文案引用）。数据不足时返回空串。

    2026-10-24 改：前/后分段**均值 → 中位数**。均值会被单个离群日带反方向
    （实例：10-20 孤立低值 3618 步把「后 3 天均 10340」的结论带偏，
    用户定案改中位数，见 docs/04-决策记录.md）。
    """
    seq = [v for _, v in wk.get(key) or []]
    if len(seq) < 3:
        return ""
    half = len(seq) // 2

    def _med(vals):
        s = sorted(vals)
        n = len(s)
        return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2

    first_med = _med(seq[:half]) if half else seq[0]
    last_med = _med(seq[-half:]) if half else seq[-1]
    if abs(last_med - first_med) < max(1.0, abs(first_med) * 0.05):
        return (f"近 {len(seq)} 天基本持平（前 {half} 天中位 {first_med:.0f} → "
                f"后 {half} 天中位 {last_med:.0f} {unit}）")
    arrow = "上升" if last_med > first_med else "下降"
    good = (last_med > first_med) if higher_better else (last_med < first_med)
    return (f"近 {len(seq)} 天{arrow}（前 {half} 天中位 {first_med:.0f} → "
            f"后 {half} 天中位 {last_med:.0f} {unit}，"
            f"{'方向正确' if good else '方向需留意'}）")


def _judge_rows(ctx) -> list:
    """判定行：默认 饮食结构 / 睡眠 / 运动。每行 = (键, 状态, 正文HTML)。

    no_sleep=True（22:00 当日速览，D-41）时不出睡眠行，只返 饮食/运动 两行。

    规则：
      · 状态只取 ok / warn / na 三档（良好 / 需注意 / 数据不足）
      · 判定为「需注意」或「良好」时，正文**必须写清具体是什么**——
        用户原话「注意什么」；空泛的结论等于没说。
    """
    cfg = ctx["cfg"]
    g = cfg.get("goals", {})
    th = cfg.get("thresholds", {})
    rows = []

    # ── 饮食结构 ──
    nut = ctx["nutrition"]
    wkg = ctx.get("weight_energy") or (ctx.get("body") or {}).get("weight_kg")
    pro_lo_t = round(1.2 * wkg) if wkg else 90
    pro_hi_t = round(1.6 * wkg) if wkg else 124
    floor = ctx.get("intake_floor", 1500)
    good_bits, bad_bits = [], []
    if nut.get("veg_total") is not None:
        if nut["veg_total"] >= 4:
            good_bits.append(f"蔬菜 <b>{nut['veg_total']:g} 拳</b>达标（≥4 拳）")
        else:
            bad_bits.append(f"蔬菜仅 <b>{nut['veg_total']:g} 拳</b>，不足 4 拳")
    if nut.get("protein_low") is not None:
        if nut["protein_low"] >= pro_lo_t:
            # 2026-10-28 修：区间整段高于参考上限时，不能说「在参考 X–Y 内」。
            # 判定实取下界（蛋白偏低才是风险），但措辞必须与数值一致。
            _ph = nut.get("protein_high") or nut["protein_low"]
            if nut["protein_low"] > pro_hi_t:
                good_bits.append(f"蛋白质 <b>{nut['protein_low']:g}–{_ph:g} g</b>"
                                 f"高于参考 {pro_lo_t}–{pro_hi_t} g（保肌角度是好事）")
            elif _ph > pro_hi_t:
                good_bits.append(f"蛋白质 <b>{nut['protein_low']:g}–{_ph:g} g</b>"
                                 f"下界在参考 {pro_lo_t} g 之上、上界超出 {pro_hi_t} g")
            else:
                good_bits.append(f"蛋白质 <b>{nut['protein_low']:g}–{_ph:g} g</b>"
                                 f"在参考 {pro_lo_t}–{pro_hi_t} g 内")
        else:
            bad_bits.append(f"蛋白质 <b>{nut['protein_low']:g}–{nut['protein_high']:g} g</b>，"
                            f"下界低于参考 {pro_lo_t} g")
    carb = nut.get("carb_level")
    if carb:
        if carb in ("low", "mid"):
            good_bits.append(f"碳水<b>{ {'low': '低', 'mid': '中'}.get(carb, carb) }</b>（减重期宜中/低）")
        else:
            bad_bits.append("碳水<b>偏高</b>")
    ca_l = nut.get("calcium_low")
    if ca_l is not None:
        ca_t = (cfg.get("goals", {}) or {}).get("calcium_mg", 800)
        ca_h = nut.get("calcium_high") or ca_l
        if ca_l >= ca_t:
            good_bits.append(f"钙 <b>{ca_l:.0f}–{ca_h:.0f} mg</b>达标（≥{ca_t}）")
        else:
            # 2026-10-26 改：下界/上界分开说，避免「605–886 低于 800」这种自相矛盾的句子
            if ca_h >= ca_t:
                bad_bits.append(f"钙 <b>{ca_l:.0f}–{ca_h:.0f} mg</b>，下界低于 {ca_t} mg（上界已达标）")
            else:
                bad_bits.append(f"钙 <b>{ca_l:.0f}–{ca_h:.0f} mg</b>，上下界均低于 {ca_t} mg 参考")
    if ctx.get("window_ok") is True:
        good_bits.append(f"进食窗口 <b>{ctx['first_meal']}–{ctx['last_meal']}</b> 完全落在 "
                         f"{cfg.get('eating_window', {}).get('start', '12:00')}–"
                         f"{cfg.get('eating_window', {}).get('end', '20:00')} 内")
    elif ctx.get("window_ok") is False:
        bad_bits.append(f"有进食落在窗口外（{ctx.get('window_out')}）")
    if nut.get("kcal_low") is not None and nut["kcal_low"] < floor:
        bad_bits.append(f"摄入区间下界 <b>{nut['kcal_low']} kcal</b> 低于 {floor} kcal 下限")

    if nut.get("kcal_low") is None:
        rows.append(("饮食结构", "na", "当日没有饮食记录，无法判定。"))
    else:
        # 2026-10-28 修（历史复现 4 次：10-16 / 10-18 / 10-20 / 10-28）：
        # 「摄入量够不够」不属于「饮食结构」，混进同一行会让一句话总结写「饮食结构需注意」，
        # 而同页总览写「饮食结构 各项达标 4/4」，自相矛盾。
        # 改法：结构项（蛋白/蔬菜/碳水/钙/窗口）单独判定；仅当摄入下界偏低时行名改为「饮食摄入」。
        _struct_bad = [b for b in bad_bits if "摄入区间下界" not in b]
        _intake_bad = any("摄入区间下界" in b for b in bad_bits)
        _label = "饮食结构" if _struct_bad else ("饮食摄入" if _intake_bad else "饮食结构")
        parts = []
        if good_bits:
            parts.append("；".join(good_bits) + "；")
        if bad_bits:
            parts.append("；".join(bad_bits) + "。")
        if _struct_bad and _intake_bad:
            tail = "——结构与量都要调。"
        elif _struct_bad:
            tail = "——结构有偏差，量不是问题。"
        elif _intake_bad:
            tail = "——结构没问题，是量偏少。"
        else:
            tail = "。"
        body_html = "".join(parts) + f'<span class="dim">{tail}</span>'
        rows.append((_label, "warn" if bad_bits else "ok", body_html))

    # ── 睡眠 ──
    if ctx.get("no_sleep"):
        pass   # 当日速览不出睡眠行（2026-10-16 用户定）
    else:
        sl = ctx.get("sleep")
        tgt = g.get("sleep_target_min", 450)
        if not sl or not sl.get("total_min"):
            rows.append(("睡眠", "na",
                         "昨夜没有睡眠记录（可能未佩戴设备，或数据尚未同步）——"
                         "减重期睡眠债会直接抬升饥饿感，这一项不能长期缺。"))
        else:
            tmin = sl["total_min"]
            bits = [f"<b>{tmin//60} 小时 {tmin%60} 分</b>"]
            if tmin >= tgt:
                bits.append(f"达到 {tgt//60} 小时 {tgt%60} 分目标")
                state = "ok"
            else:
                bits.append(f"距 {tgt//60} 小时 {tgt%60} 分目标还差 <b>{tgt - tmin} 分钟</b>")
                state = "warn"
            if sl.get("start_ts") and sl.get("end_ts"):
                bits.append(f"入睡 {sl['start_ts'][11:16]} · 起床 {sl['end_ts'][11:16]}")
            if sleep_fragment(sl, cfg):
                bits.append("<b>⚠ 云端只返回了这一小段</b>，未见更早的夜间段，"
                            "实际整夜可能更长——本条只作记录，不据此判定整夜睡眠")
            if sl.get("awaken_min"):
                # 2026-10-26：清醒过久时拆开说 —— 别把卧床时长当睡眠时长
                bits.append(f"卧床 <b>{sl['inbed_min']//60} 小时 {sl['inbed_min']%60} 分</b>，"
                            f"其中清醒 <b>{sl['awaken_min']} 分</b>，睡着即上面这个时长")
            elif sl.get("awake_min"):
                bits.append(f"夜间清醒仅 {sl['awake_min']} 分")
            wk = ctx.get("week") or {}
            if wk.get("sleep_days", 0) <= 1:
                bits.append("这是本项目第一条睡眠数据，"
                            "先稳住，连续记满 3 天再判趋势")
            rows.append(("睡眠", state, "；".join(bits) + "。"))

    # ── 运动 ──
    act = ctx["activity"]
    steps = (act or {}).get("steps")
    tgt_steps = g.get("steps_target", 8000)
    tgt_act = th.get("active_minutes_target", 30)
    wmin = ctx.get("workout_min") or 0
    rhr, base = ctx["rhr"], ctx["rhr_base"]
    if steps is None and not wmin:
        rows.append(("运动", "na", "当日没有活动数据，无法判定。"))
    else:
        # 2026-10-26 加：休息恢复日不做步数考核（锚定星期几，不锚定步数）
        _wp = cfg.get("workout_plan") or {}
        _rest = set(_wp.get("rest_weekdays") or [])
        _is_rest = bool(_rest) and date.fromisoformat(ctx["day"]).isoweekday() in _rest
        bits, bad = [], []
        if steps is not None:
            if steps >= tgt_steps:
                bits.append(f"<b>{steps:,} 步</b>（目标 {tgt_steps:,}）")
            elif _is_rest:
                bits.append(f"<b>{steps:,} 步</b>（休息恢复日，步数目标不考核）")
            else:
                bad.append(f"步数 <b>{steps:,}</b>，距目标 {tgt_steps:,} 还差 {tgt_steps - steps:,}")
        if wmin >= tgt_act:
            bits.append(f"中高强度 <b>{wmin:g} 分钟</b>（目标 {tgt_act}）")
        elif wmin > 0:
            bad.append(f"中高强度仅 <b>{wmin:g} 分钟</b>，不足 {tgt_act} 分钟")
        if rhr is not None and base is not None:
            if rhr - base >= 5:
                bad.append(f"静息心率 {rhr} bpm，比 30 天均值高 {rhr - base} bpm")
            elif base - rhr >= 5:
                # 2026-10-28 修：低于均值 5 bpm 以上不能说「持平」（45 vs 51 曾写持平）
                bits.append(f"静息心率 <b>{rhr} bpm</b>，低于 30 天均值 {base} bpm")
            else:
                bits.append(f"静息心率 {rhr} bpm 与 30 天均值持平")
        state = "warn" if bad else "ok"
        # 2026-10-24 改：有氧达标但窗口内零抗阻时，不再说「不必再加」——
        # 全有氧 + 蛋白不稳是瘦体重流失的结构性原因（第一周复盘结论）。
        if not bad and wmin >= tgt_act:
            if (ctx.get("week") or {}).get("strength_days", 0) == 0:
                _slx = None if ctx.get("no_sleep") else ctx.get("sleep")
                if _slx and _slx.get("total_min") and _slx["total_min"] < 300:
                    # 2026-10-25：严重缺觉日不再让「加抗阻」与其他建议打架
                    tail = ("——有氧强度已够；但今夜严重缺觉，抗阻改到睡够之后再加，"
                            "今日先补觉。")
                else:
                    tail = "——有氧强度已够；但近 7 天仍<b>零抗阻</b>，掉肌肉的结构性问题还在，建议运动后加 10 分钟自重抗阻。"
            else:
                tail = "——强度够了，不必再加。"
        else:
            tail = "。"
        rows.append(("运动", state, "；".join(bits + bad) + f"。<span class='dim'>{tail}</span>"))
    return rows


def _headline_advice(ctx) -> list:
    """今日建议头部三条（饮食 / 运动 / 睡眠）——每类只留最关键的一条。

    与下方「今日建议」详细版的区别：这里**必须结合长期目标与本周执行情况**，
    而不是只对昨天单点响应；详细版仍保留完整规则建议。

    no_sleep=True（22:00 当日速览，D-41）时不出睡眠条，只返 饮食/运动 两条。
    """
    cfg = ctx["cfg"]
    g = cfg.get("goals", {})
    wl = cfg.get("weight_loss", {})
    wk = ctx.get("week") or {}
    nut = ctx["nutrition"]
    out = []

    # ── 饮食 ──
    wkg = ctx.get("weight_energy") or (ctx.get("body") or {}).get("weight_kg")
    pro_lo_t = round(1.2 * wkg) if wkg else 90
    floor = ctx.get("intake_floor", 1500)
    bits = []
    ptrend = week_trend_phrase(wk, "protein_trend", "g", higher_better=True)
    seq = [v for _, v in wk.get("protein_trend") or []]
    # 2026-10-24 改：原「seq[-1] < seq[-3] 即判连降，文案引用 seq[0]（窗口起点旧值）」
    # 两处都会被离群日带偏（实例：129 g 是 10-17 旧值，中间 78→100→118→100→113 并非连降）。
    # 改用最小二乘斜率判方向，文案给窗口中枢与最近值。
    _slope = None
    if len(seq) >= 4:
        _n = len(seq)
        _xs = list(range(_n))
        _mx = (_n - 1) / 2
        _my = sum(seq) / _n
        _sxx = sum((x - _mx) ** 2 for x in _xs)
        _sxy = sum((x - _mx) * (y - _my) for x, y in zip(_xs, seq))
        _slope = (_sxy / _sxx) if _sxx else 0.0   # g/天
    if _slope is not None and _slope <= -1.5:     # 每天掉 ≥1.5 g（≈一周 −10 g）才算走低
        # 2026-10-26 改：斜率越线但最近一天已回升时，不再说「走低」——
        # 实例 10-25：斜率 −1.57 触发，文案却写成「中枢 93 g，最近 94 g」，自相矛盾。
        if seq[-1] < _my:
            bits.append(f"本周蛋白质走低（{_n} 天中枢 {_my:.0f} g，最近 {seq[-1]:g} g）"
                        "是当前最明确的短板")
        else:
            bits.append(f"蛋白质近 {_n} 天整体略降（中枢 {_my:.0f} g），"
                        f"但最近一天已回到 {seq[-1]:g} g")
    elif _slope is not None and _slope >= 1.5:   # 2026-10-25 加：上升方向不再回退分段中位
        bits.append(f"蛋白质近 {_n} 天在回升（中枢 {_my:.0f} g，最近 {seq[-1]:g} g）")
    elif _slope is not None:
        # 斜率平稳时也不回退 median 分段 —— 两者口径不一致会自相矛盾
        # （实例：10-24 斜率 +1.46 判「上升」，median 分段却写成「近 7 天下降」）
        bits.append(f"蛋白质近 {_n} 天基本平稳（中枢 {_my:.0f} g，最近 {seq[-1]:g} g）")
    elif ptrend:
        bits.append(f"蛋白质{trend_tail(ptrend)}")
    if nut.get("protein_low") is not None and nut["protein_low"] < pro_lo_t:
        bits.append(f"今日晚餐补 <b>一拳蛋白</b>（鸡胸 / 蛋清 / 豆腐），"
                    f"把下界抬回参考 {pro_lo_t} g 以上")
    if nut.get("kcal_low") is not None and nut["kcal_low"] < floor:
        bits.append(f"摄入下界 <b>{nut['kcal_low']} kcal</b> 低于 {floor}，"
                    f"主食别减，让缺口回到 {int(wl.get('deficit_safe_kcal_day', [300, 750])[0])}–"
                    f"{wl.get('deficit_safe_kcal_day', [300, 750])[1]} 达标区")
    if nut.get("veg_total") is not None and nut["veg_total"] < 4:
        bits.append("午晚两餐各补一拳蔬菜")
    ca_l = nut.get("calcium_low")
    if ca_l is not None:
        ca_t = (cfg.get("goals", {}) or {}).get("calcium_mg", 800)
        if ca_l < ca_t:
            ca_h = nut.get("calcium_high") or ca_l
            # 2026-10-24 改：原 gap 用「参考 − 上界」算，上界达标时出现「差约 0 mg」的错话；
            # 改为下界差值 + 上界达标与否两个事实分开说。
            gap_lo = int(round(ca_t - ca_l))
            hi_note = "，上界已达标" if ca_h >= ca_t else ""
            bits.append(f"钙 <b>{ca_l:.0f}–{ca_h:.0f} mg</b>，下界距 {ca_t} mg 还差 "
                        f"{gap_lo} mg{hi_note} —— "
                        f"今天加 <b>一杯脱脂奶 200 ml（约 210 mg）或 200 g 无糖酸奶</b>，"
                        f"再配一拳深绿色叶菜（西兰花 / 芥蓝）就能补齐")
    if nut.get("kcal_low") is None:
        bits.insert(0, "昨日没有饮食记录，无法判定结构。"
                        "今日先把三餐拍清楚——尤其是烹调油量，这是估算误差的最大来源")
    elif not bits:
        bits.append("四项（蛋白、蔬菜、碳水、钙）均在参考区间内，"
                    "今日保持同样结构：主食留午餐、晚餐走「蛋白 + 蔬菜」，不额外加油")
    out.append(("饮食", "；".join(bits) + "。"))

    # ── 运动 ──
    # 2026-10-24 重写（用户 2026-10-22 定案）：判定锚定「星期几」，不锚定「连续天数」。
    # 训练日练了 = 达标；训练日没练 = 真断档；休息日 = 正常（散步属恢复手段，不算断档）。
    # 原 workout_streak 的「本周已连续 N 天 / 节奏还没连上」措辞一律废除。
    dlo, dhi = wl.get("target_window_months", [6, 9])
    goal = g.get("weight_kg", 65)
    start = wl.get("start_kg", 77)
    need = max(0, start - goal)
    # kg/周 = 需减总量 ÷ 每月约 4.345 周 ÷ 月数（D-20 口径：6 个月 ≈ 0.47 kg/周，9 个月 ≈ 0.31）
    r_mo = need / 4.345 / dlo
    r_wk = need / 4.345 / dhi
    tgt_act = cfg.get("thresholds", {}).get("active_minutes_target", 30)
    train_wd = set((wk.get("workout_plan") or {}).get("train_weekdays") or [])
    strength_days = wk.get("strength_days", 0)
    lw = wk.get("last_workout") or {}
    trained_min = ctx.get("workout_min") or 0

    bits = []
    # 2026-10-25 加：昨夜严重缺觉（<5h）时，头部运动建议同步降级 ——
    # 否则会出现「只做低强度（详细版）」与「加 10 分钟抗阻（头部）」自相矛盾。
    _sl_h = None if ctx.get("no_sleep") else ctx.get("sleep")
    _short_h = bool(_sl_h and _sl_h.get("total_min") and _sl_h["total_min"] < 300)
    if train_wd:
        d0 = date.fromisoformat(ctx["day"])
        dow = d0.isoweekday()
        d_next = d0 + timedelta(days=1)          # 「今日」= 报告日 +1
        next_train = d_next.isoweekday() in train_wd
        _pace = (f"{need:g} kg 要走 {dlo}–{dhi} 个月，"
                 f"每周 {r_wk:.2f}–{r_mo:.2f} kg 就够，优先慢、不优先快")
        if dow in train_wd and trained_min >= tgt_act:
            bits.append(f"昨日为计划训练日，已练 <b>{trained_min:g} 分钟</b>，达标")
            if _short_h:
                _sm = _sl_h["total_min"]
                bits.append(f"但昨夜只睡 <b>{_sm//60} 小时 {_sm%60} 分</b>，"
                            f"今日只做 <b>低强度散步 20–30 分钟</b>，不做高强度、不加量——先补觉")
            else:
                if next_train:
                    bits.append(f"今日同为计划训练日，维持 <b>30–40 分钟</b>即可，"
                                f"<b>不加量</b>——{_pace}")
                else:
                    # 2026-10-28 修：休息恢复日原写「维持 30–40 分钟即可」，
                    # 与同句「休息恢复日」自相矛盾（10-26 记录过，本次复现）。
                    bits.append(f"今日为休息恢复日，<b>不安排中高强度</b>，"
                                f"散步 20–30 分钟即够，<b>不加量</b>——{_pace}")
        elif dow in train_wd:
            bits.append(f"昨日为计划训练日但没有训练记录，这周是<b>真断档</b>——"
                        f"今日把 <b>{tgt_act} 分钟</b>中强度补上（{_pace}）")
        else:
            lw_sport = (lw.get("sport") or "").lower()
            lw_min = lw.get("duration_min") or 0
            if lw_sport and ("walk" in lw_sport or "walking" in lw_sport):
                bits.append(f"昨日为休息恢复日，{lw_min:g} 分钟散步属恢复手段，"
                            f"<b>不算断档</b>，节奏正常")
            else:
                bits.append("昨日为休息恢复日，节奏正常")
            bits.append(f"今日{'是计划训练日，练 <b>30–40 分钟</b>即可' if next_train else '同样以恢复为主、可散步'}，"
                        f"<b>不加量</b>——{_pace}")
        if strength_days == 0:
            _r_verb = "今日运动后加" if next_train else "今日补上"
            bits.append("近 7 天仍<b>零抗阻</b>——掉秤里一半是瘦体重的结构性原因，"
                        + ("抗阻先缓一缓，等睡够了再加" if _short_h
                           else f"{_r_verb} <b>10 分钟自重抗阻</b>")
                        + "（深蹲 3×12 / 俯卧撑 3×10 / 弹力带划船 3×12）")
    else:
        # 未配置 workout_plan → 退回「窗口内有运动天数」的旧口径（不提连续天数）
        act_days = len(wk.get("steps") or [])
        if act_days == 0:
            bits.append("本周没有活动记录，无法判断运动节奏。"
                        f"今日正常佩戴设备，并安排一次 <b>{tgt_act} 分钟</b>中强度运动"
                        "（快走 / 踏步 / 单车）")
        else:
            wdays = wk.get("workout_days", 0)
            win = wk.get("days", 7)
            if trained_min >= tgt_act:
                bits.append(f"昨日中高强度 <b>{trained_min:g} 分钟</b>达标；"
                            f"近 {win} 天共 <b>{wdays} 天</b>有运动")
            elif wdays >= 2:
                bits.append(f"近 {win} 天有 <b>{wdays} 天</b>中高强度运动；"
                            f"今日恢复一次 <b>{tgt_act} 分钟</b>中强度运动，<b>不加量</b>")
            else:
                bits.append(f"近 {win} 天仅 {wdays} 天有运动，"
                            f"今日安排一次 <b>{tgt_act} 分钟</b>以上中强度运动（快走 / 踏步 / 单车）")
            if strength_days == 0:
                bits.append("近 7 天仍<b>零抗阻</b>——掉秤里一半是瘦体重的结构性原因，"
                            + ("抗阻先缓一缓，等睡够了再加" if _short_h
                               else "今日运动后加 <b>10 分钟自重抗阻</b>")
                            + "（深蹲 3×12 / 俯卧撑 3×10 / 弹力带划船 3×12）")
    st = week_trend_phrase(wk, "steps", "步", higher_better=True)
    if st:
        bits.append(st.replace("方向正确", "步数方向正确").replace("方向需留意", "步数方向需留意"))
    out.append(("运动", "；".join(bits) + "。"))

    # ── 睡眠 ──
    if ctx.get("no_sleep"):
        pass   # 当日速览不出睡眠建议（2026-10-16 用户定）
    else:
        sl = ctx.get("sleep")
        tgt = g.get("sleep_target_min", 450)
        bits = []
        sdays = wk.get("sleep_days", 0)
        if not sl or not sl.get("total_min"):
            bits.append("昨夜没有睡眠记录。今晚正常佩戴入睡，"
                        "减重期睡眠债会直接抬升饥饿感")
        else:
            tmin = sl["total_min"]
            if sdays <= 1:
                bits.append(f"昨夜 {tmin//60} 小时 {tmin%60} 分达标，但目前只有 "
                            f"<b>{sdays} 天</b>样本，还谈不上趋势")
            elif sleep_fragment(sl, cfg):
                bits.append(f"昨夜<b>只记录到 {tmin//60} 小时 {tmin%60} 分</b>"
                            f"（起点 {sl['start_ts'][11:16]}），未见更早的夜间段 → "
                            "疑似未佩戴或未同步，整夜时长不可判")
                bits.append("今晚确认佩戴到位，明早先看有没有补上完整夜间段")
                out.append(("睡眠", "；".join(bits) + "。"))
                return out
            elif tmin < tgt - 30:
                if tgt - tmin >= 120:      # 2026-10-25：严重不足（通宵/熬夜）档
                    bits.append(f"昨夜入睡 {sl['start_ts'][11:16]}、只睡 "
                                f"<b>{tmin//60} 小时 {tmin%60} 分</b>，"
                                f"距目标差 {tgt - tmin} 分钟，属<b>严重不足</b>")
                    bits.append("今晚提前 <b>60–90 分钟</b>上床，白天补 <b>20–30 分钟</b>午睡"
                                "（不晚于 15:00）——今天先补觉，别用运动补")
                    out.append(("睡眠", "；".join(bits) + "。"))
                    return out
                bits.append(f"昨夜 {tmin//60} 小时 {tmin%60} 分，距目标差 {tgt - tmin} 分钟")
            else:
                bits.append(f"昨夜 {tmin//60} 小时 {tmin%60} 分达标，"
                            f"近 7 夜已记录 {wk.get('sleep_days_7', sdays)} 夜，继续保持")
            bits.append("今晚保持 <b>23:30 前上床</b>，先把入睡时间稳住")
        out.append(("睡眠", "；".join(bits) + "。"))
    return out


def trend_tail(phrase: str) -> str:
    """把 week_trend_phrase 的长句压成短尾巴，用于头部短文案。
    2026-10-24：不再自带句号——拼接处统一加，否则出现「上升。。」双句号。"""
    return phrase.split("（")[0]


def build_headline(ctx) -> str:
    """新版一句话总结（2026-10-15 用户定版 · D-38）。

    结构：一句总判断 → 判定行（默认 饮食/睡眠/运动 三项）→ 今日建议块。
    当日速览（no_sleep=True）只剩 饮食/运动 两行，故行数与称谓一律动态算，
    不写死「三项 / 昨天」——2026-10-16 改。
    下方接「详细指标如下」分隔线 + 原有核心指标总览等全部内容。
    """
    day = ctx["day"]
    rows = _judge_rows(ctx)
    adv = _headline_advice(ctx)
    state_map = {"ok": ("良好", "ok"), "warn": ("需注意", "warn"), "na": ("数据不足", "na")}
    verdicts = [state_map[s][0] for _, s, _ in rows]
    ok_n = verdicts.count("良好")

    # 行数与日称谓都动态：no_sleep 时 rows 只有 2 行；当日速览说"当天"
    n = len(rows)
    cn_n = {0: "零项", 1: "一项", 2: "两项", 3: "三项"}.get(n, f"{n} 项")
    dl = ctx.get("day_label", "昨天")

    # 一句总判断
    if n == 0:
        lead = "当日没有可判定数据，先补记录。"
    elif "数据不足" in verdicts and ok_n == 0:
        lead = f"{dl}{cn_n}数据都不完整，判断不了——先补记录。"
    elif "需注意" not in verdicts:
        lead = f"{dl}{cn_n}<strong>全部达标</strong>，保持这个状态。"
    elif ok_n == 0:
        need = [k for k, s, _ in rows if s == "warn"]
        lead = f"{dl}{cn_n}<strong>都需要留意</strong>：{'、'.join(need)}。"
    else:
        need = [k for k, s, _ in rows if s == "warn"]
        good = [k for k, s, _ in rows if s == "ok"]
        parts = []
        if good:
            parts.append(f"<strong>{'、'.join(good)}达标</strong>")
        if need:
            parts.append(f"<strong>{'、'.join(need)}需注意</strong>")
        cn_g = {1: "一项", 2: "两项", 3: "三项"}.get(len(good), f"{len(good)} 项")
        cn_b = {1: "一项", 2: "两项", 3: "三项"}.get(len(need), f"{len(need)} 项")
        tail = (f"——{cn_g}达标、{cn_b}拖后腿。" if good and need else "。")
        lead = dl + "，".join(parts) + tail

    def esc(t):
        return t

    rows_html = []
    for k, s, v in rows:
        label, cls = state_map[s]
        rows_html.append(f'<div class="hrow"><div class="k">{k}'
                         f'<span class="pill {cls}">{label}</span></div>'
                         f'<div class="v">{v}</div></div>')

    adv_html = []
    for k, v in adv:
        adv_html.append(f'<div class="advrow"><div class="k">{k}</div>'
                        f'<div class="v">{v}</div></div>')

    return (f'<div class="hl">'
            f'<div class="hl-top"><div class="t">一句话总结 · {day}</div>'
            f'<div class="lead">{lead}</div></div>'
            f'<div class="hl-rows">{"".join(rows_html)}</div>'
            f'<div class="hl-adv"><div class="h">今日建议 · '
            f'{(date.fromisoformat(day) + timedelta(days=1)).isoformat()}</div>'
            f'{"".join(adv_html)}</div>'
            f'</div>')


def build_conclusion(ctx) -> str:
    """一句话总结（用户要求：开头就能看清昨天）。"""
    parts = []
    body = ctx.get("body")
    if body and body.get("weight_kg"):
        parts.append(f"体重 {wt(body['weight_kg'], ctx['cfg'])}")
    if ctx["rhr"] is not None:
        parts.append(f"静息心率 {ctx['rhr']} bpm")
    act = ctx["activity"]
    if act and act.get("steps") is not None:
        parts.append(f"{act['steps']:,} 步")
    if ctx.get("workout_min"):
        parts.append(f"运动 {ctx['workout_min']:g} 分")
    nut = ctx["nutrition"]
    if nut.get("kcal_low") is not None:
        parts.append(f"摄入 {nut['kcal_low']}–{nut['kcal_high']} kcal")
    en = ctx.get("energy")
    if en and en.get("total"):
        parts.append(f"支出约 {en['total']} kcal")
    gp = ctx.get("gap")
    if gp:
        parts.append(f"缺口约 {gp['low']:+d}~{gp['high']:+d} kcal")
    if not parts:
        return "当日数据不足，无法给出综合判断。"
    return " · ".join(parts) + "。"


# ---------------------------------------------------------------- 渲染

def _dist_txt(activity) -> str:
    """步数旁边的距离展示，带步幅合理性门控。

    2026-10-29 加：设备的 distance_km 长期未校准，换手环后步幅在 0.20–0.70 之间乱跳
    （10-28 为 0.34 m/步，正常步行约 0.67），直接展示会给出错误里程。
    口径：步幅落在 0.50–0.90 才当成可信距离；否则只标「不可信」，不进趋势、不做换算。
    步数与活动 kcal 不受此字段影响，仍照常判定。
    """
    if not activity or not activity.get("distance_km") or not activity.get("steps"):
        return ""
    stride = activity["distance_km"] * 1000.0 / activity["steps"]
    if 0.50 <= stride <= 0.90:
        return f" · 距离 {activity['distance_km']:g} km"
    return f" · 距离字段失真不展示（步幅 {stride:.2f} m/步，正常约 0.67）"


def render(con, cfg, day: str, no_sleep: bool = False) -> str:
    th = cfg.get("thresholds", {})
    days_trend = th.get("trend_days", 30)
    days_detail = th.get("report_days", 7)
    g = cfg.get("goals", {})
    prof = cfg.get("profile", {})
    wl = cfg.get("weight_loss", {})

    d0 = date.fromisoformat(day)
    start = (d0 - timedelta(days=days_trend - 1)).isoformat()
    # 睡眠取数口径（D-37）：报告日 D 展示「D 夜 → D+1 晨」那一觉——
    # 对早上读报告的人来说就是"昨夜的睡眠"。库内归属日 = 起床日，
    # 故单日取 date = D+1；近 7 天分期图窗口同步右移一天。
    day_next = (d0 + timedelta(days=1)).isoformat()
    start7 = (d0 - timedelta(days=days_detail - 2)).isoformat()

    body_rows = many(con, "SELECT date, weight_kg, body_fat_pct FROM body_metric "
                          "WHERE date <= ? AND date >= ? ORDER BY date", (day, start))
    sleep_rows = many(con, "SELECT * FROM sleep_session WHERE date <= ? AND date >= ? ORDER BY date",
                      (day_next, start7))
    sleep = one(con, "SELECT * FROM sleep_session WHERE date = ?", (day_next,))
    # 2026-10-26 加：夜间清醒必须从「睡眠时长」里扣掉，只留在「卧床时长」里。
    # 起因：10-25 夜卧床 741 分（21:26→09:48），stages 逐段汇总清醒 267 分，
    # 而源库 time_awake_minutes 只写 1 分 → 原口径把 12h21 当"睡眠达标"，实际实睡 7h55。
    # 口径：清醒 ≥30 分 且 占卧床 ≥8% 才拆分（日常 0–1 分不动，历史行不受影响）。
    # 归一化后：total_min = 实睡（判定与文案基准），inbed_min / awaken_min = 卧床 / 清醒。
    if sleep and sleep.get("total_min"):
        _inbed = sleep["total_min"]
        _aw = sleep.get("awake_min") or 0
        if _aw >= 30 and _aw >= _inbed * 0.08:
            sleep = dict(sleep)
            sleep["inbed_min"] = _inbed
            sleep["awaken_min"] = _aw
            sleep["total_min"] = max(_inbed - _aw, 0)
    activity = one(con, "SELECT * FROM activity_daily WHERE date = ?", (day,))
    rhr = one(con, "SELECT bpm FROM heart_rate WHERE date(ts)=? AND hr_type='resting' ORDER BY ts DESC LIMIT 1", (day,))
    rhr_avg = one(con, "SELECT AVG(bpm) AS v FROM heart_rate WHERE hr_type='resting' AND date(ts) <= ? "
                       "AND date(ts) >= ?", (day, start))
    spo2 = one(con, "SELECT value FROM spo2 WHERE date(ts)=? ORDER BY ts DESC LIMIT 1", (day,))
    stress = one(con, "SELECT value FROM stress WHERE date(ts)=? ORDER BY ts DESC LIMIT 1", (day,))
    hr_day = one(con, "SELECT MIN(bpm) AS lo, MAX(bpm) AS hi, COUNT(*) AS n, AVG(bpm) AS avg "
                      "FROM heart_rate WHERE date(ts)=? AND hr_type IN ('passive','active')", (day,))
    waist = one(con, "SELECT * FROM waist WHERE date <= ? ORDER BY date DESC LIMIT 1", (day,))
    body = one(con, "SELECT * FROM body_metric WHERE date <= ? ORDER BY date DESC LIMIT 1", (day,))
    body_today = one(con, "SELECT * FROM body_metric WHERE date = ?", (day,))
    # 2026-10-29 修：「今晨值」限定在报告日次日以内。原写法取全表最新一条，
    # 导致重生成历史报告时出现「10-27 日报写今晨 10-29」这类穿越文案。
    body_latest = one(con, "SELECT * FROM body_metric WHERE date <= ? ORDER BY date DESC LIMIT 1",
                      ((d0 + timedelta(days=1)).isoformat(),))
    workouts = many(con, "SELECT * FROM workout WHERE date(start_ts)=? ORDER BY start_ts", (day,))
    meals = many(con, "SELECT * FROM meal WHERE date = ? ORDER BY time", (day,))
    portions = many(con, "SELECT * FROM portion_library ORDER BY times_used DESC LIMIT 5")

    kcal_low = sum(m["kcal_low"] or 0 for m in meals) if meals else None
    kcal_high = sum(m["kcal_high"] or 0 for m in meals) if meals else None
    pro_low = sum(m["protein_g_low"] or 0 for m in meals) if meals else None
    pro_high = sum(m["protein_g_high"] or 0 for m in meals) if meals else None
    veg = sum(m["veg_servings"] or 0 for m in meals) if meals else None

    # 钙（2026-10-22 起追踪，见 D-47）：只汇总「有值」的记录，缺记的餐不按 0 计
    ca_rows = [m for m in meals if m.get("calcium_mg_low") is not None]
    ca_low = sum(m["calcium_mg_low"] for m in ca_rows) if ca_rows else None
    ca_high = (sum((m["calcium_mg_high"] if m.get("calcium_mg_high") is not None
                    else m["calcium_mg_low"]) for m in ca_rows) if ca_rows else None)

    # 碳水档位：按餐取众数
    carb_counts = {}
    for m in meals:
        if m.get("carb_level"):
            carb_counts[m["carb_level"]] = carb_counts.get(m["carb_level"], 0) + 1
    carb_level = max(carb_counts, key=carb_counts.get) if carb_counts else None

    nutrition = {"kcal_low": kcal_low, "kcal_high": kcal_high,
                 "protein_low": pro_low, "protein_high": pro_high,
                 "veg_total": veg, "carb_level": carb_level,
                 "calcium_low": ca_low, "calcium_high": ca_high,
                 "calcium_meal_n": len(ca_rows), "meal_n": len(meals)}

    workout_min = sum(w["duration_min"] or 0 for w in workouts) if workouts else None
    first_meal = meals[0]["time"] if meals else None
    last_meal = meals[-1]["time"] if meals else None
    win = cfg.get("eating_window", {})
    window_ok = None
    window_out = []
    if first_meal and last_meal:
        window_ok = (first_meal >= win.get("start", "12:00")) and (last_meal <= win.get("end", "20:00"))
        for m in meals:
            if m["time"] < win.get("start", "12:00") or m["time"] > win.get("end", "20:00"):
                window_out.append(m["time"])

    # 2026-10-29 修：报告日体重读数若被判定为「单日跳变异常」，且存在已复核的相邻有效读数，
    # 则能量核算（BMR / 蛋白参考）改用有效读数。
    # 起因：10-28 晨 72.4 kg（前一日 74.2、次日 74.0，前后都不接，已复核证伪），
    # 报告仍按 72.4 算 BMR 1583 并写「距目标还差 7.4 kg」，真实应为 74.0 / 9.0 kg。
    # 原则与 10-28 定的「异常读数不进趋势判据」一致，只是从趋势词延伸到主值本身。
    weight_eff = (body or {}).get("weight_kg")
    weight_anom_src = None
    weight_anom_prev = None
    if body and body.get("weight_kg") and body_latest and body_latest.get("weight_kg") \
            and body_latest["date"] != body["date"]:
        _prev_b = con.execute(
            "SELECT weight_kg FROM body_metric WHERE date < ? AND weight_kg IS NOT NULL "
            "ORDER BY date DESC LIMIT 1", (body["date"],)).fetchone()
        _j_prev = abs(body["weight_kg"] - _prev_b[0]) if _prev_b else 0.0
        _j_next = abs(body_latest["weight_kg"] - body["weight_kg"])
        if _j_prev > 0.8 and _j_next > 0.8:
            weight_eff = body_latest["weight_kg"]
            weight_anom_src = body          # 保留被证伪的那一行，用于文案
            weight_anom_prev = _prev_b[0] if _prev_b else None
    weight_for_energy = weight_eff
    energy = calc_energy(weight_for_energy, cfg, activity)
    gap = calc_gap(energy, nutrition)

    is_demo = con.execute(
        "SELECT (SELECT COUNT(*) FROM activity_daily WHERE source='demo') + "
        "(SELECT COUNT(*) FROM sleep_session WHERE source='demo') + "
        "(SELECT COUNT(*) FROM body_metric WHERE source='demo') AS n").fetchone()[0] > 0

    ctx = {"cfg": cfg, "day": day, "sleep": sleep, "activity": activity,
           "rhr": rhr["bpm"] if rhr else None,
           "rhr_base": round(rhr_avg["v"]) if rhr_avg and rhr_avg["v"] else None,
           "waist": waist, "body": body, "body_today": body_today,
           "weight_energy": weight_for_energy,
           "nutrition": nutrition, "workout_min": workout_min,
           "first_meal": first_meal, "last_meal": last_meal, "window_ok": window_ok,
           "window_out": window_out, "energy": energy, "gap": gap,
           "intake_floor": wl.get("intake_floor_kcal", 1500),
           "deficit_safe": wl.get("deficit_safe_kcal_day", [300, 750]),
           # 睡眠板块开关：True = 完全不出睡眠（总览行/三行判定/建议/分期图）
           "no_sleep": bool(no_sleep),
           # 报告日相对"今天"的称谓：当日速览说"当天"，昨日汇总说"昨天"
           "day_label": "当天" if day == date.today().isoformat() else "昨天",
           "week": build_week_ctx(con, day, days_detail,
                                  workout_plan=cfg.get("workout_plan"))}

    kcal_target = int(g.get("calorie_target_kcal") or round(g.get("weight_kg", 70) * 30))

    # ── 执行率：取「目标日 == 本报告日」的历史建议 ──
    log = load_advice_log()
    prev_entry = advice_for_target(log, day)
    exec_rows = []
    if prev_entry:
        for it in prev_entry.get("items", []):
            st, actual = eval_check(it.get("check"), ctx)
            exec_rows.append({"text": it.get("text", ""), "cat": it.get("cat", ""),
                              "status": st, "actual": actual})
    done_n = sum(1 for r in exec_rows if r["status"] == "done")
    na_n = sum(1 for r in exec_rows if r["status"] == "na")
    judge_n = len(exec_rows) - na_n
    rate = (done_n / judge_n * 100) if judge_n else None

    # ── 建议（本报告日给出，目标日 = 次日）──
    advice = build_advice(ctx)
    next_day = (d0 + timedelta(days=1)).isoformat()
    save_advice_entry({
        "report_date": day, "target_date": next_day,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "items": advice,
    })

    # ════════════════ 第一层：一眼看明白 ════════════════

    # ① 一句话总结（D-38 新版式：总判断 + 三行判定 + 今日建议）
    headline = build_headline(ctx)

    # ② 核心指标总览（D-39：体检化验单样式 —— 项目 | 结论 | 数值 | 参考范围）
    # 每项 = (项目, 结论词, 结论状态, 数值HTML, 参考范围HTML)
    ov = []
    pro_lo_t = round(1.2 * weight_for_energy) if weight_for_energy else 90
    pro_hi_t = round(1.6 * weight_for_energy) if weight_for_energy else 124

    def add(item, word, cls, val, ref, flag=False):
        ov.append((item, word, cls, val, ref, flag))

    # ── 体格 ──
    if body and body.get("weight_kg"):
        cur = weight_eff or body["weight_kg"]
        goal_w = g.get("weight_kg", 65.0)
        latest = body_latest or body
        # 2026-10-28 修：缺口一律按「本报告主值」算，不能拿今晨值算缺口却显示报告日数值
        #（10-28 曾出现「74.2 kg · 距目标还差 7.4 kg」，而 74.2−65=9.2，自相矛盾）。
        left = cur - goal_w
        _d_latest = (latest["weight_kg"] - cur) if latest else 0.0
        src_map = {"scale_mcp": "体脂秤", "photo": "照片", "manual": "手动"}
        src = src_map.get(body.get("source"), body.get("source") or "—")
        # 体重（减重语义）：**下降=达标**，故 higher_better=False。
        # 容差用绝对量 0.4 kg（≈速率安全下沿 0.4 kg/周 × 6 条记录窗口）：
        #   原相对口径 2%×75kg≈1.5 kg 会把「周减 0.4–0.8 达标」误判成持平（2026-10-18 修）。
        # 持平仍在此处手工提级为 warn —— 减重期没进展属于需留意。
        # 2026-10-29：报告日读数被证伪时，趋势序列先剔除该点再判词。
        _trend_rows = [r for r in body_rows
                       if not (weight_anom_src and r["date"] == weight_anom_src["date"])]
        trend_seq = [r["weight_kg"] for r in _trend_rows if r.get("weight_kg")][-6:]
        wword, wcls = trend_word(trend_seq, None, None, higher_better=False, tol=0.4)
        if wword == "基本持平":
            wword, wcls = "持平未降", "warn"
        src_map_latest = src_map.get(latest.get("source"), "—")
        unit = cfg.get("profile", {}).get("weight_unit", "jin")
        goal_txt = f"{goal_w:g} kg" + (f"（{goal_w * 2:g} 斤）" if unit == "jin" else "")
        left_txt = f"{left:.1f} kg" + (f"（{left * 2:.1f} 斤）" if unit == "jin" else "")
        if weight_anom_src:
            # 报告日读数已被相邻两点证伪（前一日与今晨都不接）：主值改用已复核的有效读数，
            # 把异常点单列说明。数值宁可少一个「漂亮数字」，也不能让错误读数决定缺口与距目标。
            _aw = weight_anom_src["weight_kg"]
            ref = (f"目标 <b>{goal_txt}</b> · 距目标还差 <b>{left_txt}</b><br>"
                   f"今晨 {latest['date'][5:]} <b>{wt(latest['weight_kg'], cfg)}</b>"
                   f"（{src_map_latest}，已复核）· 来源 {src} · 近 6 日<b>{wword}</b><br>"
                   f"⚠ {body['date'][5:]} 读数 <b>{_aw:g} kg</b> 判为异常：较前一记录 "
                   f"<b>{((weight_anom_prev or _aw) - _aw):+.1f} kg</b>（{weight_anom_prev or 0:g}）、"
                   f"较今晨 <b>{latest['weight_kg'] - _aw:+.1f} kg</b>（{latest['weight_kg']:.1f}），"
                   f"前后两点都不接，疑测量条件不同（称重时间 / 是否排尿 / 空腹），"
                   f"<b>该点不进趋势判据</b>，趋势词已剔除后重算")
        elif latest["date"] != body["date"]:
            # 单日变动过大（>0.8 kg）时如实标「待复核」，不让异常读数悄悄改写趋势
            _anom = (f" ⚠ 单日变动 {_d_latest:+.1f} kg，超出正常波动，"
                     f"疑测量条件不同（称重时间 / 是否排尿 / 空腹），<b>不进趋势判据</b>，请复核"
                     ) if abs(_d_latest) > 0.8 else ""
            ref = (f"目标 <b>{goal_txt}</b> · 距目标还差 "
                   f"<b>{left_txt}</b><br>"
                   f"今晨 {latest['date'][5:]} {wt(latest['weight_kg'], cfg)}"
                   f"（{src_map_latest}）{_anom}"
                   f" · 来源 {src} · 近 6 日<b>{wword}</b>")
        else:
            # 2026-10-28 修：当天速览下 latest == body（同日），上面的跳变标记永不触发，
            # 导致 10-28「单日 −1.8 kg」这类异常读数被当成权威值写进趋势词。
            # 改用「前一有效记录 → 本日」对比；命中跳变时趋势词重算并剔除当日点。
            _hist = [r for r in body_rows if r["date"] < body["date"] and r.get("weight_kg")]
            _prev_w = _hist[-1]["weight_kg"] if _hist else None
            _jump = (cur - _prev_w) if _prev_w is not None else 0.0
            _is_anom = abs(_jump) > 0.8
            _anom = ""
            if _is_anom:
                _seq2 = [r["weight_kg"] for r in _hist][-6:]
                if len(_seq2) >= 2:
                    wword, wcls = trend_word(_seq2, None, None, higher_better=False, tol=0.4)
                    if wword == "基本持平":
                        wword, wcls = "持平未降", "warn"
                _anom = (f"<br>⚠ 较前一记录 <b>{_jump:+.1f} kg</b>"
                         f"（{_prev_w:g} → {cur:g}，{_hist[-1]['date'][5:]}）超出正常波动，"
                         f"疑测量条件不同（称重时间 / 是否排尿 / 空腹），"
                         f"<b>本日值不进趋势判据</b>，趋势词已按剔除此点重算，请复核")
            ref = (f"目标 <b>{goal_txt}</b> · 距目标还差 <b>{left_txt}</b>"
                   f" · 来源 {src} · 近 6 日<b>{wword}</b>{_anom}")
        add("体重", wword, wcls,
            wt(cur, cfg, html=True), ref, wcls == "warn")
    if waist and waist.get("waist_cm"):
        wc = waist["waist_cm"]
        wgoal = g.get("waist_cm", 88.0)
        # 腰围：超过腹型肥胖线（上限）→ 方向词用「过大」（"过高"留给速率类）
        word, cls = ("过大", "warn") if wc >= th.get("waist_abdominal_obesity_cm", 90) \
            else ("达标", "ok")
        add("腰围", word, cls, f"{wc:g} <span class='u'>cm</span>",
            f"目标 <b>{wgoal:g} cm</b> · 腹型肥胖线 <b>90 cm</b>（≥90 判腹型肥胖）<br>"
            f"测量日 {waist['date']} · 周频指标，每周同一时间测一次", cls == "warn")

    # ── 心肺 ──
    if ctx["rhr"] is not None:
        base = ctx["rhr_base"]
        word, cls = verdict_word(ctx["rhr"], th.get("resting_hr_low", 40),
                                 th.get("resting_hr_high", 90))
        if base and ctx["rhr"] - base >= 5:
            word, cls = "偏快", "warn"
        ref = f"参考 <b>{th.get('resting_hr_low', 40)}–{th.get('resting_hr_high', 90)} bpm</b>"
        if base:
            ref += f" · 30 天均值 <b>{base} bpm</b>"
        if hr_day and hr_day["n"]:
            ref += f"<br>日间 {hr_day['lo']}–{hr_day['hi']} bpm（{hr_day['n']} 次采样）"
        val = f"{ctx['rhr']} <span class='u'>bpm</span>"
        if hr_day and hr_day["n"]:
            val += f"<br><span class='u'>日间 {hr_day['lo']}–{hr_day['hi']}</span>"
        add("静息心率", word, cls, val, ref, cls == "warn")
    if spo2:
        sv = spo2["value"]
        # 血氧只需「不低于下限」——低于 spo2_low 才过低，绝不判「过高」
        word, cls = floor_only_word(sv, th.get("spo2_low", 92))
        add("血氧", word, cls, f"{sv}<span class='u'>%</span>",
            f"参考 <b>≥ {th.get('spo2_low', 92)}%</b> · 仅作趋势参考，不作诊断依据"
            + (" · ⚠ 建议就医核实" if cls == "warn" else ""), cls == "warn")
    if activity and activity.get("steps") is not None:
        tgt = g.get("steps_target", 8000)
        word, cls = verdict_word(activity["steps"], tgt, None)
        ref = f"目标 <b>≥ {tgt:,} 步</b>"
        if activity.get("distance_km"):
            ref += _dist_txt(activity)
        # 2026-10-26 加：休息恢复日不做步数考核 —— 与建议区「不必补走」口径统一，
        # 否则同页会出现「步数目标不考核」与「过低 ▲」两个相反结论（10-25 周日实测命中）。
        _wp_ov = cfg.get("workout_plan") or {}
        _rest_ov = set(_wp_ov.get("rest_weekdays") or [])
        if _rest_ov and date.fromisoformat(day).isoweekday() in _rest_ov:
            word, cls = "不考核", "na"
            ref = (f"<b>休息恢复日</b>，步数不作考核项（散步是恢复手段） · 参考目标 ≥ {tgt:,} 步"
                   + _dist_txt(activity))
        add("步数", word, cls, f"{activity['steps']:,} <span class='u'>步</span>", ref, cls == "warn")
    if workouts:
        wtxt = " / ".join(f"{w['sport']} {w['duration_min']:g} 分" for w in workouts)
        tmin = sum(w["duration_min"] or 0 for w in workouts)
        word, cls = verdict_word(tmin, th.get("active_minutes_target", 30), None)
        ref = (f"目标 <b>≥ {th.get('active_minutes_target', 30)} 分钟</b>中高强度"
               f" · 消耗 {sum(w['kcal'] or 0 for w in workouts):,} kcal"
               f"<br>{wtxt}")
        add("运动量", word, cls,
            f"{tmin:g} <span class='u'>分钟</span><br><span class='u'>{wtxt}</span>", ref, cls == "warn")
    else:
        add("运动量", "无记录", "na", "—",
            f"当日无中高强度运动 · 目标 <b>≥ {th.get('active_minutes_target', 30)} 分钟</b>")

    # ── 睡眠（D-37：昨夜 → 今晨那一觉）──
    # no_sleep=True（22:00 当日速览）时整行不出：当日报告查的是"今夜"的睡眠，
    # 结构上必然查不到，出了只会显示一条永远错误的文案。睡眠归 10:10 昨日汇总。
    if ctx.get("no_sleep"):
        pass
    elif sleep and sleep.get("total_min"):
        tgt = g.get("sleep_target_min", 450)
        tmin = sleep["total_min"]
        word, cls = verdict_word(tmin, tgt - 30, None)
        st_t = (sleep.get("start_ts") or "")[11:16]
        en_t = (sleep.get("end_ts") or "")[11:16]
        ref = f"目标 <b>{tgt//60}h{tgt%60:02d}</b>（容差 −30 分）"
        if st_t and en_t:
            ref += f"<br>入睡 <b>{st_t}</b> · 起床 <b>{en_t}</b>"
        if sleep.get("inbed_min"):
            # 2026-10-26：清醒过长时把卧床与实睡分开说，避免把"躺着不动"当成"睡着"
            ref += (f"<br>卧床 <b>{sleep['inbed_min']//60}h{sleep['inbed_min']%60:02d}</b>"
                    f" · 其中清醒 <b>{sleep['awaken_min']} 分</b>")
        if sleep.get("light_min"):
            ref += f" · 浅睡 {sleep['light_min']} 分"
        if sleep.get("rem_min"):
            ref += f" · REM {sleep['rem_min']} 分"
        if sleep.get("awaken_min"):
            pass   # 清醒时长已在上面「卧床 / 其中清醒」里说过，此处不重复
        elif sleep.get("awake_min"):
            ref += f" · 夜醒 {sleep['awake_min']} 分"
        if ctx["week"].get("sleep_days", 0) <= 1:
            ref += "<br>⚠ 仅 1 天样本，记满 3 天才谈趋势"
        if sleep_fragment(sleep, cfg):
            # 2026-10-25 加：疑似片段时不下「过低」结论，只如实标记录不足
            word, cls, flag = "疑似片段", "na", False
            ref += ("<br><b>⚠ 云端只返回这一小段</b>，此前无夜间记录 → "
                    "整夜时长不可判，<b>不作睡眠不足的判定依据</b>")
        else:
            flag = cls == "warn"
        add("睡眠", word, cls, f"{tmin//60}h{tmin%60:02d}", ref, flag)
    else:
        add("睡眠", "数据不足", "na", "—",
            f"该夜无睡眠记录（可能未佩戴 / 未同步） · 目标 <b>{g.get('sleep_target_min', 450)//60}h"
            f"{g.get('sleep_target_min', 450)%60:02d}</b>")

    # ── 能量收支 ──
    if kcal_low:
        floor = ctx["intake_floor"]
        word, cls = verdict_word(kcal_low, floor, None)
        conf = "低" if (kcal_high - kcal_low) > 400 else ("中" if (kcal_high - kcal_low) > 200 else "高")
        ref = f"下限 <b>≥ {floor} kcal</b>（低于此则缺口过大）<br>置信度 <b>{conf}</b> · 照片 / 口述估算，不给点值"
        add("摄入热量", word, cls,
            f"{kcal_low}–{kcal_high} <span class='u'>kcal</span>", ref, cls == "warn")
    if energy and energy.get("total"):
        # 口径A（当日实测）= 判定依据。口径B（BMR×系数）已于 2026-10-16 删除显示
        # —— 用户定「直接删掉，清爽些」（D-42）：既然判定用甲，再并排一个给相反
        # 结论的系数法只会造成混淆。
        add("支出热量", "参考值", "",
            f"约 {energy['total']:,} <span class='u'>kcal</span>",
            f"基础代谢 <b>{energy['bmr']:,}</b> + 活动消耗 <b>{energy['active_kcal']:,}</b>"
            f"（已含运动，不重复计）")
    if gap:
        lo_s, hi_s = ctx["deficit_safe"]
        word, cls = gap_word(gap, lo_s, hi_s)
        ref = f"达标线 <b>{lo_s}–{hi_s} kcal/天</b>（对应周减 0.4–0.8 kg）<br>中位约 <b>{gap['mid']:+d}</b>"
        if cls == "warn" and gap["mid"] > hi_s:
            ref += " · 缺口过大，长期掉肌肉风险"
        elif cls == "warn":
            ref += " · 缺口过小，无实质进展"
        add("热量缺口", word, cls,
            f"{gap['low']:+d} ~ {gap['high']:+d} <span class='u'>kcal</span>", ref, cls == "warn")

    # ── 营养结构 ──
    dq_items, dq_verdict = diet_quality(nutrition, cfg, weight_for_energy)
    if kcal_low:
        words = diet_verdict_words(dq_items)
        bad = [w for w in words if "达标" not in w and "数据不足" not in w]
        cls = "warn" if bad else "ok"
        head = ("、".join(bad) if bad else "各项达标")
        det = []
        for k, v, c2, note in dq_items:
            mk = "✓" if c2 == "ok" else ("✗" if c2 == "warn" else "—")
            det.append(f"{mk} {k} <b>{v}</b>")
        ref = (f"蛋白质参考 <b>{pro_lo_t}–{pro_hi_t} g</b>（1.2–1.6 g/kg，保肌）"
               f" · 蔬菜 <b>≥4 拳</b> · 碳水宜中/低 · 钙 <b>≥800 mg</b><br>"
               f"总评：<b>{dq_verdict}</b>（{len(dq_items)} 项中 "
               f"{sum(1 for _, _, c3, _ in dq_items if c3 == 'ok')}/{len(dq_items)} 达标）")
        add("饮食结构", head, cls, " · ".join(det), ref, cls == "warn")

    # 渲染（化验单样式）
    ov_html = ""
    for item, word, cls, val, ref, flag in ov:
        vcls = {"ok": "ok", "warn": "warn", "na": "na", "": "ok"}.get(cls, "ok")
        rowcls = f' class="row-{vcls}"' if vcls in ("warn", "na") else ""
        word_cls = vcls if cls else "na"
        word_html = f'<span class="vd {word_cls}">{word}</span>'
        flagattr = " flag" if flag else ""
        ov_html += (f'<tr{rowcls}><td class="c-item">{item}</td>'
                    f'<td>{word_html}</td>'
                    f'<td class="c-val"{flagattr}>{val}</td>'
                    f'<td class="c-ref">{ref}</td></tr>')


    # ③ 热量账本
    ledger_html = ""
    if energy and energy.get("total") and gap:
        lo_s, hi_s = ctx["deficit_safe"]
        in_zone = lo_s <= gap["mid"] <= hi_s
        zone_txt = "落在达标区" if in_zone else "偏离达标区"
        ledger_html = f"""
<div class="ledger">
  <div class="lbox"><div class="h">支出（基础代谢 + 活动）</div>
    <div class="big">{energy['total']:,} kcal</div>
    <div class="s">基础代谢 BMR {energy['bmr']:,}<br>
      活动消耗 {energy['active_kcal']:,}<span style="color:#b0b0aa">（含运动与步行，不重复计运动）</span></div>
  </div>
  <div class="lbox"><div class="h">摄入（区间 + 置信度）</div>
    <div class="big">{nutrition['kcal_low']}–{nutrition['kcal_high']} kcal</div>
    <div class="s">蛋白质 {fmt(pro_low, nd=1)}–{fmt(pro_high, nd=1)} g<br>
      蔬菜 {fmt(veg, nd=1)} 拳 · 碳水 {dq_items[2][1] if len(dq_items) > 2 else '—'}<br>
      钙 {nutrition.get('calcium_low') or 0:.0f}–{nutrition.get('calcium_high') or 0:.0f} mg<span style="color:#b0b0aa">（参考 800）</span><br>
      <span style="color:#b0b0aa">照片估算，区间口径，不给点值</span></div>
  </div>
</div>
<div class="gapbox">
  <div class="h" style="font-size:12px;color:#6f6f6a">热量缺口 = 支出 − 摄入</div>
  <div class="big">{gap['low']:+d} ~ {gap['high']:+d} kcal</div>
  <div class="s" style="font-size:12px;color:#6f6f6a;margin-top:6px">
    中位情形约 <b>{gap['mid']:+d} kcal</b> · 达标线 {lo_s}–{hi_s} kcal/天（周减 0.4–0.8 kg）→ <b>{zone_txt}</b><br>
    区间宽度主要来自摄入估算误差（{nutrition['kcal_high']-nutrition['kcal_low']} kcal）→
    <b>单日是否达标不可判定，看 7 天滚动均值</b>。
  </div>
</div>"""

    # ④ 饮食结构
    dq_rows = "".join(
        f'<tr><td>{k}</td><td class="v">{v}{tag(c)}</td><td class="n">{n}</td></tr>'
        for k, v, c, n in dq_items)

    # ⑤ 睡眠（D-37：昨夜 → 今晨那一觉）
    if sleep and sleep.get("total_min"):
        st_t = (sleep.get("start_ts") or "")[11:16]
        en_t = (sleep.get("end_ts") or "")[11:16]
        bed_txt = f"入睡 {st_t} · 起床 {en_t}" if st_t and en_t else ""
        sleep_line = (f"实睡 {sleep['total_min']//60}h{sleep['total_min']%60:02d}"
                      f"（目标 {g.get('sleep_target_min',450)//60}h{g.get('sleep_target_min',450)%60:02d}）")
        if sleep.get("inbed_min"):
            # 2026-10-26：卧床与实睡分开列，否则"躺着 12 小时"会被读成"睡了 12 小时"
            sleep_line += (f" · 卧床 {sleep['inbed_min']//60}h{sleep['inbed_min']%60:02d}"
                           f"（其中清醒 {sleep['awaken_min']} 分）")
        if bed_txt:
            sleep_line += f" · {bed_txt}"
        if sleep.get("deep_min") is not None:
            sleep_line += f" · 深睡 {sleep['deep_min']} 分"
        if sleep.get("rem_min") is not None:
            sleep_line += f" · REM {sleep['rem_min']} 分"
        if sleep.get("awaken_min") is not None:
            pass   # 清醒时长已在上面「卧床（其中清醒 X 分）」里列出，不重复
        elif sleep.get("awake_min") is not None:
            sleep_line += f" · 夜间清醒 {sleep['awake_min']} 分"
        if sleep.get("wake_count") is not None:
            sleep_line += f" · 夜醒 {sleep['wake_count']} 次"
        sleep_line += f" · 评分 {sleep.get('score') or '—'}"
        if sleep_fragment(sleep, cfg):
            sleep_line += ("<br><b style='color:#b45309'>⚠ 疑似片段</b>：云端只返回这一小段，"
                           "此前没有夜间睡眠记录 → 实际整夜时长可能远长于此，"
                           "<b>本项只作记录，不作为「睡眠不足」的判定依据</b>。"
                           "下次出报告前若补上完整夜间段，会以补上的为准。")
    else:
        sleep_line = ("<b>数据不足</b> —— 该夜（昨夜 → 今晨）没有睡眠记录，"
                      "可能是未佩戴设备或数据未同步。本项缺失会影响减重期的饥饿感判断。")

    # ⑥ 今日建议（分运动/饮食/睡眠）
    CAT_ORDER = ["运动", "饮食", "睡眠", "体重", "其他"]
    adv_grouped = []
    for c in CAT_ORDER:
        items = [x for x in advice if x["cat"] == c]
        if items:
            adv_grouped.append((c, items))
    advice_html = ""
    for c, items in adv_grouped:
        advice_html += f'<div class="note" style="margin-top:14px;margin-bottom:4px;font-weight:600;color:#1b1b1a">{c}</div>'
        advice_html += '<ul class="advice">' + "".join(
            f'<li>{t["text"]}</li>' for t in items) + '</ul>'

    # ⑦ 执行率
    if exec_rows:
        rate_txt = (f"可核验 <b>{judge_n}</b> 项，达成 <b>{done_n}</b> 项 → 执行率 <b>{rate:.0f}%</b>"
                    if judge_n else "本批建议暂无可核验项")
        exec_html = f'<div class="rate">{rate_txt}</div><table><thead><tr><th>前一日给出的建议</th>' \
                    f'<th>实际</th><th>判定</th></tr></thead><tbody>'
        for r in exec_rows:
            dot = {"done": "done", "miss": "miss", "na": "na"}[r["status"]]
            verdict = {"done": "达成", "miss": "未达成", "na": "不适用"}[r["status"]]
            exec_html += (f'<tr><td>{r["text"]}</td><td class="n">{r["actual"]}</td>'
                          f'<td><span class="dot {dot}"></span>{verdict}</td></tr>')
        exec_html += "</tbody></table>"
        src_note = f'来源：{prev_entry.get("report_date")} 报告给出的建议（目标日 {day}）'
    else:
        exec_html = ('<p class="note">暂无「目标日 = 本报告日」的历史建议记录。'
                     '本日起会自动写入 data/advice_log.jsonl，明日报告即可看到执行率。</p>')
        src_note = ""

    # ════════════════ 第二层：明细 ════════════════

    def _desc_cell(m) -> str:
        """描述单元格：主体是 description，若有 note 则小字附注（D-34，可回溯）。"""
        d = m["description"] or "—"
        n = m["note"]
        if n:
            d += (f"<div style='font-size:11px;color:#8a8a84;margin-top:3px;"
                  f"line-height:1.45'>备注：{n}</div>")
        return d

    rows_html = "".join(
        f"<tr><td>{m['time']}</td><td>{MEAL_CN.get(m['meal_type'], m['meal_type'])}</td>"
        f"<td>{_desc_cell(m)}</td>"
        f"<td>{PLACE_CN.get(m['place'], m['place'] or '—')}</td>"
        f"<td class='num'>{fmt(m['kcal_low'])}–{fmt(m['kcal_high'])}</td>"
        f"<td class='num'>{fmt(m['protein_g_low'], nd=1)}–{fmt(m['protein_g_high'], nd=1)} g</td>"
        f"<td class='num'>{fmt(m['veg_servings'], nd=1)}</td>"
        f"<td>{CARB_CN.get(m['carb_level'], m['carb_level'] or '—')}</td>"
        f"<td>{CONF_CN.get(m['confidence'], m['confidence'])}</td></tr>"
        for m in meals
    ) or "<tr><td colspan='9' style='color:#6f6f6a'>当日无饮食记录</td></tr>"

    workout_html = "".join(
        f"<tr><td>{w['sport']}</td>"
        f"<td>{(w['start_ts'] or '')[11:16]}–{(w['end_ts'] or '')[11:16]}</td>"
        f"<td class='num'>{fmt(w['duration_min'], nd=1)} 分</td>"
        f"<td class='num'>{fmt(w['distance_km'], nd=2)}</td>"
        f"<td class='num'>{fmt(w['kcal'])}</td>"
        f"<td class='num'>{fmt(w['avg_hr'])} / {fmt(w['max_hr'])}</td></tr>"
        for w in workouts
    ) or "<tr><td colspan='6' style='color:#6f6f6a'>当日无运动记录</td></tr>"

    portion_html = "".join(
        f"<tr><td>{p['name']}</td><td class='num'>{fmt(p['kcal_low'])}–{fmt(p['kcal_high'])}</td>"
        f"<td class='num'>{fmt(p['protein_g'], nd=1)} g</td><td class='num'>{p['times_used']}</td></tr>"
        for p in portions
    ) or "<tr><td colspan='4' style='color:#6f6f6a'>份量库为空</td></tr>"

    banner = ""
    if is_demo:
        banner = ('<div class="banner">⚠️ 当前库中含<b>演示数据</b>（source=demo）。'
                  '本页仅用于预览报表样式，不是真实健康记录。</div>')

    spo2_txt = "—"
    if spo2:
        cls = "warn" if spo2["value"] < th.get("spo2_low", 92) else "ok"
        spo2_txt = f'{spo2["value"]}%{tag(cls, "偏低" if cls == "warn" else "正常")}'
    stress_txt = f"{stress['value']}" if stress else "—"
    hr_txt = "—"
    if hr_day and hr_day["n"]:
        hr_txt = (f'{hr_day["lo"]}–{hr_day["hi"]} bpm'
                  f'<span style="color:#6f6f6a;font-size:12px"> · {hr_day["n"]} 个样本 · 均 {round(hr_day["avg"])}</span>')

    # ── 进食窗口 ──
    win_s, win_e = win.get("start", "12:00"), win.get("end", "20:00")
    if first_meal and last_meal:
        wrows = [
            (f"首餐 ≥ {win_s}", first_meal, "ok" if first_meal >= win_s else "bad"),
            (f"末餐 ≤ {win_e}", last_meal, "ok" if last_meal <= win_e else "bad"),
            ("窗口外进食", "无" if not window_out else "、".join(window_out),
             "ok" if not window_out else "bad"),
        ]
        win_html = ("<table><thead><tr><th>检查项</th><th>实际</th><th>结论</th></tr></thead><tbody>"
                    + "".join(f'<tr><td>{k}</td><td>{v}</td><td>{tag(c, "符合" if c=="ok" else "不符合")}</td></tr>'
                              for k, v, c in wrows)
                    + "</tbody></table>")
        win_html += f'<div class="note">整体：{"完全符合 12:00–20:00 进食窗口。" if window_ok else "存在窗口外进食，需注意。"}</div>'
    else:
        win_html = '<p class="note">当日无饮食记录，无法核对进食窗口。</p>'

    # ── 睡眠分期卡片 ──
    # no_sleep=True（22:00 当日速览）时整卡不出。2026-10-16 用户定：
    # 「晚上 22 点的速览不用管睡眠，早上 10 点的昨日汇总已经有了，专注食物和运动。」
    if no_sleep:
        sleep_card_html = ""
    else:
        sleep_card_html = f"""<div class="card"><h2>睡眠分期（近 {days_detail} 天）</h2>
{sleep_chart(sleep_rows)}
<div class="legend">
<span><i style="background:var(--deep)"></i>深睡</span>
<span><i style="background:var(--light)"></i>浅睡</span>
<span><i style="background:var(--rem)"></i>REM</span>
<span><i style="background:var(--awake)"></i>夜间清醒</span>
</div>
<div class="note">分期算法各厂商有差异，只与自己的历史对比，不跨设备比较。</div></div>"""

    # ── 睡眠情况卡片（2026-10-26 修：no_sleep 时整卡不出，与「睡眠板块已关闭」口径一致）──
    if no_sleep:
        sleep_main_html = ""
    else:
        sleep_main_html = (
            '<div class="card"><h2>睡眠情况</h2>'
            f'<div>{sleep_line}</div>'
            '<div class="note">统计的是<b>报告日当夜</b>的睡眠（昨夜 → 今晨；睡完才有意义，'
            '故完整日报放次日早上出）。库内归属日 = 起床日，取数口径见 D-37。</div></div>')

    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>健康日报 · {day}</title>
<style>{CSS}</style></head>
<body><div class="wrap">
{banner}
<h1>健康日报</h1>
<div class="sub">{day} · 生成于 {datetime.now():%Y-%m-%d %H:%M} · {prof.get('name', '')}</div>

{headline}

<div class="sect gate"><span class="pill">详细指标如下</span> 想细看再往下</div>

<div class="card"><h2>核心指标总览</h2>
<table class="ov"><thead><tr>
  <th>项目</th><th class="c-verdict">结论</th><th>数值</th><th class="c-ref">参考范围 / 说明</th>
</tr></thead><tbody>{ov_html}</tbody></table>
<div class="note">结论列<b>一个词讲明结果</b>：达标 / 过低 / 过高 / 偏大 / 偏快 / 过小 / 过大 / 连续上升……；
<b>▲</b> 标记需留意的项。达标项淡化显示，一眼扫出异常项。</div></div>

<div class="card"><h2>热量账本</h2>
{ledger_html if ledger_html else '<p class="note">数据不足，无法核算（需同时有体重、活动消耗与饮食记录）。</p>'}</div>

<div class="card"><h2>饮食结构是否合理</h2>
<table><thead><tr><th>项目</th><th>值</th><th>说明</th></tr></thead><tbody>{dq_rows}</tbody></table>
<div class="note"><b>总评：{dq_verdict}</b>。"纤维素"用蔬菜拳数代口径——照片判不出膳食纤维，本系统不录入该字段。</div></div>

{sleep_main_html}

<div class="card"><h2>今日建议</h2>
{advice_html}
<div class="note">规则版建议（基于阈值 + 减重安全口径）。分运动 / 饮食 / 睡眠三类，每条尽量落到具体动作。</div></div>

<div class="card"><h2>昨日建议执行率（T+1 对比）</h2>
{exec_html}
<div class="note">{src_note}　核验口径：建议自带可核验指标（步数 / 运动分钟 / 蔬菜拳数 / 进食窗口 / 睡眠 / 腰围周期），按当日实际数据自动判定。</div></div>

<div class="sect">以下是明细 · 想细看再往下</div>

<div class="card"><h2>餐食明细</h2>
<table><thead><tr><th>时间</th><th>餐次</th><th>内容</th><th>地点</th>
<th style="text-align:right">热量区间</th><th style="text-align:right">蛋白质</th>
<th style="text-align:right">蔬菜(拳)</th><th>碳水</th><th>置信度</th></tr></thead>
<tbody>{rows_html}</tbody></table>
<div class="note">合计：热量约 <b>{fmt(kcal_low)}–{fmt(kcal_high)} kcal</b> ·
蛋白质约 <b>{fmt(pro_low, nd=1)}–{fmt(pro_high, nd=1)} g</b> · 蔬菜约 <b>{fmt(veg, nd=1)} 拳</b>。
蔬菜份数以「一拳」为一份——营养学里「一拳头」本身就是份量单位。</div></div>

<div class="card"><h2>进食窗口核对（{win_s}–{win_e}）</h2>{win_html}</div>

<div class="card"><h2>当日摄入（热量区间）</h2>
{kcal_bar(kcal_low, kcal_high, kcal_target)}
<div class="note">热量以<b>区间 + 置信度</b>呈现，不给点值——照片估算日均误差可达数百 kcal。
目标线 {kcal_target} kcal 来自 config（goals.calorie_target_kcal）。</div></div>

<div class="card"><h2>运动明细</h2>
<table><thead><tr><th>类型</th><th>时间段</th><th style="text-align:right">时长</th>
<th style="text-align:right">距离(km)</th><th style="text-align:right">消耗(kcal)</th>
<th style="text-align:right">心率 平均/最大</th></tr></thead>
<tbody>{workout_html}</tbody></table></div>

<div class="card"><h2>体重趋势（近 {days_trend} 天）</h2>
{weight_chart(body_rows, g.get('weight_kg'))}
<div class="note">只看趋势，不看单日绝对值。测量条件需固定：晨起、排尿后、空腹、同一台秤。</div></div>

{sleep_card_html}

<div class="card"><h2>恢复与体征</h2>
<table><tbody>
<tr><td style="color:#6f6f6a">静息心率</td><td>{fmt(ctx['rhr'], ' bpm')}</td>
<td style="color:#6f6f6a">30 天均值</td><td>{fmt(ctx['rhr_base'], ' bpm')}</td></tr>
<tr><td style="color:#6f6f6a">日间心率</td><td colspan="3">{hr_txt}</td></tr>
<tr><td style="color:#6f6f6a">血氧</td><td>{spo2_txt}</td>
<td style="color:#6f6f6a">压力指数</td><td>{stress_txt}</td></tr>
</tbody></table>
<div class="note">异常值只做标记 + 建议就医，系统不做任何解读。</div></div>

<div class="card"><h2>个人份量库（越用越准）</h2>
<table><thead><tr><th>名称</th><th style="text-align:right">热量区间</th>
<th style="text-align:right">蛋白质</th><th style="text-align:right">使用次数</th></tr></thead>
<tbody>{portion_html}</tbody></table></div>

<div class="card"><h2>数据来源与局限</h2>
<ul style="margin:0;padding-left:20px;color:#6f6f6a;font-size:13px">
<li>数据源：本机 <code>data/health.db</code>；活动 / 心率 / 压力 source = <code>mi_fitness</code>；体重 source = <code>scale_mcp</code>。</li>
<li><b>设备来源不可判</b>：MCP 解析丢弃了 <code>sid</code>，分不出某天数据是手机还是手环上报。手环 2026-10-10 晚才配对。</li>
<li><b>营养为估算</b>：照片 / 口述口径，区间 + 置信度，误差可达数百 kcal，仅用于「看见吃了什么、判断饮食结构」，不用于精确热量核算。</li>
<li><b>支出为估算</b>：基础代谢用 Mifflin-St Jeor 公式，活动消耗取自设备汇总，本身也有误差。</li>
<li>本报告只做生活方式层面的记录与建议，<b>不构成医疗建议</b>；不下疾病诊断、不给药物或补剂建议；异常心率 / 血氧只标记并建议就医。</li>
</ul></div>

<footer>
本报告为<b>生活方式管理参考，不构成医疗建议</b>。身体不适请就医。<br>
数据源：data/health.db　|　建议台账：data/advice_log.jsonl　|　项目根：D:\\健康
</footer>
</div></body></html>"""


MEAL_CN = {"breakfast": "早餐", "lunch": "午餐", "dinner": "晚餐", "snack": "加餐"}
PLACE_CN = {"home": "家里", "eatout": "外食", "takeout": "外卖"}
CONF_CN = {"low": "低", "mid": "中", "high": "高"}
CARB_CN = {"low": "低", "mid": "中", "high": "高"}


def weight_delta(rows):
    vals = [r["weight_kg"] for r in rows if r["weight_kg"] is not None]
    if len(vals) < 2:
        return None
    return vals[-1] - vals[0]


def main() -> int:
    ap = argparse.ArgumentParser(description="生成 HTML 健康日报")
    ap.add_argument("--date", help="日期 YYYY-MM-DD，默认取库中最新一天")
    ap.add_argument("--open", action="store_true", help="生成后自动打开")
    # ── 睡眠板块开关（2026-10-16 用户定）──
    # 22:00「当日速览」不要睡眠：当日报告在结构上就不可能查到"今夜"的睡眠
    # （D-37 口径：报告日 D 查的是 D 夜→D+1 晨，归属日 = D+1，当天根本不存在），
    # 那一栏只会永远显示"昨夜没有睡眠记录"——一条必然错误的文案。
    # 睡眠归 10:10「昨日汇总」管，那里 D+1 已过去，数据是实的。
    #
    # 默认行为：--date 为今天（或未指定而取到最新一天=今天）→ 自动关闭睡眠；
    #           传 --sleep 可强制打开；传 --no-sleep 可对任意日期强制关闭。
    ap.add_argument("--no-sleep", dest="no_sleep", action="store_true",
                    help="不出睡眠板块（总览行 / 三行判定 / 建议 / 分期图全去掉）")
    ap.add_argument("--sleep", dest="sleep_on", action="store_true",
                    help="强制显示睡眠板块（覆盖「当日自动关闭」的默认行为）")
    args = ap.parse_args()

    cfg = load_cfg()
    con = connect()

    if args.date:
        day = args.date
    else:
        r = con.execute("SELECT MAX(d) AS d FROM ("
                        "SELECT MAX(date) d FROM body_metric UNION ALL "
                        "SELECT MAX(date) d FROM sleep_session UNION ALL "
                        "SELECT MAX(date) d FROM activity_daily)").fetchone()
        day = r["d"] or date.today().isoformat()

    no_sleep = args.no_sleep
    if not no_sleep and not args.sleep_on and day == date.today().isoformat():
        no_sleep = True   # 当日速览：结构上无睡眠可查，不出这一项

    html = render(con, cfg, day, no_sleep=no_sleep)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORT_DIR / f"report-{day}.html"
    out.write_text(html, encoding="utf-8")
    con.close()

    print(f"✅ 报告已生成：{out}")
    print(f"   大小：{out.stat().st_size / 1024:.1f} KB")
    if no_sleep:
        print("   睡眠板块：已关闭（当日速览口径，睡眠归次日 10:10 昨日汇总）")
    else:
        print("   睡眠板块：已开启")
    if args.open:
        import webbrowser
        webbrowser.open(out.as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
