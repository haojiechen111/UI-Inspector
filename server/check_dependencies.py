#!/usr/bin/env python3
"""
依赖检查脚本
用于在启动服务前检查所有必要的依赖是否已安装
"""
import sys
import json
import platform
import subprocess
import os

def check_dependencies():
    """检查所有必要的Python包"""
    # 获取完整的Python版本字符串（包括所有版本号部分）
    python_version_full = sys.version.split()[0]  # 例如: "3.9.24"
    
    # 检测当前使用的 Python 命令
    python_executable = sys.executable  # 当前运行的 Python 解释器路径
    python_cmd = os.path.basename(python_executable)  # python 或 python3
    
    # 使用 python --version 命令获取版本（用户建议的方式）
    python_version_from_cmd = None
    try:
        result = subprocess.run([python_executable, "--version"], 
                              capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            # Python 3.4+ 输出到 stdout，早期版本输出到 stderr
            version_output = result.stdout.strip() or result.stderr.strip()
            # 提取版本号，例如 "Python 3.9.24" -> "3.9.24"
            if version_output.startswith("Python "):
                python_version_from_cmd = version_output.split()[1]
    except:
        pass
    
    # 检测可用的 pip 命令
    pip_cmd = None
    pip_methods = []
    
    # 方法1: 尝试 pip3
    try:
        result = subprocess.run(["pip3", "--version"], capture_output=True, timeout=5)
        if result.returncode == 0:
            pip_cmd = "pip3"
            pip_methods.append("pip3")
    except:
        pass
    
    # 方法2: 尝试 pip
    try:
        result = subprocess.run(["pip", "--version"], capture_output=True, timeout=5)
        if result.returncode == 0:
            if not pip_cmd:  # 如果 pip3 不可用，使用 pip
                pip_cmd = "pip"
            pip_methods.append("pip")
    except:
        pass
    
    # 方法3: python -m pip (最可靠的方法)
    try:
        result = subprocess.run([python_executable, "-m", "pip", "--version"], 
                              capture_output=True, timeout=5)
        if result.returncode == 0:
            if not pip_cmd:  # 如果前面的都不可用，使用 python -m pip
                pip_cmd = f"{python_cmd} -m pip"
            pip_methods.append(f"{python_cmd} -m pip")
    except:
        pass
    
    os_type = platform.system()  # Windows, Darwin (Mac), Linux
    os_name = platform.platform()  # 详细的操作系统信息
    
    results = {
        "python_version": python_version_full,
        "python_version_from_cmd": python_version_from_cmd,  # 使用 --version 命令获取的版本
        "python_ok": sys.version_info >= (3, 7),
        "python_executable": python_executable,
        "python_cmd": python_cmd,
        "pip_cmd": pip_cmd,
        "pip_methods": pip_methods,
        "os_type": os_type,
        "os_name": os_name,
        "dependencies": {},
        "sys_path": sys.path[:5]  # 显示前5个路径，帮助诊断
    }
    
    # 包名映射：import名称 -> pip安装名称
    package_mapping = {
        "fastapi": "fastapi",
        "uvicorn": "uvicorn",
        "adbutils": "adbutils",
        "requests": "requests",
        "PIL": "pillow",  # Pillow uses PIL as import name
        "urllib3": "urllib3"
    }
    
    for import_name, pip_name in package_mapping.items():
        try:
            if import_name == "PIL":
                __import__("PIL")
            else:
                __import__(import_name)
            results["dependencies"][pip_name] = {
                "installed": True, 
                "error": None,
                "import_name": import_name
            }
        except ImportError as e:
            # 为缺失的依赖提供详细的安装建议
            install_cmd = None
            if pip_cmd:
                # 优先推荐使用 python -m pip，因为它确保安装到当前运行的 Python
                if f"{python_cmd} -m pip" in pip_methods:
                    install_cmd = f"{python_cmd} -m pip install {pip_name}"
                else:
                    install_cmd = f"{pip_cmd} install {pip_name}"
            else:
                install_cmd = f"{python_cmd} -m pip install {pip_name}"
            
            results["dependencies"][pip_name] = {
                "installed": False, 
                "error": str(e),
                "import_name": import_name,
                "install_cmd": install_cmd
            }
    
    # 检查是否所有依赖都已安装
    all_ok = results["python_ok"] and all(
        dep["installed"] for dep in results["dependencies"].values()
    )
    results["all_ok"] = all_ok
    
    # 输出JSON格式，方便Kotlin解析
    print(json.dumps(results, ensure_ascii=False))
    return 0 if all_ok else 1

if __name__ == "__main__":
    sys.exit(check_dependencies())
