#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""安装 mi-fitness-mcp（小米运动健康云端取数服务，可选增强）。

**市场版**：源码不在专家包里，运行时用 git 从上游仓库拉取；直连不通自动换代理镜像。

用法：
    python install_band_mcp.py --root <PROJECT_ROOT>
    python install_band_mcp.py --root <PROJECT_ROOT> --dry-run           # 只报告计划
    python install_band_mcp.py --root <PROJECT_ROOT> --src-url <镜像URL>  # 指定源
    python install_band_mcp.py --root <PROJECT_ROOT> --from-path <本地源码目录>
    python install_band_mcp.py --root <PROJECT_ROOT> --force             # 重装
    python install_band_mcp.py --root <PROJECT_ROOT> --no-extras         # 不装 Web 扫码仪表盘

做什么（5 步）：
    1. 取源码：git clone 上游仓库（直连 → 代理镜像逐个试）；已有源码则跳过
    2. 探测本机 Python ≥ 3.11
    3. 源码落在 <PROJECT_ROOT>/vendor/mi-fitness-mcp-cn/
    4. 建虚拟环境 .venv 并 pip install -e '.[all]'（默认含扫码登录 Web 仪表盘）
    5. 验证 CLI，打印 mcp_exe 路径、预期源库路径与 config.yaml 建议值

环境要求（部署前必须满足）：
    - git（若没有，脚本会给出「浏览器下载 ZIP 手动放好」的兜底路径）
    - Python ≥ 3.11
    - 能访问 GitHub 或其镜像；pip 依赖需要访问 PyPI（国内慢可用 --index-url 换清华源）

不做什么（边界）：
    - 不碰登录：扫码授权必须用户本人用小米运动健康 App 操作
    - 不写 config.yaml：建议值只打印，由专家与用户确认后填写
    - 不覆盖已有安装：目标目录已有完整源码 + .venv 时默认跳过，--force 才重建

第三方声明：mi-fitness-mcp-cn 非小米官方（MIT，逆向云端接口），接口可能因官方改版失效；
只读取用户本人账号数据，凭据存系统 keyring 不落明文。
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

TARGET_NAME = "mi-fitness-mcp-cn"
UPSTREAM_URL = "https://github.com/HUAYUE1024/mi-fitness-mcp-cn"
MIN_PY = (3, 11)
CLONE_TIMEOUT = 240

# 直连优先，失败依次换镜像（都是对同一仓库的只读转发）
SOURCES = [
    ("GitHub 直连", UPSTREAM_URL),
    ("ghfast 代理", "https://ghfast.top/" + UPSTREAM_URL),
    ("ghproxy 代理", "https://ghproxy.net/" + UPSTREAM_URL),
    ("gitclone 镜像", "https://gitclone.com/github.com/HUAYUE1024/mi-fitness-mcp-cn"),
]


def ok(msg):
    print(f"✓ {msg}")


def warn(msg):
    print(f"⚠ {msg}")


def fail(msg):
    print(f"✗ {msg}", file=sys.stderr)


# ── 1. 取源码 ────────────────────────────────────────────────────

def git_version() -> str | None:
    exe = shutil.which("git")
    if not exe:
        return None
    try:
        r = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=20)
        return (r.stdout or "").strip() or None
    except Exception:
        return None


def looks_like_source(d: Path) -> bool:
    return (d / "pyproject.toml").exists() and (d / "src" / "mi_fitness_mcp").is_dir()


def read_version(d: Path) -> str:
    try:
        t = (d / "pyproject.toml").read_text(encoding="utf-8", errors="replace")
        m = re.search(r'^version\s*=\s*"([^"]+)"', t, re.M)
        if m:
            return m.group(1)
    except OSError:
        pass
    return "未标注"


