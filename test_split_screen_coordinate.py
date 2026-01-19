#!/usr/bin/env python3
"""测试分屏模式下的坐标转换逻辑"""

print("=" * 80)
print("分屏模式坐标转换逻辑验证")
print("=" * 80)

# 模拟分屏场景：左侧窗口和右侧窗口
left_window_bounds = {
    'x1': 974,
    'y1': 80,
    'x2': 1906,
    'y2': 1440
}

right_window_bounds = {
    'x1': 1906,
    'y1': 80,
    'x2': 2860,
    'y2': 1440
}

# 假设右侧窗口的hierarchy使用相对坐标(从0开始)
right_hierarchy_root_bounds = {
    'x1': 0,
    'y1': 0,
    'x2': 954,  # 宽度 = 2860 - 1906 = 954
    'y2': 1360  # 高度 = 1440 - 80 = 1360
}

# 假设右侧窗口的hierarchy使用相对坐标(从1开始,类似HeadsUp)
right_hierarchy_root_bounds_v2 = {
    'x1': 1,
    'y1': 0,
    'x2': 955,
    'y2': 1360
}

# "听歌观影" TextView在右侧窗口的相对坐标
textview_relative_bounds = {
    'x1': 20,
    'y1': 294,
    'x2': 932,
    'y2': 1428
}

test_click_x = 2393
test_click_y = 376

print(f"\n📋 分屏窗口信息:")
print(f"  左侧窗口 bounds: [{left_window_bounds['x1']},{left_window_bounds['y1']}][{left_window_bounds['x2']},{left_window_bounds['y2']}]")
print(f"  右侧窗口 bounds: [{right_window_bounds['x1']},{right_window_bounds['y1']}][{right_window_bounds['x2']},{right_window_bounds['y2']}]")
print(f"  右侧Hierarchy root (版本1): [{right_hierarchy_root_bounds['x1']},{right_hierarchy_root_bounds['y1']}][{right_hierarchy_root_bounds['x2']},{right_hierarchy_root_bounds['y2']}]")
print(f"  右侧Hierarchy root (版本2): [{right_hierarchy_root_bounds_v2['x1']},{right_hierarchy_root_bounds_v2['y1']}][{right_hierarchy_root_bounds_v2['x2']},{right_hierarchy_root_bounds_v2['y2']}]")
print(f"  TextView相对坐标: [{textview_relative_bounds['x1']},{textview_relative_bounds['y1']}][{textview_relative_bounds['x2']},{textview_relative_bounds['y2']}]")
print(f"  测试点击坐标: ({test_click_x}, {test_click_y})")

