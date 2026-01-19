# CarUI Accessibility Service - 辅助服务方案

## 📋 概述

这是一个基于Android Accessibility Service的UI检查工具，用于解决uiautomator在特定场景（分屏、滚动列表等）下无法获取准确坐标的问题。

## ✨ 特性

- ✅ 实时获取完整UI树结构
- ✅ 准确的屏幕坐标（包括分屏场景）
- ✅ 支持多窗口、多Display
- ✅ 轻量级HTTP服务器（端口8765）
- ✅ 不干扰其他辅助服务

## 🔧 编译方法

### 方式1：使用Android Studio

1. 用Android Studio打开此目录
2. 等待Gradle同步完成
3. 点击 Build → Build Bundle(s) / APK(s) → Build APK(s)
4. APK输出路径：`build/outputs/apk/debug/accessibility_service-debug.apk`

### 方式2：使用命令行

```bash
cd accessibility_service
./gradlew assembleDebug
# APK位置: build/outputs/apk/debug/accessibility_service-debug.apk
```

## 📦 安装步骤

### 1. 安装APK到设备

```bash
adb install -r accessibility_service-debug.apk
```

### 2. 启用辅助服务

**方法A：通过应用引导**
1. 在设备上打开"CarUI Accessibility"应用
2. 点击"打开辅助功能设置"按钮
3. 在列表中找到"CarUI Accessibility Service"
4. 点击启用

**方法B：直接进入设置**
1. 打开系统设置 → 辅助功能
2. 找到"CarUI Accessibility Service"
3. 启用服务

### 3. 验证服务状态

```bash
# 检查服务是否运行
adb shell dumpsys accessibility | grep CarUI

# 测试HTTP接口
adb forward tcp:8765 tcp:8765
curl http://localhost:8765/api/status
```

## 🌐 API接口

服务启动后，在设备的8765端口提供HTTP接口：

### 1. 获取UI树

```
GET /api/hierarchy?display=0
```

**参数:**
- `display`: Display ID（默认0）

**响应:**
```json
{
  "success": true,
  "error": null,
  "nodes": [
    {
      "className": "android.widget.FrameLayout",
      "text": "",
      "contentDescription": "",
      "resourceId": "",
      "bounds": {
        "left": 0,
        "top": 0,
        "right": 1920,
        "bottom": 1080
      },
      "clickable": false,
      "window": {
        "title": "StatusBar",
        "type": 1,
        "displayId": 0
      },
      "children": [...]
    }
  ]
}
```

### 2. 检查服务状态

```
GET /api/status
```

**响应:**
```json
{
  "service": "running",
  "port": 8765
}
```

## 🔌 与Python服务器集成

修改`server/main.py`，添加辅助服务数据源：

```python
def get_hierarchy_from_accessibility(serial: str, display: int = 0):
    """从辅助服务获取UI树"""
    try:
        # 设置端口转发
        subprocess.run(["adb", "-s", serial, "forward", "tcp:8765", "tcp:8765"], 
                      check=True, timeout=5)
        
        # 请求UI树
        response = requests.get(f"http://localhost:8765/api/hierarchy?display={display}", 
                               timeout=5)
        if response.status_code == 200:
            data = response.json()
            if data.get("success"):
                return convert_accessibility_to_xml(data["nodes"])
        
        return None
    except Exception as e:
        print(f"从辅助服务获取UI树失败: {e}")
        return None

@app.get("/api/hierarchy")
def get_hierarchy(display: int = 0, use_accessibility: bool = False):
    global current_serial
    if not current_serial:
        raise HTTPException(status_code=400, detail="Device not connected")
    
    # 优先使用辅助服务
    if use_accessibility:
        xml_content = get_hierarchy_from_accessibility(current_serial, display)
        if xml_content:
            return {"xml": xml_content, "source": "accessibility"}
    
    # Fallback到uiautomator
    # ... 现有逻辑 ...
```

## ⚠️ 重要说明

### 对其他辅助服务的影响

**✅ 通常不会影响其他服务：**
- Android支持多个辅助服务同时运行
- 本服务只读取UI树，不拦截用户操作
- 不会修改UI内容或发送事件

**⚠️ 需要注意：**
- 某些车载系统可能限制辅助服务数量
- 如果设备上有语音助手等关键服务，建议先在测试环境验证
- 运行多个辅助服务会有轻微性能开销

### 性能考虑

- HTTP服务器非常轻量，仅在请求时获取UI树
- 不会持续监听UI事件
- 建议按需启用，不使用时可关闭服务

## 🐛 故障排查

### 服务无法启动

1. 检查权限：确保已在辅助功能设置中启用
2. 查看日志：`adb logcat -s CarUIAccessibility`
3. 重启设备后重新启用服务

### HTTP接口无响应

1. 检查端口转发：`adb forward --list`
2. 重新设置转发：`adb forward tcp:8765 tcp:8765`
3. 检查防火墙设置

### UI树数据不完整

1. 确认Display ID正确
2. 检查目标窗口是否可访问
3. 某些系统窗口可能有访问限制

## 📄 许可证

与主项目保持一致

## 🤝 贡献

欢迎提交Issue和Pull Request
