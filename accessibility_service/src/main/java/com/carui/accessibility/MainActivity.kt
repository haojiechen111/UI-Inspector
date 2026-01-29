package com.carui.accessibility

import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.widget.Button
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import java.io.BufferedReader
import java.io.InputStreamReader

class MainActivity : AppCompatActivity() {

    private lateinit var statusText: TextView
    private lateinit var openSettingsButton: Button
    private lateinit var enableServiceButton: Button
    private lateinit var helpText: TextView

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

        // 一键启用按钮（需要 root）
        enableServiceButton = Button(this).apply {
            text = "一键启用（ROOT）"
            setOnClickListener {
                enableAccessibilityServiceByRoot()
            }
        }
        layout.addView(enableServiceButton)
        
        // 说明/帮助文本
        helpText = TextView(this).apply {
            textSize = 14f
            setPadding(0, 40, 0, 0)
        }
        layout.addView(helpText)
        
        setContentView(layout)
    }

    override fun onResume() {
        super.onResume()
        updateStatus()
    }

    private fun updateStatus() {
        val enabled = isAccessibilityServiceEnabled()
        val running = CarUIAccessibilityService.instance != null

        statusText.text = when {
            enabled && running -> "✅ 已启用且运行中\nHTTP端口: 8765"
            enabled -> "✅ 已启用（等待系统启动服务）\nHTTP端口: 8765"
            else -> "❌ 未启用"
        }

        // SS4 上 Settings 打不开时，给用户可复制的 ADB 兜底命令
        val component = getServiceComponent()
        helpText.text = buildString {
            appendLine("使用说明：")
            appendLine("- 如系统设置页可打开：点“打开辅助功能设置”手动启用")
            appendLine("- 如设置页会崩溃：点“一键启用（ROOT）”")
            appendLine()
            appendLine("ADB 兜底（电脑执行，保留原有 enabled 列表并追加本服务）：")
            appendLine("adb shell settings put secure enabled_accessibility_services \"<原值>:$component\"")
            appendLine("adb shell settings put secure accessibility_enabled 1")
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                appendLine()
                appendLine("组件名：$component")
            }
        }
    }

    private fun openAccessibilitySettings() {
        val intent = Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)
        startActivity(intent)
    }

    private fun getServiceComponent(): String {
        // settings 里 enabled_accessibility_services 常用 flattened component 格式
        return "${packageName}/.CarUIAccessibilityService"
    }

    private fun isAccessibilityServiceEnabled(): Boolean {
        return try {
            val enabledServices = Settings.Secure.getString(
                contentResolver,
                Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES
            ) ?: return false
            enabledServices.split(":").any { it.equals(getServiceComponent(), ignoreCase = true) }
        } catch (_: Throwable) {
            false
        }
    }

    /**
     * 尝试通过 root 直接写 secure settings，绕过 Settings 页面。
     * 适用于车机 userdebug/eng 或已 root 的环境。
     */
    private fun enableAccessibilityServiceByRoot() {
        val component = getServiceComponent()
        val readCmd = "settings get secure enabled_accessibility_services"
        val enableCmd = "settings put secure accessibility_enabled 1"

        // 读取当前 enabled 列表（root 下执行，确保在受限机型也能读到）
        val current = execSuAndGetOutput(readCmd).trim().ifEmpty { "null" }
        val newValue = when {
            current.contains(component, ignoreCase = true) -> current
            current == "null" -> component
            else -> "$current:$component"
        }

        val writeCmd = "settings put secure enabled_accessibility_services \"$newValue\""
        val ok1 = execSu(writeCmd)
        val ok2 = execSu(enableCmd)

        statusText.text = if (ok1 && ok2) {
            "✅ 已通过 ROOT 写入设置\n请稍等系统拉起服务…"
        } else {
            "❌ ROOT 启用失败\n请使用 ADB 兜底命令（见下方）"
        }

        // 立即刷新展示
        updateStatus()
    }

    private fun execSu(cmd: String): Boolean {
        return try {
            val p = Runtime.getRuntime().exec(arrayOf("su", "-c", cmd))
            val code = p.waitFor()
            code == 0
        } catch (_: Throwable) {
            false
        }
    }

    private fun execSuAndGetOutput(cmd: String): String {
        return try {
            val p = Runtime.getRuntime().exec(arrayOf("su", "-c", cmd))
            BufferedReader(InputStreamReader(p.inputStream)).use { it.readText() }
        } catch (_: Throwable) {
            ""
        }
    }
}
