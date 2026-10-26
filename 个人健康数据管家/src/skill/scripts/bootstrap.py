#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""首次使用 · 一键配置运行环境。

跑完这个脚本，专家就能正常干活了。它会：

  1. 确定项目根目录（默认 ~/health-data；拒绝云同步目录）
  2. 选定 Python 解释器并记下来（后续所有调用复用它，不再探测）
  3. 检查 / 安装唯一依赖 PyYAML
  4. 建目录骨架
  5. 从模板生成 config/config.yaml（填入你的身高、年龄、目标）
  6. 初始化 SQLite 库（建表，幂等）
  7. 写 .setup_done.json —— 专家靠它判断「这台机器配好了」

幂等：重复跑安全。已存在的 config.yaml 不会被覆盖（除非 --force）。

用法：
    python bootstrap.py                                  # 交互式，按提示填
    python bootstrap.py --root ~/health-data \
        --height 175 --age 45 --sex male --unit kg --target-weight 72 --no-input
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL_DIR = HERE.parent
TEMPLATE = SKILL_DIR / "templates" / "config.example.yaml"
INIT_DB = HERE / "init_db.py"
MARKER = ".setup_done.json"
RUNTIME = ".runtime.json"

DIRS = [
    "data", "data/raw", "data/raw/mi_fitness", "data/meals", "data/metrics",
    "config", "reports", "logs", "photos/inbox", "photos/archive", "backups",
]

# 云同步目录：数据放这里 = 本地数据变成云端数据
CLOUD_MARKERS = (
    "onedrive", "dropbox", "icloud", "baidunetdisk", "baidu", "坚果云",
    "nutstore", "googledrive", "google drive", "wpscloud", "weiyun",
    "sync", "同步", "微云", "夸克", "阿里云盘",
)


def fail(msg: str, code: int = 1) -> "NoReturn":
    print(f"\n✗ {msg}", file=sys.stderr)
    raise SystemExit(code)


def ok(msg: str) -> None:
    print(f"✓ {msg}")


def warn(msg: str) -> None:
    print(f"⚠ {msg}")


def resolve_root(raw: str) -> Path:
    p = Path(raw).expanduser()
    try:
        p = p.resolve()
    except OSError:
        p = p.absolute()
    low = str(p).lower()
    hit = [m for m in CLOUD_MARKERS if m in low]
    if hit:
        fail("项目目录落在云同步路径里（命中：%s）。\n"
             "  数据必须放本地磁盘，换个目录重跑，例如 --root ~/health-data" % "、".join(hit))
    return p


