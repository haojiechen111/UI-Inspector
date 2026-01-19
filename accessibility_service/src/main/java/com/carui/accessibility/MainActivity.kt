package com.carui.accessibility

import android.content.Intent
import android.os.Bundle
import android.provider.Settings
import android.widget.Button
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity

class MainActivity : AppCompatActivity() {

    private lateinit var statusText: TextView
    private lateinit var openSettingsButton: Button

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        
        // 简单布局
        val layout = android.widget.LinearLayout(this).apply {
            orientation = android.widget.LinearLayout.VERTICAL
            setPadding(50, 50, 50, 50)
        }
        
        // 标题
        val titleText = TextView(this).apply {
            text = "CarUI Accessibility Service"
            textSize = 24f
            setPadding(0, 0, 0, 40)
        }
        layout.addView(titleText)
        
        // 状态文本
        statusText = TextView(this).apply {
            text = "检查服务状态..."
            textSize = 16f
            setPadding(0, 0, 0, 20)
        }
        layout.addView(statusText)
        
        // 打开设置按钮
        openSettingsButton = Button(this).apply {
            text = "打开辅助功能设置"
            setOnClickListener {
                openAccessibilitySettings()
            }
        }
        layout.addView(openSettingsButton)
        
        // 说明文本
        val instructionText = TextView(this).apply {
            text = """
                使用说明:
                1. 点击上方按钮打开辅助功能设置
                2. 找到"CarUI Accessibility Service"
                3. 启用该服务
                4. 服务启动后会在端口8765提供HTTP接口
                5. Python服务器可通过该接口获取UI树
            """.trimIndent()
            textSize = 14f
            setPadding(0, 40, 0, 0)
        }
        layout.addView(instructionText)
        
        setContentView(layout)
    }

    override fun onResume() {
        super.onResume()
        updateStatus()
    }

    private fun updateStatus() {
        val isEnabled = CarUIAccessibilityService.instance != null
        statusText.text = if (isEnabled) {
            "✅ 服务已启用\nHTTP端口: 8765"
        } else {
            "❌ 服务未启用"
        }
    }

    private fun openAccessibilitySettings() {
        val intent = Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)
        startActivity(intent)
    }
}
