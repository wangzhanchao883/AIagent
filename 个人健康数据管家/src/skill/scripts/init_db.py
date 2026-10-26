#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
建库建表（幂等）。可重复执行，不破坏已有数据。

用法：
    python scripts/init_db.py                 # 建库/升级 schema
    python scripts/init_db.py --reset         # 清空重建（先自动备份旧库）
    python scripts/init_db.py --status        # 只看当前库状态
"""
import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
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
DB_PATH = ROOT / "data" / "health.db"
SCHEMA_VERSION = "1.0.0"

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- 元数据
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- 一、活动日汇总
CREATE TABLE IF NOT EXISTS activity_daily (
    date           TEXT PRIMARY KEY,
    steps          INTEGER,
    distance_km    REAL,
    active_kcal    INTEGER,
    active_minutes INTEGER,
    stand_hours    INTEGER,
    source         TEXT,
    fetched_at     TEXT
);

-- 二、心率样本
CREATE TABLE IF NOT EXISTS heart_rate (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       TEXT NOT NULL,
    bpm      INTEGER,
    hr_type  TEXT,
    source   TEXT,
    UNIQUE (ts, hr_type, source)
);

-- 三、睡眠会话
CREATE TABLE IF NOT EXISTS sleep_session (
    date        TEXT PRIMARY KEY,
    start_ts    TEXT,
    end_ts      TEXT,
    total_min   INTEGER,
    deep_min    INTEGER,
    light_min   INTEGER,
    rem_min     INTEGER,
    awake_min   INTEGER,
    wake_count  INTEGER,
    score       INTEGER,
    source      TEXT,
    fetched_at  TEXT
);

-- 四、血氧 / 压力
CREATE TABLE IF NOT EXISTS spo2 (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT NOT NULL,
    value   INTEGER,
    ctx     TEXT,
    source  TEXT,
    UNIQUE (ts, ctx, source)
);

CREATE TABLE IF NOT EXISTS stress (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT NOT NULL,
    value   INTEGER,
    source  TEXT,
    UNIQUE (ts, source)
);

-- 五、运动记录
CREATE TABLE IF NOT EXISTS workout (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ext_id       TEXT UNIQUE,
    start_ts     TEXT,
    end_ts       TEXT,
    sport        TEXT,
    duration_min REAL,
    distance_km  REAL,
    kcal         INTEGER,
    avg_hr       INTEGER,
    max_hr       INTEGER,
    source       TEXT
);

-- 六、身体形态
CREATE TABLE IF NOT EXISTS body_metric (
    date          TEXT PRIMARY KEY,
    weight_kg     REAL,
    bmi           REAL,
    body_fat_pct  REAL,
    muscle_mass_kg REAL,
    source        TEXT,
    photo_path    TEXT,
    note          TEXT,
    fetched_at    TEXT
);

CREATE TABLE IF NOT EXISTS waist (
    date       TEXT PRIMARY KEY,
    waist_cm   REAL,
    note       TEXT,
    source     TEXT,
    photo_path TEXT
);

-- 七、饮食
CREATE TABLE IF NOT EXISTS meal (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    date             TEXT,
    time             TEXT,
    meal_type        TEXT,
    photo_path       TEXT,
    description      TEXT,
    place            TEXT,
    kcal_low         INTEGER,
    kcal_high        INTEGER,
    protein_g_low    REAL,
    protein_g_high   REAL,
    veg_servings     REAL,
    carb_level       TEXT,
    confidence       TEXT,
    verified_by_user INTEGER DEFAULT 0,
    note             TEXT,
    created_at       TEXT,
    source           TEXT,
    calcium_mg_low   REAL,
    calcium_mg_high  REAL
);
CREATE INDEX IF NOT EXISTS idx_meal_date ON meal (date);

-- 八、个人份量库（越用越准的关键）
CREATE TABLE IF NOT EXISTS portion_library (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT UNIQUE,
    unit        TEXT,
    kcal_low    INTEGER,
    kcal_high   INTEGER,
    protein_g   REAL,
    times_used  INTEGER DEFAULT 0,
    last_used   TEXT,
    confirmed   INTEGER DEFAULT 0,
    note        TEXT,
    source      TEXT
);

-- 九、每日复盘与反馈（闭环第 4 环）
CREATE TABLE IF NOT EXISTS daily_review (
    date                 TEXT PRIMARY KEY,
    energy               INTEGER,
    hunger               INTEGER,
    water_ml             INTEGER,
    advice_text          TEXT,
    advice_followed      TEXT,
    advice_feedback_note TEXT,
    created_at           TEXT,
    source               TEXT
);

-- 视图：近 30 天形态趋势（机器读数据源，报表只消费视图）
DROP VIEW IF EXISTS v_body_trend;
CREATE VIEW v_body_trend AS
SELECT m.date,
       m.weight_kg,
       m.bmi,
       m.body_fat_pct,
       m.muscle_mass_kg,
       w.waist_cm,
       m.source,
       m.photo_path,
       m.note
FROM body_metric m
LEFT JOIN waist w ON w.date = m.date;
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


# 幂等列补齐：旧库缺列时自动 ALTER TABLE 补上，不必重建库。
# 只做「加列」这类非破坏性变更；改类型/删列请走 --reset。
REQUIRED_COLUMNS = {
    "activity_daily": {"source": "TEXT"},
    "heart_rate": {"source": "TEXT"},
    "sleep_session": {"source": "TEXT"},
    "spo2": {"source": "TEXT"},
    "stress": {"source": "TEXT"},
    "workout": {"source": "TEXT"},
    "body_metric": {"source": "TEXT", "photo_path": "TEXT", "note": "TEXT"},
    "waist": {"source": "TEXT", "photo_path": "TEXT"},
    "meal": {"source": "TEXT", "note": "TEXT",
             "calcium_mg_low": "REAL", "calcium_mg_high": "REAL"},
    "portion_library": {"source": "TEXT"},
    "daily_review": {"source": "TEXT"},
}


def ensure_columns(con: sqlite3.Connection) -> list[str]:
    """检测并补齐缺失的列，返回本次新增的列清单（幂等）。"""
    added: list[str] = []
    for table, cols in REQUIRED_COLUMNS.items():
        exists = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        if not exists:  # 表还不存在，交给 SCHEMA 建
            continue
        for col, typ in cols.items():
            if col not in exists:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
                added.append(f"{table}.{col}")
    if added:
        con.commit()
    return added


def create_schema(con: sqlite3.Connection) -> list[str]:
    con.executescript(SCHEMA)
    added = ensure_columns(con)
    con.execute(
        "INSERT INTO meta (key, value) VALUES ('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (SCHEMA_VERSION,),
    )
    con.execute(
        "INSERT INTO meta (key, value) VALUES ('created_at', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (datetime.now().isoformat(timespec="seconds"),),
    )
    con.commit()
    return added


def backup_old() -> Path | None:
    if not DB_PATH.exists():
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = DB_PATH.with_name(f"health.backup-{stamp}.db")
    shutil.copy2(DB_PATH, dst)
    return dst


def show_status(con: sqlite3.Connection) -> None:
    print(f"库文件：{DB_PATH}")
    print(f"大小：  {DB_PATH.stat().st_size / 1024:.1f} KB")
    tabs = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    print(f"\n共 {len(tabs)} 张表：")
    for t in tabs:
        n = con.execute(f"SELECT COUNT(*) FROM {t['name']}").fetchone()[0]
        print(f"  {t['name']:<22} {n:>6} 行")
    ver = con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    print(f"\nschema 版本：{ver[0] if ver else '未知'}")


def main() -> int:
    ap = argparse.ArgumentParser(description="健康数据中枢——建库")
    ap.add_argument("--reset", action="store_true", help="清空重建（自动备份旧库）")
    ap.add_argument("--status", action="store_true", help="只显示库状态")
    args = ap.parse_args()

    if args.status:
        if not DB_PATH.exists():
            print("库不存在，请先运行 python scripts/init_db.py")
            return 1
        con = connect()
        show_status(con)
        con.close()
        return 0

    if args.reset:
        bk = backup_old()
        if bk:
            print(f"已备份旧库 -> {bk.name}")
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(DB_PATH) + suffix)
            if p.exists():
                p.unlink()

    con = connect()
    added = create_schema(con)
    print(f"✅ schema 就绪（v{SCHEMA_VERSION}）")
    print(f"   位置：{DB_PATH}")
    if added:
        print(f"   本次补列：{', '.join(added)}")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
