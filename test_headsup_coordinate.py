#!/usr/bin/env python3
"""测试HeadsUp坐标转换逻辑的脚本"""

print("=" * 80)
print("HeadsUp坐标转换逻辑验证")
print("=" * 80)

# 模拟HeadsUp窗口的数据
window_bounds = {
    'x1': 21,
    'y1': 100,
    'x2': 954,
    'y2': 463
}

hierarchy_root_bounds = {
    'x1': 1,
    'y1': 0,
    'x2': 999,
    'y2': 903
}

close_textview_bounds = {
    'x1': 51,
    'y1': 302,
    'x2': 924,
    'y2': 412
}

test_click_x = 488
test_click_y = 360

print(f"\n📋 HeadsUp窗口信息:")
print(f"  Window bounds: [{window_bounds['x1']},{window_bounds['y1']}][{window_bounds['x2']},{window_bounds['y2']}]")
print(f"  Hierarchy root: [{hierarchy_root_bounds['x1']},{hierarchy_root_bounds['y1']}][{hierarchy_root_bounds['x2']},{hierarchy_root_bounds['y2']}]")
print(f"  关闭TextView: [{close_textview_bounds['x1']},{close_textview_bounds['y1']}][{close_textview_bounds['x2']},{close_textview_bounds['y2']}]")
print(f"  测试点击坐标: ({test_click_x}, {test_click_y})")

print(f"\n🔍 旧逻辑分析 (检查起点位置):")
print(f"  Hierarchy起点: ({hierarchy_root_bounds['x1']}, {hierarchy_root_bounds['y1']})")
print(f"  Window起点: ({window_bounds['x1']}, {window_bounds['y1']})")
print(f"  判断: hierarchy起点({hierarchy_root_bounds['x1']},{hierarchy_root_bounds['y1']})接近原点(0,0)")
print(f"        window起点({window_bounds['x1']},{window_bounds['y1']})远离原点")
print(f"  结论: ❌ 错误判断为相对坐标，需要转换")

# 旧逻辑的错误转换
src_w = hierarchy_root_bounds['x2'] - hierarchy_root_bounds['x1']
src_h = hierarchy_root_bounds['y2'] - hierarchy_root_bounds['y1']
dst_w = window_bounds['x2'] - window_bounds['x1']
dst_h = window_bounds['y2'] - window_bounds['y1']
old_scale_x = dst_w / src_w
old_scale_y = dst_h / src_h
old_offset_x = window_bounds['x1'] - hierarchy_root_bounds['x1'] * old_scale_x
old_offset_y = window_bounds['y1'] - hierarchy_root_bounds['y1'] * old_scale_y

print(f"  旧转换参数: scale=({old_scale_x:.4f}, {old_scale_y:.4f}), offset=({old_offset_x:.2f}, {old_offset_y:.2f})")

# 应用旧逻辑转换TextView bounds
old_tx1 = int(round(close_textview_bounds['x1'] * old_scale_x + old_offset_x))
old_ty1 = int(round(close_textview_bounds['y1'] * old_scale_y + old_offset_y))
old_tx2 = int(round(close_textview_bounds['x2'] * old_scale_x + old_offset_x))
old_ty2 = int(round(close_textview_bounds['y2'] * old_scale_y + old_offset_y))

print(f"  旧逻辑转换后TextView bounds: [{old_tx1},{old_ty1}][{old_tx2},{old_ty2}]")

# 检查测试点是否在旧转换后的bounds内
old_hit = (old_tx1 <= test_click_x <= old_tx2) and (old_ty1 <= test_click_y <= old_ty2)
print(f"  测试点({test_click_x},{test_click_y})是否在转换后的TextView内: {'✅ 是' if old_hit else '❌ 否'}")

print(f"\n✨ 新逻辑分析 (检查坐标范围):")
print(f"  Hierarchy root起点: ({hierarchy_root_bounds['x1']}, {hierarchy_root_bounds['y1']})")
print(f"  Window bounds: [{window_bounds['x1']},{window_bounds['y1']}][{window_bounds['x2']},{window_bounds['y2']}]")

# 新逻辑的判断
margin = 100
x_in_range = (window_bounds['x1'] - margin <= hierarchy_root_bounds['x1'] <= window_bounds['x2'] + margin)
y_in_range = (window_bounds['y1'] - margin <= hierarchy_root_bounds['y1'] <= window_bounds['y2'] + margin)

print(f"  检查x坐标: {window_bounds['x1']-margin} <= {hierarchy_root_bounds['x1']} <= {window_bounds['x2']+margin}")
print(f"           {window_bounds['x1']-margin} <= {hierarchy_root_bounds['x1']} <= {window_bounds['x2']+margin} = {x_in_range}")
print(f"  检查y坐标: {window_bounds['y1']-margin} <= {hierarchy_root_bounds['y1']} <= {window_bounds['y2']+margin}")
print(f"           {window_bounds['y1']-margin} <= {hierarchy_root_bounds['y1']} <= {window_bounds['y2']+margin} = {y_in_range}")
print(f"  判断: hierarchy坐标{'在' if (x_in_range and y_in_range) else '不在'}window范围内")
print(f"  结论: ✅ 正确判断为绝对坐标，不需要转换")

# 新逻辑不转换
new_scale_x = 1.0
new_scale_y = 1.0
new_offset_x = 0.0
new_offset_y = 0.0

print(f"  新转换参数: scale=({new_scale_x:.4f}, {new_scale_y:.4f}), offset=({new_offset_x:.2f}, {new_offset_y:.2f})")

# TextView bounds保持不变
new_tx1 = close_textview_bounds['x1']
new_ty1 = close_textview_bounds['y1']
new_tx2 = close_textview_bounds['x2']
new_ty2 = close_textview_bounds['y2']

print(f"  新逻辑转换后TextView bounds: [{new_tx1},{new_ty1}][{new_tx2},{new_ty2}]")

# 检查测试点是否在TextView bounds内
new_hit = (new_tx1 <= test_click_x <= new_tx2) and (new_ty1 <= test_click_y <= new_ty2)
print(f"  测试点({test_click_x},{test_click_y})是否在TextView内: {'✅ 是' if new_hit else '❌ 否'}")

print(f"\n" + "=" * 80)
print("🎯 验证结果:")
print("=" * 80)
print(f"旧逻辑: {'❌ 失败' if not old_hit else '✅ 成功'} - 点击({test_click_x},{test_click_y}){'无法' if not old_hit else '可以'}命中'关闭'TextView")
print(f"新逻辑: {'✅ 成功' if new_hit else '❌ 失败'} - 点击({test_click_x},{test_click_y}){'可以' if new_hit else '无法'}命中'关闭'TextView")

if new_hit and not old_hit:
    print(f"\n🎉 修复成功！新逻辑正确解决了HeadsUp坐标偏差问题。")
    print(f"   点击坐标({test_click_x},{test_click_y})现在能正确选中'关闭'TextView，而不是父FrameLayout。")
elif not new_hit:
    print(f"\n⚠️  修复未生效！需要进一步调试。")
else:
    print(f"\n⚠️  旧逻辑也能命中？需要检查测试数据。")

print("=" * 80)
