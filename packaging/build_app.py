"""Build an onedir executable and a native archive on the current operating system."""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def platform_tag() -> str:
    machine = platform.machine().lower()
    arch = "arm64" if machine in {"arm64", "aarch64"} else "x64" if machine in {"x86_64", "amd64"} else machine
    if sys.platform == "win32":
        return f"windows-{arch}"
    if sys.platform == "darwin":
        return f"macos-{arch}"
    return f"linux-{arch}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build FGDocQA with PyInstaller on this OS; Windows binaries must be built on Windows.")
    parser.add_argument("--dist-dir", type=Path, default=ROOT / "dist", help="Final output root; application is placed in <dist-dir>/FGDocQA.")
    parser.add_argument("--work-dir", type=Path, default=ROOT / "build" / "pyinstaller", help="Temporary PyInstaller work/spec directory.")
    parser.add_argument("--no-archive", action="store_true", help="Only build the application directory.")
    args = parser.parse_args()
    dist = args.dist_dir.resolve()
    work = args.work_dir.resolve()
    work.mkdir(parents=True, exist_ok=True)
    tag = platform_tag()
    command = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--console",
        "--name", "FGDocQA", "--paths", str(ROOT / "src"),
        "--distpath", str(dist), "--workpath", str(work / "work"), "--specpath", str(work),
        "--add-data", f"{ROOT / 'src' / 'docqa' / 'web' / 'static'}{os.pathsep}docqa/web/static",
        "--add-data", f"{ROOT / 'examples'}{os.pathsep}examples",
        "--collect-all", "pypdf", "--collect-all", "PIL",
        "--hidden-import", "docqa.cli", "--hidden-import", "docqa.web.server",
    ]
    if sys.platform == "win32":
        command.extend(["--icon", str(ROOT / "src" / "docqa" / "web" / "static" / "favicon.ico")])
    command.append(str(ROOT / "packaging" / "launcher.py"))
    subprocess.run(command, cwd=ROOT, check=True)
    app = dist / "FGDocQA"
    if sys.platform == "win32":
        shutil.copy2(ROOT / "packaging" / "启动FGDocQA.bat", app)
        executable = app / "FGDocQA.exe"
    else:
        launcher = app / "启动FGDocQA.command"
        shutil.copy2(ROOT / "packaging" / "启动FGDocQA.command", launcher)
        launcher.chmod(0o755)
        executable = app / "FGDocQA"
        executable.chmod(executable.stat().st_mode | 0o111)
    licenses = app / "licenses"
    licenses.mkdir(exist_ok=True)
    for filename in ("ARTWORK-LICENSE.md", "whale-artwork-LICENSE-ARTWORK.txt", "whale-artwork-NOTICE.txt"):
        shutil.copy2(ROOT / "src" / "docqa" / "web" / "static" / filename, licenses / filename)
    (app / "使用说明.txt").write_text(
        "FGDocQA 离线演示版\n\n"
        "请先完整解压，保留 FGDocQA 与 _internal 的目录结构。\n"
        "Windows 双击 FGDocQA.exe 或 启动FGDocQA.bat；macOS 双击 启动FGDocQA.command。\n"
        "无需安装 Python，无需 API Key。程序默认强制 Mock，不读取 .env。\n"
        "首次运行自动生成示例知识库并打开浏览器；控制台显示实际本机网址。\n"
        "默认使用 127.0.0.1:8770；端口占用时自动选择其他空闲端口。\n"
        "请保持控制台窗口打开，按 Ctrl+C 或关闭该窗口停止服务。\n"
        "演示数据写入用户应用数据目录的 FGDocQA/demo-v1，不写入解压目录。\n"
        "命令行自检：FGDocQA --smoke-test（Windows 为 FGDocQA.exe --smoke-test）。\n"
        "更多命令运行 --help。真实 API 接入请使用源码版并参阅 README.md。\n",
        encoding="utf-8",
    )
    (app / "BUILD-INFO.json").write_text(json.dumps({
        "application": "FGDocQA", "version": "1.0.0-submission", "platform": tag,
        "python": platform.python_version(), "mode": "mock", "standalone": True,
        "requires_python_installation": False,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Executable: {executable}")
    if not args.no_archive:
        # tar preserves macOS framework symlinks and executable permissions.
        archive_format = "zip" if sys.platform == "win32" else "gztar"
        archive = shutil.make_archive(str(dist / f"FGDocQA-{tag}"), archive_format, root_dir=dist, base_dir="FGDocQA")
        print(f"Archive: {archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
