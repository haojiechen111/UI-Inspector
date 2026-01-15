# Car UI Inspector - Android Studio Plugin

This is the Android Studio implementation of the Car UI Inspector tool.

## Prerequisites
- Android Studio (Flamingo or newer recommended)
- JDK 17
- Python 3.7+ (支持 Windows, macOS, Linux/Ubuntu)
- Python 依赖包: `fastapi`, `uvicorn`, `adbutils`, `pillow`

### 依赖安装（跨平台）

插件会自动检测 Python 环境和依赖包，如果缺少依赖，会显示针对您操作系统的安装命令。

**Windows:**
```bash
pip install -r server/requirements.txt
# 或单独安装
pip install fastapi uvicorn adbutils pillow
```

**macOS:**
```bash
pip3 install -r server/requirements.txt
# 或单独安装
pip3 install fastapi uvicorn adbutils pillow
```

**Ubuntu/Debian:**
```bash
# 首先确保安装了 Python 和 pip
sudo apt update && sudo apt install python3 python3-pip
# 然后安装依赖
pip3 install -r server/requirements.txt
```

**CentOS/RHEL:**
```bash
# 首先确保安装了 Python 和 pip
sudo yum install python3 python3-pip
# 然后安装依赖
pip3 install -r server/requirements.txt
```

## How to Build & Install
1. Open this folder (`android_studio_plugin`) in Android Studio or IntelliJ IDEA.
2. The project will automatically sync with Gradle.
3. Run the task `./gradlew buildPlugin` from the terminal or Gradle tool window.
4. The generated plugin zip will be in `build/distributions/`.
5. In Android Studio, go to `Settings` -> `Plugins` -> `⚙️` -> `Install Plugin from Disk...` and select the zip.

## Features
- Real-time Car UI mirroring in a Tool Window.
- Multi-display support (Display 0, 2, 4, 5).
- High-performance ADB capture (300ms refresh).
- Integrated Python backend logic.
- **SS4 device auto-detection and initialization**
- **Custom modal dialogs for device/display selection** (better than native select)

## Server Configuration
- **Port**: `18888` (changed from 8000 to avoid conflicts with other services)
- If you need to change the port, modify:
  - `server/main.py` - line with `uvicorn.run(..., port=18888)`
  - `src/main/kotlin/com/carui/inspector/PythonServerManager.kt` - `SERVER_URL`
  - `src/main/kotlin/com/carui/inspector/CarUiToolWindowFactory.kt` - `browser.loadURL(...)`

## Manual Server Testing
If you want to test the server independently:
```bash
cd server
python main.py
# Then open: http://localhost:18888/static/index.html
```

## Project Structure
- `src/`: Kotlin source code for the IDE integration.
- `server/`: Python backend and static Web UI assets.

## Recent Updates
- ✅ **Enhanced cross-platform support** (Windows, macOS, Linux/Ubuntu, CentOS/RHEL)
- ✅ **Smart pip detection** - Automatically detects available pip commands (pip, pip3, python -m pip)
- ✅ **Platform-specific installation guidance** - Shows customized installation commands based on OS
- ✅ Fixed SS4 device detection (direct string matching)
- ✅ Replaced native `<select>` with custom modal dialogs to fix WebView click issues
- ✅ Changed default port from 8000 to 18888 to avoid port conflicts

## Cross-Platform Compatibility
This plugin has been designed to work seamlessly across major operating systems:

- ✅ **Windows 10/11** - Full support with automatic pip detection
- ✅ **macOS** - Tested on Intel and Apple Silicon (M1/M2)
- ✅ **Ubuntu/Debian** - Full support with apt package manager integration
- ✅ **Linux (CentOS/RHEL)** - Full support with yum package manager integration

The plugin automatically:
1. Detects your operating system
2. Finds available Python and pip commands
3. Provides OS-specific installation instructions if dependencies are missing
4. Uses the most reliable pip installation method for your system