def step_python(root: Path) -> Path:
    exe = Path(sys.executable).resolve()
    ver = "%d.%d.%d" % sys.version_info[:3]
    if sys.version_info < (3, 9):
        fail(f"Python 版本过低（{ver}），需要 3.9 及以上。先升级 Python 再跑一次。")
    ok(f"Python {ver} → {exe}")
    (root / RUNTIME).write_text(
        json.dumps({"python": str(exe), "version": ver,
                    "recorded_at": datetime.now().isoformat(timespec="seconds")},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    return exe


def step_deps(exe: Path) -> None:
    try:
        import yaml  # noqa: F401
        ok("依赖 PyYAML 已就绪")
        return
    except ImportError:
        pass
    warn("缺 PyYAML，正在安装（只需一次）…")
    cmds = [
        [str(exe), "-m", "pip", "install", "--quiet", "pyyaml"],
        [str(exe), "-m", "pip", "install", "--quiet", "-i",
         "https://pypi.tuna.tsinghua.edu.cn/simple", "pyyaml"],
    ]
    for cmd in cmds:
        r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
        if r.returncode == 0:
            ok("PyYAML 安装完成")
            return
    fail("PyYAML 装不上。手动跑一次：\n"
         f"  {exe} -m pip install pyyaml -i https://pypi.tuna.tsinghua.edu.cn/simple\n"
         "（其余功能都只依赖标准库，只差这一个包）")


def step_dirs(root: Path) -> None:
    for d in DIRS:
        (root / d).mkdir(parents=True, exist_ok=True)
    # 写权限实测
    probe = root / "data" / ".write_test"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as e:
        fail(f"目录不可写：{root / 'data'}（{e}）")
    ok(f"目录骨架就绪（{len(DIRS)} 个）")


def ask(prompt: str, default: str = "") -> str:
    tip = f"{prompt}" + (f"（默认 {default}）" if default else "") + "："
    try:
        v = input(tip).strip()
    except EOFError:
        return default
    return v or default


def step_config(root: Path, args) -> Path:
    cfg_path = root / "config" / "config.yaml"
    if cfg_path.exists() and not args.force:
        ok(f"config.yaml 已存在，跳过（要重写加 --force）：{cfg_path}")
        return cfg_path
    if not TEMPLATE.exists():
        fail(f"找不到配置模板：{TEMPLATE}（安装包可能不完整，重新解压一次）")

    height = args.height
    age = args.age
    sex = args.sex
    unit = args.unit
    target = args.target_weight
    fist = args.fist_width
    name = args.name

    if not args.no_input:
        print("\n── 填几个数，用来算基础代谢和热量缺口（直接回车 = 用默认值）──")
        if not name:
            name = ask("你的名字（显示在报告上）", "")
        if not height:
            try:
                height = float(ask("身高 cm", "172"))
            except ValueError:
                height = 172.0
        if not age:
            try:
                age = int(ask("年龄", "40"))
            except ValueError:
                age = 40
        if not sex:
            sex = "female" if ask("性别（male/female）", "male").lower().startswith("f") else "male"
        if not unit:
            unit = "kg" if ask("体重输入单位（kg/jin，jin=斤）", "jin").lower().startswith("k") else "jin"
        if not target:
            try:
                target = float(ask("目标体重（输入单位同上）", ""))
            except ValueError:
                target = None
        if not fist:
            try:
                fist = float(ask("拳宽 cm（量拳头最宽处，用于份量参照，可跳过）", "9"))
            except ValueError:
                fist = 9.0

    text = TEMPLATE.read_text(encoding="utf-8")
    repl = {}
    if name:
        repl['name: "你的名字"'] = f'name: "{name}"'
    if sex:
        repl["sex: male"] = f"sex: {sex}"
    if age:
        repl["birth_year: 1985"] = f"birth_year: {datetime.now().year - int(age)}"
    if height:
        repl["height_cm: 172"] = f"height_cm: {float(height):g}"
    if unit:
        repl["weight_unit: jin"] = f"weight_unit: {unit}"
    if fist:
        repl["fist_width_cm: 9.0"] = f"fist_width_cm: {float(fist):g}"
    if target:
        # --target-weight 的输入单位与 weight_unit 一致，但库里一律存 kg，这里必须换算
        u = unit or "jin"            # 模板默认 weight_unit: jin
        t_kg = float(target) * 0.5 if u == "jin" else float(target)
        repl["weight_kg: 70.0"] = f"weight_kg: {t_kg:g}"
        repl["goal_kg: 70.0"] = f"goal_kg: {t_kg:g}"
    elif height:
        # 没给目标体重时，按 BMI 22.5 反推一个起点（kg）
        t = round(22.5 * (float(height) / 100) ** 2, 1)
        repl["weight_kg: 70.0"] = f"weight_kg: {t:g}"
        repl["goal_kg: 70.0"] = f"goal_kg: {t:g}"

    missing = []
    for k, v in repl.items():
        if k in text:
            text = text.replace(k, v, 1)
        else:
            missing.append(k)
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(text, encoding="utf-8")
    ok(f"已生成 {cfg_path}")
    if missing:
        warn(f"模板里有 {len(missing)} 处占位值没替换上（不影响运行，可手动改）：{missing[:3]}")
    if not target:
        warn("没填目标体重 → 已按 BMI 22.5 给了一个起点，记得在 config 里改成你真正的目标")
    return cfg_path


def step_db(root: Path, exe: Path) -> None:
    if not INIT_DB.exists():
        fail(f"找不到建库脚本 {INIT_DB}（安装包不完整，重新解压一次）")
    env = dict(os.environ, HEALTH_ROOT=str(root))
    r = subprocess.run([str(exe), str(INIT_DB), "--root", str(root)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env)
    out = (r.stdout or "").strip()
    err = (r.stderr or "").strip()
    if r.returncode != 0:
        fail(f"建库失败：\n{err or out}")
    loc = ""
    for ln in out.splitlines():
        if "位置" in ln:
            loc = ln.split("：", 1)[-1].strip()
    ok("数据库就绪" + (f" — {loc}" if loc else ""))


def step_marker(root: Path, exe: Path, cfg: Path) -> None:
    marker = {
        "version": os.environ.get("HEALTH_PACKAGE_VERSION", "unknown"),
        "root": str(root),
        "python": str(exe),
        "config": str(cfg),
        "db": str(root / "data" / "health.db"),
        "setup_at": datetime.now().isoformat(timespec="seconds"),
    }
    (root / MARKER).write_text(json.dumps(marker, ensure_ascii=False, indent=2),
                               encoding="utf-8")
    ok(f"配置标记已写入 {root / MARKER}")


def main() -> int:
    ap = argparse.ArgumentParser(description="健康数据管家 · 一键配置环境")
    ap.add_argument("--root", default=str(Path.home() / "health-data"),
                    help="项目根目录（默认 ~/health-data，拒绝云同步目录）")
    ap.add_argument("--height", type=float, help="身高 cm")
    ap.add_argument("--age", type=int, help="年龄")
    ap.add_argument("--sex", choices=["male", "female"])
    ap.add_argument("--unit", choices=["kg", "jin"], help="体重输入单位")
    ap.add_argument("--target-weight", type=float, help="目标体重（与 --unit 同单位）")
    ap.add_argument("--fist-width", type=float, help="拳宽 cm")
    ap.add_argument("--name", default="", help="署名（显示在报告上）")
    ap.add_argument("--no-input", action="store_true", help="不交互，缺的参数用默认值")
    ap.add_argument("--force", action="store_true", help="已存在 config.yaml 也覆盖")
    args = ap.parse_args()

    print("═" * 56)
    print(" 健康数据管家 · 首次配置")
    print("═" * 56)

    root = resolve_root(args.root)
    print(f"项目目录：{root}\n")
    root.mkdir(parents=True, exist_ok=True)

    exe = step_python(root)
    step_deps(exe)
    step_dirs(root)
    cfg = step_config(root, args)
    step_db(root, exe)
    step_marker(root, exe, cfg)

    print("\n" + "═" * 56)
    print(" 配置完成。下一步：")
    print(f"   1. 自检：{exe} {HERE / 'selfcheck.py'} --root \"{root}\"")
    print("   2. 回到对话，发一张体重秤照片，或说「今早 75.4」（单位按你设的体重单位）")
    print(f"   3. 报告会输出到：{root / 'reports'}")
    print("═" * 56)
    print(" 隐私：数据只在本机 %s，不上传任何服务器。" % root)
    print("       项目目录别放云同步盘；config.yaml 别提交到公开仓库。")
    print("═" * 56)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
