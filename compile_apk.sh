#!/bin/bash
# 编译辅助服务APK的脚本

echo "🚀 开始编译CarUI辅助服务APK..."
echo ""

# 检查Android SDK
if [ -z "$ANDROID_HOME" ]; then
    echo "❌ 未设置ANDROID_HOME环境变量"
    echo "请设置ANDROID_HOME指向Android SDK路径，例如："
    echo "  export ANDROID_HOME=~/Android/Sdk"
    echo ""
    echo "🔍 尝试查找Android SDK..."
    
    # 常见的Android SDK位置
    POSSIBLE_PATHS=(
        "$HOME/Android/Sdk"
        "$HOME/.android/sdk"
        "/opt/android-sdk"
        "$HOME/Library/Android/sdk"
    )
    
    for path in "${POSSIBLE_PATHS[@]}"; do
        if [ -d "$path" ]; then
            echo "✅ 找到Android SDK: $path"
            export ANDROID_HOME="$path"
            export PATH="$PATH:$ANDROID_HOME/platform-tools:$ANDROID_HOME/tools"
            break
        fi
    done
    
    if [ -z "$ANDROID_HOME" ]; then
        echo "❌ 无法找到Android SDK"
        exit 1
    fi
fi

echo "✅ Android SDK: $ANDROID_HOME"
echo ""

# 进入accessibility_service目录
cd "$(dirname "$0")/accessibility_service"

if [ ! -f "build.gradle" ]; then
    echo "❌ 未找到build.gradle文件"
    exit 1
fi

echo "📦 项目路径: $(pwd)"
echo ""

# 检查是否有gradle
if command -v gradle &> /dev/null; then
    echo "✅ 使用系统gradle"
    GRADLE_CMD="gradle"
elif [ -f "../gradlew" ]; then
    echo "✅ 使用项目gradlew"
    GRADLE_CMD="../gradlew"
elif [ -f "gradlew" ]; then
    echo "✅ 使用当前目录gradlew"
    GRADLE_CMD="./gradlew"
else
    echo "⚠️ 未找到gradle命令，尝试使用Android Studio..."
    echo ""
    echo "请在Android Studio中："
    echo "1. 打开项目: $(pwd)"
    echo "2. 选择 Build → Build Bundle(s) / APK(s) → Build APK(s)"
    echo "3. 等待编译完成"
    echo ""
    echo "或者手动安装gradle："
    echo "  sudo apt install gradle  # Ubuntu/Debian"
    echo "  brew install gradle      # macOS"
    exit 1
fi

# 清理旧的构建
echo "🧹 清理旧的构建..."
rm -rf build/

# 编译APK
echo "🔨 开始编译..."
$GRADLE_CMD assembleDebug

if [ $? -eq 0 ]; then
    APK_PATH="build/outputs/apk/debug/accessibility_service-debug.apk"
    if [ -f "$APK_PATH" ]; then
        APK_SIZE=$(du -h "$APK_PATH" | cut -f1)
        echo ""
        echo "✅ ============================================"
        echo "✅ 编译成功！"
        echo "✅ ============================================"
        echo "📦 APK位置: $APK_PATH"
        echo "📊 APK大小: $APK_SIZE"
        echo ""
        echo "下一步："
        echo "1. 连接Android设备: adb devices"
        echo "2. 安装APK: adb install -r $APK_PATH"
        echo "3. 在设备上启用辅助服务"
        echo ""
    else
        echo "❌ APK文件未生成: $APK_PATH"
        exit 1
    fi
else
    echo ""
    echo "❌ 编译失败"
    echo "请检查错误信息"
    exit 1
fi
