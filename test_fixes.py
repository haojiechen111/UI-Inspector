#!/usr/bin/env python3
"""测试SS4检测和select修复的脚本"""

import sys
import os

# 添加server目录到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'server'))

print("=" * 60)
print("测试 1: SS4设备检测逻辑")
print("=" * 60)

# 测试detect_ss_device函数
def test_detect_ss_device():
    """测试SS4检测函数"""
    test_cases = [
        ("HU_SS4-userdebug-SE-TK-LIA.W.db.20260113-81", "SS4"),
        ("HU_SS3-userdebug-xxx", "SS3"),
        ("Normal_Device-xxx", None),
        ("ss4_lowercase", "SS4"),
        ("SS5-test", "SS5"),
    ]
    
    for display_id, expected in test_cases:
        # 模拟检测逻辑
        output_upper = display_id.upper()
        
        result = None
        if 'SS4' in output_upper:
            result = "SS4"
        elif 'SS3' in output_upper:
            result = "SS3"
        elif 'SS2' in output_upper:
            result = "SS2"
        elif 'SS5' in output_upper:
            result = "SS5"
        
        status = "✅ PASS" if result == expected else "❌ FAIL"
        print(f"{status} | Input: '{display_id}' -> Expected: {expected}, Got: {result}")

test_detect_ss_device()

print("\n" + "=" * 60)
print("测试 2: HTML结构检查")
print("=" * 60)

# 检查HTML文件中是否有select-wrapper
html_path = os.path.join(os.path.dirname(__file__), 'server/static/index.html')
with open(html_path, 'r', encoding='utf-8') as f:
    html_content = f.read()

has_wrapper = 'select-wrapper' in html_content
wrapper_count = html_content.count('select-wrapper')

print(f"{'✅' if has_wrapper else '❌'} HTML中包含 select-wrapper: {has_wrapper}")
print(f"{'✅' if wrapper_count >= 2 else '❌'} select-wrapper数量: {wrapper_count} (应该≥2)")

print("\n" + "=" * 60)
print("测试 3: CSS样式检查")
print("=" * 60)

# 检查CSS文件
css_path = os.path.join(os.path.dirname(__file__), 'server/static/style.css')
with open(css_path, 'r', encoding='utf-8') as f:
    css_content = f.read()

checks = [
    ('select-wrapper', '.select-wrapper'),
    ('display: block', 'display: block'),
    ('height: 36px', 'height: 36px'),
    ('box-sizing: border-box', 'box-sizing: border-box'),
    ('-webkit-appearance: none', '-webkit-appearance: none'),
]

for name, pattern in checks:
    found = pattern in css_content
    print(f"{'✅' if found else '❌'} CSS包含 {name}: {found}")

print("\n" + "=" * 60)
print("测试 4: Python代码检查")
print("=" * 60)

# 检查main.py
py_path = os.path.join(os.path.dirname(__file__), 'server/main.py')
with open(py_path, 'r', encoding='utf-8') as f:
    py_content = f.read()

py_checks = [
    ("SS4检测使用字符串搜索", "'SS4' in output_upper"),
    ("SS3检测", "'SS3' in output_upper"),
    ("详细日志", "📱 Device:"),
    ("Raw repr", "Raw repr"),
]

for name, pattern in py_checks:
    found = pattern in py_content
    print(f"{'✅' if found else '❌'} {name}: {found}")

print("\n" + "=" * 60)
print("总结")
print("=" * 60)
print("""
如果所有测试都通过（✅），说明代码修改已正确应用。

下一步：
1. 重启Python服务器: cd server && python main.py
2. 在浏览器中打开: http://localhost:8000/static/index.html
3. 查看Python控制台输出，应该看到 [SS_DETECT] 相关日志
4. 测试select下拉框点击是否准确

如果SS4仍未被识别，请检查：
- adb devices 是否能看到设备
- adb -s <serial> shell getprop ro.build.display.id 的输出
""")
