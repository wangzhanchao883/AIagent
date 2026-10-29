#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 src/ 构建成两个宿主的产物，并打出 Release 用的 zip。

    python build.py                 # 构建到 dist/ 并打两个 zip
    python build.py --ver 1.5.0
    python build.py --no-zip        # 只构建目录，不打 zip

产物（dist/ 已被 .gitignore 挡住，不进仓库）：
    dist/doubao/health-data-steward/                  ← 豆包：上传这个目录当技能
    dist/workbuddy/health-data-steward-<ver>/         ← WorkBuddy：导入这个目录当专家包
    dist/health-data-steward-doubao.zip               ← 固定文件名，挂到 Release
    dist/health-data-steward-workbuddy.zip

设计原则：
  1. src/ 是唯一事实源。产物一律生成，不手改、不提交。
  2. zip 文件名固定不带版本号，这样 Release 的
     /releases/latest/download/<文件名> 直链永久有效。
  3. 两份 SKILL.md 必须逐字节一致，构建时强制校验。
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
SKILL_SRC = SRC / "skill"
WB_SRC = SRC / "workbuddy"
DIST = ROOT / "dist"

SKILL_NAME = "health-data-steward"
SKILL_LINE_WARN = 500          # Agent Skills 规范建议上限
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store", "Thumbs.db",
                                "data", "config", "reports")

ZIP_DOUBAO = "health-data-steward-doubao.zip"
ZIP_WORKBUDDY = "health-data-steward-workbuddy.zip"


def die(msg: str) -> None:
    print(f"FAIL:{msg}")
    sys.exit(1)


def check_src() -> None:
    for p in (SKILL_SRC / "SKILL.md", SKILL_SRC / "scripts", SKILL_SRC / "references",
              WB_SRC / "plugin.json", WB_SRC / "persona.md", WB_SRC / "expert.png"):
        if not p.exists():
            die(f"missing {p.relative_to(ROOT)}")


def frontmatter(path: Path) -> dict:
    """极简 frontmatter 读取（只取顶层标量，够用即可，不引入 PyYAML）。"""
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        die(f"{path.name} 缺 frontmatter")
    out = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.startswith((" ", "\t")):
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def make_zip(src_dir: Path, zip_path: Path) -> int:
    """打包 src_dir，zip 内顶层就是 src_dir 的目录名（解压不散开）。"""
    base = src_dir.parent
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(src_dir.rglob("*")):
            if p.is_file():
                z.write(p, p.relative_to(base).as_posix())
    return zip_path.stat().st_size


def human(n: int) -> str:
    return f"{n / 1024:.0f} KB" if n < 1024 * 1024 else f"{n / 1048576:.1f} MB"


def build(ver: str, want_zip: bool) -> None:
    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir(parents=True)

    # ---- 豆包：技能目录本体 ----
    doubao = DIST / "doubao" / SKILL_NAME
    doubao.parent.mkdir(parents=True)
    shutil.copytree(SKILL_SRC, doubao, ignore=IGNORE)

    # ---- WorkBuddy：专家包外壳 + 同一份技能 ----
    wb = DIST / "workbuddy" / f"{SKILL_NAME}-{ver}"
    (wb / "agents").mkdir(parents=True)
    (wb / "avatars").mkdir()
    (wb / ".codebuddy-plugin").mkdir()

    plugin = json.loads((WB_SRC / "plugin.json").read_text(encoding="utf-8"))
    plugin["version"] = ver
    (wb / ".codebuddy-plugin" / "plugin.json").write_text(
        json.dumps(plugin, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    shutil.copy2(WB_SRC / "persona.md", wb / "agents" / f"{SKILL_NAME}.md")
    shutil.copy2(WB_SRC / "expert.png", wb / "avatars" / "expert.png")

    (wb / "skills").mkdir()
    shutil.copytree(SKILL_SRC, wb / "skills" / SKILL_NAME, ignore=IGNORE)

    # ---- 自检 ----
    fm = frontmatter(doubao / "SKILL.md")
    if fm.get("name") != SKILL_NAME:
        die(f"SKILL.md frontmatter name 应为 {SKILL_NAME}，实际 {fm.get('name')!r}")
    if not fm.get("description"):
        die("SKILL.md frontmatter 缺 description")

    skill_lines = len((doubao / "SKILL.md").read_text(encoding="utf-8").splitlines())
    n_refs = len(list((doubao / "references").glob("*.md")))
    n_scripts = len(list((doubao / "scripts").glob("*.py")))

    # 两份 skill 必须逐字节一致，否则说明有人手改了产物
    a = (doubao / "SKILL.md").read_bytes()
    b = (wb / "skills" / SKILL_NAME / "SKILL.md").read_bytes()
    if a != b:
        die("两份 SKILL.md 不一致（产物被手改过？）")

    # ---- 打 Release 附件 ----
    sizes = {}
    if want_zip:
        sizes[ZIP_DOUBAO] = make_zip(doubao, DIST / ZIP_DOUBAO)
        sizes[ZIP_WORKBUDDY] = make_zip(wb, DIST / ZIP_WORKBUDDY)

    print(f"OK:build:ver={ver}")
    print(f"  SKILL.md      {skill_lines} 行"
          f"{'  ⚠️ 超过 ' + str(SKILL_LINE_WARN) + ' 行建议上限' if skill_lines > SKILL_LINE_WARN else ''}")
    print(f"  references    {n_refs} 篇")
    print(f"  scripts       {n_scripts} 个")
    print(f"  豆包目录   → {doubao.relative_to(ROOT)}")
    print(f"  WorkBuddy  → {wb.relative_to(ROOT)}")
    for name, size in sizes.items():
        print(f"  zip 附件   → dist/{name}  ({human(size)})")

    print("\n下一步：")
    print("  本地装  : 豆包上传 dist/doubao/ 下的目录；WorkBuddy 导入 dist/workbuddy/ 下的目录")
    if want_zip:
        print("  发 Release：")
        print(f'    gh release create {SKILL_NAME}-v{ver} \\')
        print(f'      --title "健康数据管家 {ver}" \\')
        print(f'      --notes-file 发布说明-{ver}.md \\')
        print(f'      dist/{ZIP_WORKBUDDY} dist/{ZIP_DOUBAO}')


def main() -> None:
    ap = argparse.ArgumentParser(description="构建健康数据管家双宿主产物与发布包")
    ap.add_argument("--ver", default="1.5.0", help="版本号，写进 plugin.json 与目录名（默认 1.5.0）")
    ap.add_argument("--no-zip", action="store_true", help="只构建目录，不生成 zip")
    a = ap.parse_args()
    check_src()
    build(a.ver, not a.no_zip)


if __name__ == "__main__":
    main()