def git_clone(url: str, target: Path) -> tuple[bool, str]:
    """浅克隆到 target 的临时邻居目录，成功后 rename，避免半成品留在原地。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=".clone-", dir=str(target.parent)))
    dest = tmp / TARGET_NAME
    try:
        r = subprocess.run(
            ["git", "clone", "--depth", "1", "--single-branch", url, str(dest)],
            capture_output=True, text=True, errors="replace", timeout=CLONE_TIMEOUT,
        )
        if r.returncode != 0 or not looks_like_source(dest):
            msg = ((r.stderr or "") + (r.stdout or "")).strip().splitlines()
            return False, (msg[-1] if msg else f"退出码 {r.returncode}")
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(dest), str(target))
        return True, ""
    except subprocess.TimeoutExpired:
        return False, f"超时（>{CLONE_TIMEOUT}s）"
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:200]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def copy_local(src: Path, target: Path) -> tuple[bool, str]:
    if not looks_like_source(src):
        return False, f"{src} 下找不到 pyproject.toml + src/mi_fitness_mcp"
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, target, ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__"))
    return (True, "") if looks_like_source(target) else (False, "复制后结构不完整")


def manual_hint(target: Path) -> None:
    print("\n手动兜底（任选其一，做完重跑本脚本即可跳过下载）：", file=sys.stderr)
    print(f"  A. 换源重试：python install_band_mcp.py --root <PROJECT_ROOT> --src-url <镜像URL>", file=sys.stderr)
    print(f"  B. 浏览器下载：打开 {UPSTREAM_URL} → Code → Download ZIP", file=sys.stderr)
    print(f"     解压后把带 pyproject.toml 的那一层放到：{target}", file=sys.stderr)
    print(f"  C. 已有本地副本：加 --from-path <那份源码目录>", file=sys.stderr)


# ── 2. 探测 Python ≥ 3.11 ───────────────────────────────────────

def py_version(py: str) -> tuple[int, int] | None:
    try:
        r = subprocess.run([py, "--version"], capture_output=True, text=True, timeout=30)
        out = (r.stdout or "") + (r.stderr or "")
        parts = out.strip().split()
        if len(parts) >= 2 and parts[0].lower() == "python":
            nums = parts[1].split(".")
            return int(nums[0]), int(nums[1])
    except Exception:
        pass
    return None


def detect_python(explicit: str | None) -> str | None:
    candidates: list[str] = []
    if explicit:
        candidates.append(explicit)
    home = Path.home()
    candidates.append(str(home / ".workbuddy" / "binaries" / "python" / "envs" / "default" / "Scripts" / "python.exe"))
    candidates.append(str(home / ".workbuddy" / "binaries" / "python" / "envs" / "default" / "bin" / "python"))
    for name in ("python3", "python"):
        found = shutil.which(name)
        if found:
            candidates.append(found)

    seen = set()
    for py in candidates:
        if py in seen:
            continue
        seen.add(py)
        if not (Path(py).exists() or shutil.which(py)):
            continue
        ver = py_version(py)
        if ver and ver >= MIN_PY:
            return py
        if ver:
            warn(f"{py} 版本 {ver[0]}.{ver[1]} 低于 3.11，跳过")
    return None


# ── 4. venv + pip install ───────────────────────────────────────

def venv_python(venv: Path) -> Path:
    if os.name == "nt":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def ensure_venv(py: str, venv: Path, force: bool) -> Path:
    vpy = venv_python(venv)
    if vpy.exists() and not force:
        ok(f"虚拟环境已存在，复用：{venv}")
        return vpy
    if venv.exists() and force:
        warn(f"--force：重建虚拟环境 {venv}")
        shutil.rmtree(venv)
    r = subprocess.run([py, "-m", "venv", str(venv)])
    if r.returncode != 0 or not vpy.exists():
        fail("创建虚拟环境失败")
        raise SystemExit(1)
    ok(f"虚拟环境就绪：{venv}")
    return vpy


def pip_install(vpy: Path, target: Path, extras: bool, index_url: str | None) -> None:
    spec = ".[all]" if extras else "."
    cmd = [str(vpy), "-m", "pip", "install", "-e", spec, "--no-input"]
    if index_url:
        cmd += ["--index-url", index_url]
    print(f"\n安装依赖（约 1–3 分钟，视网速）：{' '.join(cmd[2:])}")
    print("-" * 60)
    r = subprocess.run(cmd, cwd=str(target))
    print("-" * 60)
    if r.returncode != 0:
        fail("pip install 失败。常见原因与处理：")
        print("    · 国内访问 PyPI 慢/超时 → 加 --index-url https://pypi.tuna.tsinghua.edu.cn/simple 重试", file=sys.stderr)
        print("    · 已配镜像仍失败 → 检查网络后重跑（源码已就绪，不会重复下载）", file=sys.stderr)
        print(f"    · 手动方式：cd {target} && {venv_python(target / '.venv')} -m pip install -e '{spec}'", file=sys.stderr)
        raise SystemExit(1)
    ok("依赖安装完成")


# ── 5. 验证 CLI + 输出指引 ──────────────────────────────────────

def mcp_exe_path(venv: Path) -> Path:
    if os.name == "nt":
        return venv / "Scripts" / "mi-fitness-mcp.exe"
    return venv / "bin" / "mi-fitness-mcp"


def verify_cli(exe: Path) -> bool:
    if not exe.exists():
        warn(f"未找到 CLI 可执行文件：{exe}")
        return False
    r = subprocess.run([str(exe), "--help"], capture_output=True, text=True, timeout=60)
    out = (r.stdout or "") + (r.stderr or "")
    if r.returncode == 0 and "usage" in out.lower():
        return True
    warn(f"CLI 自检输出异常：{out[:200]}")
    return False


def expected_source_db() -> Path:
    """复刻 platformdirs.user_data_dir('mi-fitness-mcp') 的默认值，仅作预览；
    真实路径以 mi-fitness-mcp doctor 输出为准。"""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "mi-fitness-mcp" / "mi-fitness-mcp" / "mi_fitness.db"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "mi-fitness-mcp" / "mi_fitness.db"
    return Path.home() / ".local" / "share" / "mi-fitness-mcp" / "mi_fitness.db"


def print_next_steps(exe: Path, root: Path) -> None:
    db = expected_source_db()
    print("\n" + "=" * 60)
    print("安装完成。接下来（登录必须你本人操作，脚本不能代劳）：")
    print("=" * 60)
    print(f"\nmcp_exe（填入 config.yaml 的 sources.band.mcp_exe）：\n  {exe}")
    print(f"\n预期源库路径（填入 sources.band.source_db，以 doctor 实际输出为准）：\n  {db}")
    print("\n【第一步：登录，二选一】")
    print("  A. 扫码登录（推荐，无需抓 Cookie）：")
    print(f"     终端1：{exe} api")
    print(f"     终端2：{exe} web")
    print("     浏览器打开 http://127.0.0.1:8322/ → 扫码登录 → 小米运动健康 App 确认")
    print("  B. 手动 Cookie：")
    print("     浏览器登录 account.xiaomi.com，从 Cookie 取 userId 与 passToken，然后：")
    print(f"     {exe} setup --mode mi_fitness_cloud --user-id <userId> --pass-token <passToken> --region cn")
    print("\n【第二步：验证连通】（输出里看「连接状态 ✅」和「数据库： <路径>」）")
    print(f"  {exe} doctor")
    print("\n【第三步：回专家】把 doctor 显示的源库路径告诉专家，由专家写入 config.yaml，")
    print("  然后跑 sync_band.py --days 3 --dry-run 预演，确认后实跑。")
    print("\n第三方声明：本项目非小米官方，云端接口为逆向所得，可能因官方改版失效；")
    print("只读取你本人账号数据，凭据存本机系统 keyring，不落明文。")
    print("=" * 60)


# ── main ────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(
        description="安装 mi-fitness-mcp（小米云端取数，可选增强；源码运行时从上游拉取）")
    ap.add_argument("--root", default=".", help="健康数据项目根目录（含 data/ config/ 的那一级）")
    ap.add_argument("--python", dest="py", default=None, help="显式指定用于建 venv 的 Python（需 ≥3.11）")
    ap.add_argument("--src-url", default=os.environ.get("BAND_SRC_URL"),
                    help="只从这个 URL 克隆（跳过内置源列表）；国内可用镜像地址")
    ap.add_argument("--from-path", default=None, help="用本地已有源码目录，不联网")
    ap.add_argument("--force", action="store_true", help="重装：重新取源码 + 重建 venv")
    ap.add_argument("--no-extras", action="store_true", help="只装核心依赖，不装 Web 扫码仪表盘")
    ap.add_argument("--index-url", default=None, help="pip 镜像源（国内慢时用清华源等）")
    ap.add_argument("--dry-run", action="store_true", help="只报告计划，不执行")
    args = ap.parse_args()

    print("mi-fitness-mcp 安装器（健康数据管家 · 可选增强 · 源码运行时拉取）")
    print(f"上游仓库：{UPSTREAM_URL}")

    root = Path(args.root).expanduser().resolve()
    target = root / "vendor" / TARGET_NAME
    venv = target / ".venv"
    exe = mcp_exe_path(venv)

    # Python 先探，失败就别白克隆
    py = detect_python(args.py)
    if not py:
        fail("未找到 Python ≥ 3.11（mi-fitness-mcp 的硬性要求）")
        print("    装好 Python 3.11+ 后用 --python <路径> 指定，或加入 PATH 重试。", file=sys.stderr)
        return 1
    ver = py_version(py)
    ok(f"使用 Python {ver[0]}.{ver[1]}：{py}")

    # 源码现状
    has_src = looks_like_source(target)
    if has_src and not args.force:
        ok(f"源码已就绪：{target}（v{read_version(target)}，跳过下载）")
    elif args.from_path:
        ok(f"源码来源：本地目录 {args.from_path}（不联网）")
    elif args.src_url:
        ok(f"源码来源：指定 URL {args.src_url}")
    else:
        gv = git_version()
        if gv:
            ok(f"git 可用：{gv}；将按「直连 → 代理镜像」顺序尝试")
        else:
            warn("本机没有 git —— 只能用浏览器下载 ZIP 放到指定目录（见末尾兜底）")

    if args.dry_run:
        spec = "." if args.no_extras else ".[all]"
        print("\n[dry-run] 计划执行：")
        if not has_src:
            if args.from_path:
                print(f"  1. 复制本地源码 {args.from_path} → {target}")
            else:
                srcs = [args.src_url] if args.src_url else [u for _, u in SOURCES]
                print("  1. git clone --depth 1 依次尝试：")
                for i, u in enumerate(srcs, 1):
                    print(f"       {i}) {u}")
                print(f"     目标：{target}")
        else:
            print("  1. 源码已存在，跳过")
        print(f"  2. 用 {py} 建 venv → {venv}")
        print(f"  3. pip install -e '{spec}'")
        print(f"  4. 验证 CLI：{exe} --help")
        print(f"  5. 预期源库：{expected_source_db()}")
        print("\n（未做任何改动）")
        return 0

    # 1) 取源码
    if not (has_src and not args.force):
        target.parent.mkdir(parents=True, exist_ok=True)
        if args.from_path:
            okd, err = copy_local(Path(args.from_path).expanduser(), target)
            if not okd:
                fail(f"本地源码不可用：{err}")
                return 1
            ok(f"已复制本地源码 → {target}（v{read_version(target)}）")
        else:
            if not git_version():
                fail("本机没有 git，无法自动拉取源码")
                manual_hint(target)
                return 1
            tries = [(args.src_url, "指定 URL")] if args.src_url else SOURCES
            done = False
            for label, url in tries:
                print(f"\n拉取源码（{label}）：{url}")
                gok, gerr = git_clone(url, target)
                if gok:
                    ok(f"已克隆 → {target}（v{read_version(target)}）")
                    done = True
                    break
                warn(f"{label} 失败：{gerr}")
            if not done:
                fail("所有源都失败——网络或镜像不可用")
                manual_hint(target)
                return 1

    # 4) venv + 安装（已有可用环境就跳过，--force 才重装）
    if venv_python(venv).exists() and exe.exists() and not args.force:
        ok("依赖已装好，跳过 pip（要重装加 --force）")
    else:
        vpy = ensure_venv(py, venv, args.force)
        pip_install(vpy, target, not args.no_extras, args.index_url)

    # 5) 验证 + 指引
    print()
    if verify_cli(exe):
        ok(f"CLI 可用：{exe}")
    else:
        warn("CLI 自检未过，但依赖已装——可手动跑一次 --help 查看原因")
    print_next_steps(exe, root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
