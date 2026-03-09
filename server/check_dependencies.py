#!/usr/bin/env python3
"""Car UI Inspector 环境检查脚本（增强版）

目标：
1) 给小白用户更明确的安装指引
2) 对常见兼容性问题（Python/pip/ADB）做显式诊断
3) 输出结构化 JSON，方便 IDE 侧渲染友好错误页面
"""

import json
import os
import platform
import shutil
import subprocess
import sys
from typing import Dict, List, Optional, Tuple


PACKAGE_MAPPING = {
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
    "adbutils": "adbutils",
    "requests": "requests",
    "PIL": "pillow",  # Pillow import name is PIL
    "urllib3": "urllib3",
}


def _run(args: List[str], timeout: int = 6) -> Tuple[int, str, str]:
    """Best-effort subprocess runner."""
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
        return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()
    except Exception as e:
        return 1, "", str(e)


def _detect_pip(python_executable: str, python_cmd: str) -> Tuple[Optional[str], List[str], bool]:
    """Detect pip command candidates.

    Returns:
      (preferred_pip_cmd, pip_methods, pip_ok)
    """
    pip_methods: List[str] = []
    pip_cmd: Optional[str] = None

    for cmd in ("pip3", "pip"):
        rc, _, _ = _run([cmd, "--version"], timeout=4)
        if rc == 0:
            pip_methods.append(cmd)
            if pip_cmd is None:
                pip_cmd = cmd

    # Most reliable method: current interpreter -m pip
    rc, _, _ = _run([python_executable, "-m", "pip", "--version"], timeout=4)
    if rc == 0:
        method = f"{python_cmd} -m pip"
        pip_methods.append(method)
        pip_cmd = method

    # de-dup while preserving order
    seen = set()
    dedup = []
    for m in pip_methods:
        if m not in seen:
            dedup.append(m)
            seen.add(m)

    return pip_cmd, dedup, pip_cmd is not None


def _detect_adb() -> Dict:
    """Detect adb command and version, with fallback path hints."""
    info = {
        "adb_ok": False,
        "adb_cmd": "adb",
        "adb_path": None,
        "adb_version": None,
        "adb_error": None,
        "adb_candidates": [],
    }

    adb_path = shutil.which("adb")
    if adb_path:
        rc, out, err = _run(["adb", "version"], timeout=5)
        if rc == 0:
            first_line = (out.splitlines() or [""])[0]
            info.update({
                "adb_ok": True,
                "adb_path": adb_path,
                "adb_version": first_line or "unknown",
            })
            return info
        info["adb_error"] = err or out or "adb command failed"

    # Candidate SDK paths (for better guidance only)
    home = os.path.expanduser("~")
    candidates = [
        os.path.join(os.environ.get("ANDROID_HOME", ""), "platform-tools", "adb") if os.environ.get("ANDROID_HOME") else "",
        os.path.join(os.environ.get("ANDROID_SDK_ROOT", ""), "platform-tools", "adb") if os.environ.get("ANDROID_SDK_ROOT") else "",
        os.path.join(home, "Android", "Sdk", "platform-tools", "adb"),
        os.path.join(home, "Library", "Android", "sdk", "platform-tools", "adb"),
        os.path.join(home, "AppData", "Local", "Android", "Sdk", "platform-tools", "adb.exe"),
    ]

    existing = [p for p in candidates if p and os.path.exists(p)]
    info["adb_candidates"] = existing
    if not info["adb_error"]:
        info["adb_error"] = "adb not found in PATH"
    return info


def _os_adb_install_hint(os_type: str) -> str:
    if os_type == "Windows":
        return (
            "安装 Android SDK Platform-Tools，并把 platform-tools 目录加入 PATH。"
            "可在 Android Studio > SDK Manager 安装。"
        )
    if os_type == "Darwin":
        return "可执行：brew install android-platform-tools"
    return "可执行：sudo apt update && sudo apt install android-sdk-platform-tools"


def _linux_pip_install_cmd() -> str:
    """Best-effort pip install command for common Linux distros."""
    if shutil.which("apt"):
        return "sudo apt update && sudo apt install -y python3-pip"
    if shutil.which("dnf"):
        return "sudo dnf install -y python3-pip"
    if shutil.which("yum"):
        return "sudo yum install -y python3-pip"
    if shutil.which("pacman"):
        return "sudo pacman -Sy --noconfirm python-pip"
    if shutil.which("zypper"):
        return "sudo zypper install -y python3-pip"
    # Last-resort fallback
    return "curl -sS https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py && python3 /tmp/get-pip.py"