def test_transform(window_bounds, hierarchy_root, textview_bounds, name):
    print(f"\n{'='*80}")
    print(f"测试场景: {name}")
    print(f"{'='*80}")
    
    margin = 100
    
    # 新逻辑：检查hierarchy坐标是否在window范围内
    x_in_range = (window_bounds['x1'] - margin <= hierarchy_root['x1'] <= window_bounds['x2'] + margin)
    y_in_range = (window_bounds['y1'] - margin <= hierarchy_root['y1'] <= window_bounds['y2'] + margin)
    
    print(f"\n🔍 坐标系判断:")
    print(f"  Hierarchy起点: ({hierarchy_root['x1']}, {hierarchy_root['y1']})")
    print(f"  Window范围: [{window_bounds['x1']},{window_bounds['y1']}][{window_bounds['x2']},{window_bounds['y2']}]")
    print(f"  检查X: {window_bounds['x1']-margin} <= {hierarchy_root['x1']} <= {window_bounds['x2']+margin} = {x_in_range}")
    print(f"  检查Y: {window_bounds['y1']-margin} <= {hierarchy_root['y1']} <= {window_bounds['y2']+margin} = {y_in_range}")
    print(f"  判断结果: {'绝对坐标' if (x_in_range and y_in_range) else '相对坐标'}")
    
    if x_in_range and y_in_range:
        # 绝对坐标，不转换
        scale_x = 1.0
        scale_y = 1.0
        offset_x = 0.0
        offset_y = 0.0
        print(f"  ✅ 不需要转换")
    else:
        # 相对坐标，需要转换
        src_w = max(1, hierarchy_root['x2'] - hierarchy_root['x1'])
        src_h = max(1, hierarchy_root['y2'] - hierarchy_root['y1'])
        dst_w = max(1, window_bounds['x2'] - window_bounds['x1'])
        dst_h = max(1, window_bounds['y2'] - window_bounds['y1'])
        scale_x = dst_w / src_w
        scale_y = dst_h / src_h
        offset_x = window_bounds['x1'] - hierarchy_root['x1'] * scale_x
        offset_y = window_bounds['y1'] - hierarchy_root['y1'] * scale_y
        print(f"  ✅ 需要转换: scale=({scale_x:.4f}, {scale_y:.4f}), offset=({offset_x:.2f}, {offset_y:.2f})")
    
    # 转换TextView坐标
    tx1 = int(round(textview_bounds['x1'] * scale_x + offset_x))
    ty1 = int(round(textview_bounds['y1'] * scale_y + offset_y))
    tx2 = int(round(textview_bounds['x2'] * scale_x + offset_x))
    ty2 = int(round(textview_bounds['y2'] * scale_y + offset_y))
    
    print(f"\n📍 TextView坐标转换:")
    print(f"  转换前: [{textview_bounds['x1']},{textview_bounds['y1']}][{textview_bounds['x2']},{textview_bounds['y2']}]")
    print(f"  转换后: [{tx1},{ty1}][{tx2},{ty2}]")
    
    # 检查测试点是否在TextView内
    hit = (tx1 <= test_click_x <= tx2) and (ty1 <= test_click_y <= ty2)
    print(f"\n🎯 点击测试:")
    print(f"  点击坐标: ({test_click_x}, {test_click_y})")
    print(f"  X范围: {tx1} <= {test_click_x} <= {tx2} = {tx1 <= test_click_x <= tx2}")
    print(f"  Y范围: {ty1} <= {test_click_y} <= {ty2} = {ty1 <= test_click_y <= ty2}")
    print(f"  结果: {'✅ 命中' if hit else '❌ 未命中'}")
    
    return hit

# 测试不同的场景
result1 = test_transform(
    right_window_bounds,
    right_hierarchy_root_bounds,
    textview_relative_bounds,
    "右侧窗口 - Hierarchy从(0,0)开始"
)

result2 = test_transform(
    right_window_bounds,
    right_hierarchy_root_bounds_v2,
    textview_relative_bounds,
    "右侧窗口 - Hierarchy从(1,0)开始"
)

# 测试如果TextView已经是绝对坐标
textview_absolute_bounds = {
    'x1': 1926,  # 1906 + 20
    'y1': 374,   # 80 + 294
    'x2': 2838,  # 1906 + 932
    'y2': 1508   # 80 + 1428
}

result3 = test_transform(
    right_window_bounds,
    right_hierarchy_root_bounds_v2,
    textview_absolute_bounds,
    "右侧窗口 - TextView已经是绝对坐标"
)

print(f"\n" + "=" * 80)
print("🎯 总结:")
print("=" * 80)
print(f"场景1 (相对坐标从0开始): {'✅ 成功' if result1 else '❌ 失败'}")
print(f"场景2 (相对坐标从1开始): {'✅ 成功' if result2 else '❌ 失败'}")
print(f"场景3 (TextView绝对坐标): {'✅ 成功' if result3 else '❌ 失败'}")

if not any([result1, result2, result3]):
    print(f"\n⚠️  所有场景都失败了！可能需要检查:")
    print(f"  1. Window bounds是否正确")
    print(f"  2. Hierarchy root bounds是否正确")
    print(f"  3. TextView bounds是否正确")
    print(f"  4. 坐标转换逻辑是否有问题")
elif result3 and not (result1 or result2):
    print(f"\n💡 提示: TextView可能已经使用绝对坐标，但被误判为相对坐标并进行了错误转换")
    print(f"  需要改进坐标系判断逻辑，直接检查TextView的坐标范围")
print("=" * 80)
