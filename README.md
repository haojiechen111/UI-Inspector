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

### 方法1：在 Android Studio 中编译（推荐）✨

这是最简单可靠的方式，无需配置系统环境：

1. **打开项目**
   - 启动 Android Studio
   - 选择 `File` → `Open`
   - 选择本项目目录

2. **等待 Gradle 同步**
   - Android Studio 会自动下载 Gradle 并同步项目
   - 等待右下角的同步进度完成

3. **编译插件**
   
   **方式A：使用 Gradle 工具窗口（推荐）**
   - 打开右侧的 `Gradle` 工具窗口（`View` → `Tool Windows` → `Gradle`）
   - 展开 `Tasks` → `intellij`
   - 双击 `buildPlugin` 任务
   
   **方式B：使用 Terminal**
   - 打开 Android Studio 底部的 `Terminal` 标签
   - 如果 gradlew 存在，执行：`./gradlew buildPlugin`
   - 如果 gradlew 不存在，先执行：`gradle wrapper`，然后再执行：`./gradlew buildPlugin`

4. **获取编译结果**
   - 编译成功后，插件包位于：`build/distributions/UI-Inspector-x.x.x.zip`

5. **安装插件**
   - 在 Android Studio 中，进入 `Settings/Preferences` → `Plugins`
   - 点击 ⚙️ 图标 → `Install Plugin from Disk...`
   - 选择刚才生成的 `.zip` 文件
   - 重启 Android Studio

### 方法2：命令行编译

**前提条件：**
- 已安装 JDK 17
- 已安装 Gradle 或使用项目自带的 Gradle Wrapper

**步骤：**

```bash
# 1. 进入项目目录
cd /path/to/UI-Inspector

# 2a. 如果有 gradlew（推荐）
./gradlew buildPlugin

# 2b. 如果没有 gradlew，先生成 wrapper
gradle wrapper
./gradlew buildPlugin

# 2c. 或直接使用系统 gradle
gradle buildPlugin

# 3. 编译产物在
ls -la build/distributions/
```

### 环境问题排查

**问题1：`gradle: command not found`**
```bash
# Ubuntu/Debian
sudo apt update && sudo apt install gradle

# macOS
brew install gradle

# 或者不安装gradle，直接在Android Studio中编译
```

**问题2：`./gradlew: No such file or directory`**
```bash
# 方案A：生成 gradle wrapper
gradle wrapper

# 方案B：直接在 Android Studio 中编译（推荐）
```

**问题3：Shell 配置文件语法错误**
```bash
# 如果遇到 /etc/profile 或 .bash_profile 错误
# 暂时跳过配置文件执行：
bash --noprofile --norc
cd /path/to/UI-Inspector
gradle buildPlugin
```

## Features
- Real-time Car UI mirroring in a Tool Window.
- Multi-display support (Display 0, 2, 4, 5).
- High-performance ADB capture (300ms refresh).
- Integrated Python backend logic.
- **SS4 device auto-detection and initialization**
- **Custom modal dialogs for device/display selection** (better than native select)
- **默认使用辅助服务（AccessibilityService）获取节点，可手动切换到 UIAutomator**

## Server Configuration
- **Port**: `18888` (changed from 8000 to avoid conflicts with other services)
- If you need to change the port, modify:
  - `server/main.py` - line with `uvicorn.run(..., port=18888)`
  - `src/main/kotlin/com/carui/inspector/PythonServerManager.kt` - `SERVER_URL`
  - `src/main/kotlin/com/carui/inspector/CarUiToolWindowFactory.kt` - `browser.loadURL(...)`

## 编译辅助服务APK

本项目包含一个辅助服务APK（`accessibility_service`），用于解决UIAutomator在分屏、滚动列表等场景下的坐标问题。

### 快速编译（使用脚本）

```bash
# 在项目根目录执行
./compile_apk.sh
```

脚本会自动：
- ✅ 检测Android SDK环境
- ✅ 选择可用的gradle命令
- ✅ 编译APK并显示路径
- ✅ 提供安装步骤说明

### 手动编译

**方式1：在Android Studio中编译**
```bash
# 1. 用Android Studio打开 accessibility_service 目录
cd accessibility_service

# 2. 等待Gradle同步完成
# 3. 选择 Build → Build Bundle(s) / APK(s) → Build APK(s)
# 4. APK位置: build/outputs/apk/debug/CarUIAccessibilityService-debug.apk
```

**方式2：使用命令行**
```bash
cd accessibility_service
./gradlew assembleDebug
# 或
gradle assembleDebug
```

### 安装和使用

```bash
# 1. 安装APK
adb install -r accessibility_service/build/outputs/apk/debug/CarUIAccessibilityService-debug.apk

# 2. 在设备上启用辅助服务
# - 打开"CarUI Accessibility"应用
# - 点击"打开辅助功能设置"
# - 启用"CarUI Accessibility Service"

# 3. 验证服务
adb forward tcp:8765 tcp:8765
curl http://localhost:8765/api/status
```

