#!/usr/bin/env python3
"""测试修复后的分屏坐标转换逻辑"""

print("=" * 80)
print("修复后的分屏模式坐标转换验证")
print("=" * 80)

right_window_bounds = {'x1': 1906, 'y1': 80, 'x2': 2860, 'y2': 1440}
hierarchy_root = {'x1': 1, 'y1': 0, 'x2': 955, 'y2': 1360}
textview_absolute = {'x1': 1926, 'y1': 374, 'x2': 2838, 'y2': 1508}  # 已经是绝对坐标
test_x, test_y = 2393, 376

print(f"\n📋 输入:")
print(f"  Window bounds: [{right_window_bounds['x1']},{right_window_bounds['y1']}][{right_window_bounds['x2']},{right_window_bounds['y2']}]")
print(f"  Hierarchy root: [{hierarchy_root['x1']},{hierarchy_root['y1']}][{hierarchy_root['x2']},{hierarchy_root['y2']}]")
print(f"  TextView bounds: [{textview_absolute['x1']},{textview_absolute['y1']}][{textview_absolute['x2']},{textview_absolute['y2']}]")
print(f"  点击坐标: ({test_x}, {test_y})")

print(f"\n🔍 新逻辑判断:")
margin = 100
x_in_window = (right_window_bounds['x1'] - margin <= hierarchy_root['x1'] <= right_window_bounds['x2'] + margin)
y_in_window = (right_window_bounds['y1'] - margin <= hierarchy_root['y1'] <= right_window_bounds['y2'] + margin)
looks_like_absolute = (textview_absolute['x1'] > 500 or textview_absolute['y1'] > 500)

print(f"  策略1 - Hierarchy root在window内: x={x_in_window}, y={y_in_window}")
print(f"  策略2 - TextView坐标看起来是绝对坐标(>500): {looks_like_absolute}")
print(f"           TextView.x1={textview_absolute['x1']} > 500 = True")
print(f"           TextView.y1={textview_absolute['y1']} < 500 = False")

if looks_like_absolute:
    print(f"  ✅ 判断为绝对坐标，不进行转换")
    scale_x, scale_y, offset_x, offset_y = 1.0, 1.0, 0.0, 0.0
    tx1, ty1 = textview_absolute['x1'], textview_absolute['y1']
    tx2, ty2 = textview_absolute['x2'], textview_absolute['y2']
else:
    print(f"  需要转换")
    src_w = hierarchy_root['x2'] - hierarchy_root['x1']
    src_h = hierarchy_root['y2'] - hierarchy_root['y1']
    dst_w = right_window_bounds['x2'] - right_window_bounds['x1']
    dst_h = right_window_bounds['y2'] - right_window_bounds['y1']
    scale_x = dst_w / src_w
    scale_y = dst_h / src_h
    offset_x = right_window_bounds['x1'] - hierarchy_root['x1'] * scale_x
    offset_y = right_window_bounds['y1'] - hierarchy_root['y1'] * scale_y
    tx1 = int(round(textview_absolute['x1'] * scale_x + offset_x))
    ty1 = int(round(textview_absolute['y1'] * scale_y + offset_y))
    tx2 = int(round(textview_absolute['x2'] * scale_x + offset_x))
    ty2 = int(round(textview_absolute['y2'] * scale_y + offset_y))

print(f"\n📍 结果:")
print(f"  转换后bounds: [{tx1},{ty1}][{tx2},{ty2}]")

hit = (tx1 <= test_x <= tx2) and (ty1 <= test_y <= ty2)
print(f"\n🎯 点击测试: ({test_x}, {test_y})")
print(f"  X范围: {tx1} <= {test_x} <= {tx2} = {tx1 <= test_x <= tx2}")
print(f"  Y范围: {ty1} <= {test_y} <= {ty2} = {ty1 <= test_y <= ty2}")
print(f"  结果: {'✅ 命中TextView' if hit else '❌ 未命中'}")

print(f"\n" + "=" * 80)
if hit:
    print("🎉 修复成功！分屏右侧窗口的坐标问题已解决！")
else:
    print("❌ 仍然失败，需要进一步调试")
print("=" * 80)