def _get_pip_bootstrap_cmd(os_type: str, python_executable: str, python_cmd: str) -> Tuple[str, bool]:
    """Return a command to install/repair pip and whether ensurepip is available."""
    rc, _, _ = _run([python_executable, "-m", "ensurepip", "--version"], timeout=4)
    ensurepip_ok = rc == 0
    if ensurepip_ok:
        return f"{python_cmd} -m ensurepip --upgrade", True

    if os_type == "Windows":
        return "winget install --id Python.Python.3 -e", False
    if os_type == "Darwin":
        return "brew install python", False
    return _linux_pip_install_cmd(), False


def check_dependencies() -> int:
    python_version_full = sys.version.split()[0]
    python_executable = sys.executable
    python_cmd = os.path.basename(python_executable) or "python"

    python_version_from_cmd = None
    rc, out, err = _run([python_executable, "--version"], timeout=4)
    if rc == 0:
        version_output = out or err
        if version_output.startswith("Python "):
            parts = version_output.split()
            if len(parts) > 1:
                python_version_from_cmd = parts[1]

    pip_cmd, pip_methods, pip_ok = _detect_pip(python_executable, python_cmd)
    adb_info = _detect_adb()

    os_type = platform.system()
    os_name = platform.platform()
    pip_bootstrap_cmd, ensurepip_ok = _get_pip_bootstrap_cmd(os_type, python_executable, python_cmd)

    results: Dict = {
        "python_version": python_version_full,
        "python_version_from_cmd": python_version_from_cmd,
        "python_ok": sys.version_info >= (3, 7),
        "python_executable": python_executable,
        "python_cmd": python_cmd,
        "pip_cmd": pip_cmd,
        "pip_methods": pip_methods,
        "pip_ok": pip_ok,
        "ensurepip_ok": ensurepip_ok,
        "pip_bootstrap_cmd": pip_bootstrap_cmd,
        "os_type": os_type,
        "os_name": os_name,
        "dependencies": {},
        "sys_path": sys.path[:5],
        "adb_ok": adb_info["adb_ok"],
        "adb_cmd": adb_info["adb_cmd"],
        "adb_path": adb_info["adb_path"],
        "adb_version": adb_info["adb_version"],
        "adb_error": adb_info["adb_error"],
        "adb_candidates": adb_info["adb_candidates"],
        "install_all_cmd": None,
        "recommendations": [],
    }

    missing_packages: List[str] = []
    for import_name, pip_name in PACKAGE_MAPPING.items():
        try:
            __import__(import_name)
            results["dependencies"][pip_name] = {
                "installed": True,
                "error": None,
                "import_name": import_name,
            }
        except ImportError as e:
            missing_packages.append(pip_name)
            install_cmd = (
                f"{python_cmd} -m pip install {pip_name}"
                if pip_ok
                else f"{pip_bootstrap_cmd} && {python_cmd} -m pip install {pip_name}"
            )
            results["dependencies"][pip_name] = {
                "installed": False,
                "error": str(e),
                "import_name": import_name,
                "install_cmd": install_cmd,
            }

    if missing_packages:
        # 对最终用户更易理解的一键命令
        joined = " ".join(sorted(set(missing_packages)))
        results["install_all_cmd"] = (
            f"{python_cmd} -m pip install {joined}"
            if pip_ok
            else f"{pip_bootstrap_cmd} && {python_cmd} -m pip install {joined}"
        )

    recommendations: List[str] = []
    if not results["python_ok"]:
        recommendations.append(f"Python 版本过低，当前 {python_version_full}，请升级到 3.7+。")

    if missing_packages:
        if not pip_ok:
            recommendations.append("未检测到可用 pip，请先安装/修复 pip。")
            recommendations.append(f"安装/修复 pip 可执行：{pip_bootstrap_cmd}")
        recommendations.append("缺少 Python 依赖包，请先安装再重试。")

    if not results["adb_ok"]:
        recommendations.append("未检测到 adb，请安装 Android Platform-Tools 并加入 PATH。")
        recommendations.append(_os_adb_install_hint(os_type))
        if results["adb_candidates"]:
            recommendations.append(
                "检测到可能存在 adb 但未加入 PATH：" + ", ".join(results["adb_candidates"])
            )

    results["recommendations"] = recommendations

    all_ok = (
        results["python_ok"]
        and results["adb_ok"]
        and all(dep["installed"] for dep in results["dependencies"].values())
    )
    results["all_ok"] = all_ok

    print(json.dumps(results, ensure_ascii=False))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(check_dependencies())