📖 **详细文档：** 查看 [`accessibility_service/README.md`](accessibility_service/README.md)

---

## 数据源（默认辅助服务 / 可切换 UIAutomator）

Web UI 顶部有一个“数据源”开关：

- **默认：辅助服务**（适合分屏、滚动列表、截图受限等场景；SS4 也支持端口连接）
- 手动关闭后：**UIAutomator**（兼容传统安卓设备/场景）

你提到的“自由对话开关”开启后，通常意味着车机侧无障碍能力已打开；此时建议保持默认的“辅助服务”模式。

---

## 连接设备时自动安装无障碍 APK（无需手动 adb install）

从 **v1.0.0+** 起，插件在你点击“连接设备”成功后，会自动检查并在必要时安装：

`CarUIAccessibilityService-debug.apk`

实现方式：该 APK 会被打包进插件安装目录的 `server/` 目录中，Web UI 连接成功后会调用：

`POST /api/accessibility/ensure`（仅安装模式：`enable_service=false, probe_running=false`）

因此你不需要再手动执行：

```bash
adb install -r accessibility_service/build/outputs/apk/debug/CarUIAccessibilityService-debug.apk
```

> 注意：自动安装只负责“装 APK”；是否启用无障碍服务仍取决于你是否打开了 Web UI 顶部的“数据源=辅助服务”，以及设备侧是否允许写 secure settings / root。

---

## SS4 设备上“辅助服务:未运行”的排查与修复建议

你看到的状态一般来自 Web UI 调用 `GET /api/accessibility/status`：

- **enabled**：通过 `adb shell settings get secure enabled_accessibility_services` 判断（部分 SS4/车机系统可能不支持该命令）
- **running**：通过 `adb forward tcp:8765 tcp:8765` + 访问 `http://localhost:8765/api/status` 判断（服务端口由无障碍服务内置 HTTP 提供）

### 1) 典型原因（SS4 特别常见）

SS4 初始化后你会使用 `localhost:5559` 作为截图/输入的 serial，但“辅助服务 APK 实际运行”的 serial 可能是：

- `localhost:5559`（多数环境）
- 或者初始化前的 **原始物理 serial**（少数环境）

如果后台错误地只对其中一个 serial 做 `settings/forward/probe`，就会出现：

> 已连接正常，但一直显示「辅助服务:未运行」

本仓库已在 `server/main.py` 中修复：

- `/api/accessibility/enable|disable|status` 统一使用更稳健的 serial 选择逻辑
- `/api/accessibility/status` 会返回 `shell_serial / target_serial / probe.candidates` 等信息帮助定位

### 2) 直接诊断命令（建议你在电脑上执行）

假设你当前连接是 `localhost:5559`，但也请把 `<ORIG_SERIAL>` 换成你的原始 serial 都跑一遍：

```bash
# A) 看无障碍服务是否在 enabled 列表里（如果 settings 命令可用）
adb -s localhost:5559 shell settings get secure enabled_accessibility_services

# B) 看我们的 APK 是否安装
adb -s localhost:5559 shell pm path com.carui.accessibility

# C) 看服务日志（只抓我们 TAG）
adb -s localhost:5559 logcat -s CarUIAccessibility

# D) 如果你怀疑服务跑在原始 serial 上：
adb -s <ORIG_SERIAL> shell settings get secure enabled_accessibility_services
adb -s <ORIG_SERIAL> shell pm path com.carui.accessibility
adb -s <ORIG_SERIAL> logcat -s CarUIAccessibility
```

### 3) 如果 settings 页面打不开/无法手动启用

辅助服务 APK 内的 `CarUI Accessibility` App 有 “一键启用（ROOT）” 按钮，会尝试通过 `su` 写入 secure settings。

如果设备有 root，也可以用 ADB 兜底（会覆盖 enabled_accessibility_services，请你先读取原值再追加）：

```bash
adb -s <SERIAL> shell settings put secure enabled_accessibility_services "<原值>:com.carui.accessibility/.CarUIAccessibilityService"
adb -s <SERIAL> shell settings put secure accessibility_enabled 1
```

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

## 可见即可说（Text Visible Speak）一键启动（集成到插件）

插件 ToolWindow 顶部新增按钮：**“启动可见即可说模拟”**。

点击后会在电脑上启动一个独立的 Python GUI（tkinter），用于：
- 自动 `adb forward tcp:27183 tcp:27183`
- 启动设备端 `TextVisibleSpeakRemoteService`
- 在 GUI 中输入文本，触发设备端“可见即可说”执行（依赖设备端能力）

### 依赖
- `adb` 在 PATH 中可用
- `python3`/`python` 可用，且 **tkinter** 可用
  - Ubuntu/Debian: `sudo apt-get install python3-tk`

### 设备端要求（重要）
该功能只负责启动 PC 侧 GUI。

设备端必须已集成并注册：
- `com.chehejia.car.voice/.TextVisibleSpeakRemoteService`

并且设备端无障碍服务需启用（否则 RouterService 无法执行点击）。

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
