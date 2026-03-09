package com.carui.inspector

import com.intellij.openapi.project.Project
import com.intellij.openapi.wm.ToolWindow
import com.intellij.openapi.wm.ToolWindowFactory
import com.intellij.openapi.wm.ex.ToolWindowManagerListener
import com.intellij.ui.content.ContentFactory
import com.intellij.ui.jcef.JBCefBrowser
import com.intellij.ide.plugins.PluginManagerCore
import com.intellij.openapi.extensions.PluginId
import com.intellij.openapi.application.ApplicationManager
import java.awt.BorderLayout
import java.awt.FlowLayout
import javax.swing.JButton
import javax.swing.JPanel

class CarUiToolWindowFactory : ToolWindowFactory {

    override fun createToolWindowContent(project: Project, toolWindow: ToolWindow) {
        val plugin = PluginManagerCore.getPlugin(PluginId.getId("com.carui.inspector"))
        val pluginPath = plugin?.pluginPath?.toString() ?: ""

        // ToolWindow 关闭默认只是“隐藏”，不会销毁内容；所以要自己做 hard reset。
        val panel = JPanel(BorderLayout())

        // 顶部工具条：放一些“外部辅助工具”按钮，不影响现有 WebView。
        val toolbar = JPanel(FlowLayout(FlowLayout.LEFT, 8, 6))
        val btnTextVisibleSpeak = JButton("启动可见即可说模拟")
        btnTextVisibleSpeak.toolTipText = "启动 Text Visible Speak PC GUI（需要 python3+tkinter + adb；设备端需集成对应 Service）"
        btnTextVisibleSpeak.addActionListener {
            TextVisibleSpeakLauncher.launch(project, pluginPath)
        }
        toolbar.add(btnTextVisibleSpeak)
        panel.add(toolbar, BorderLayout.NORTH)

        val loadingHtml = """
            <html>
            <body style=\"background:#1a1a1a; color:white; display:flex; justify-content:center; align-items:center; height:100vh; font-family:sans-serif;\">
                <div style=\"text-align:center;\">
                    <div style=\"border:4px solid #333; border-top:4px solid #3b82f6; border-radius:50%; width:40px; height:40px; animation:spin 1s linear infinite; margin: 0 auto 20px;\"></div>
                    <h2 style=\"margin:0;\">正在检查环境...</h2>
                    <p style=\"color:#888; margin-top:10px;\">正在检查Python环境和依赖包</p>
                </div>
                <style>@keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }</style>
            </body>
            </html>
        """.trimIndent()

        var browser: JBCefBrowser? = null

        fun attachBrowser(newBrowser: JBCefBrowser) {
            panel.removeAll()
            panel.add(toolbar, BorderLayout.NORTH)
            panel.add(newBrowser.component, BorderLayout.CENTER)
            panel.revalidate()
            panel.repaint()
        }

        fun startServerAndLoadPage(targetBrowser: JBCefBrowser) {
            // Step 3: Check dependencies and start server in background
            Thread {
                // 首先检查依赖
                val checkResult = PythonServerManager.checkDependencies(pluginPath)

                if (!checkResult.success) {
                    // 依赖检查失败，显示详细错误
                    javax.swing.SwingUtilities.invokeLater {
                        val pythonVersion = checkResult.pythonVersionFromCmd
                            ?: checkResult.pythonVersion
                            ?: "unknown"

                        val errorHtml = buildErrorHtml(
                            title = "环境检查失败",
                            message = checkResult.errorMessage ?: "未知错误",
                            details = buildString {
                                append("<div style='display:grid; gap:8px;'>")
                                append("<div style='background:#111827; border:1px solid #374151; border-radius:8px; padding:10px 12px;'>")
                                append("<strong style='color:#93c5fd;'>环境摘要</strong><br>")
                                append("Python: <code>${escapeHtml(pythonVersion)}</code><br>")
                                append("PIP: ${if (checkResult.pipOk) "✅ 可用" else "❌ 不可用"}")
                                if (checkResult.pipMethods.isNotEmpty()) {
                                    append("（${escapeHtml(checkResult.pipMethods.joinToString(", "))}）")
                                }
                                append("<br>")
                                append("ADB: ${if (checkResult.adbOk) "✅ 可用" else "❌ 不可用"}")
                                checkResult.adbVersion?.let {
                                    append("（${escapeHtml(it)}）")
                                }
                                checkResult.adbPath?.let {
                                    append("<br>ADB路径: <code>${escapeHtml(it)}</code>")
                                }
                                append("</div>")

                                if (checkResult.recommendations.isNotEmpty()) {
                                    append("<div style='background:#1f2937; border:1px solid #374151; border-radius:8px; padding:10px 12px;'>")
                                    append("<strong style='color:#fcd34d;'>建议操作</strong><ol style='margin:8px 0 0 18px; padding:0;'>")
                                    checkResult.recommendations.forEach { tip ->
                                        append("<li>${escapeHtml(tip)}</li>")
                                    }
                                    append("</ol></div>")
                                }

                                checkResult.installAllCmd?.let { cmd ->
                                    append("<div style='background:#2d2d2d; padding:12px; border-radius:8px; border:1px solid #3f3f46;'>")
                                    append("<strong>一键安装 Python 依赖：</strong>")
                                    append("<code style='display:block; color:#10b981; margin-top:8px; font-size:13px;'>${escapeHtml(cmd)}</code>")
                                    append("</div>")
                                }

                                if (checkResult.missingPackagesWithCmd.isNotEmpty()) {
                                    append("<div style='background:#2d2d2d; padding:12px; border-radius:8px; border:1px solid #3f3f46;'>")
                                    append("<strong>逐条安装命令：</strong>")
                                    checkResult.missingPackagesWithCmd.forEach { (_, cmd) ->
                                        append("<code style='display:block; color:#10b981; margin:6px 0; font-size:13px;'>${escapeHtml(cmd)}</code>")
                                    }
                                    append("</div>")
                                }

                                if (!checkResult.adbOk) {
                                    append("<div style='background:#422006; border-left:3px solid #f59e0b; padding:12px; margin-top:6px;'>")
                                    append("<strong style='color:#fbbf24;'>ADB 未就绪</strong><br>")
                                    append("<span style='color:#fde68a;'>请安装 Android Platform-Tools 并加入 PATH。</span><br>")
                                    when (checkResult.osType) {
                                        "Darwin" -> append("<code style='display:block; color:#10b981; margin-top:8px;'>brew install android-platform-tools</code>")
                                        "Windows" -> append("<span style='color:#fde68a;'>可在 Android Studio > SDK Manager 安装 Platform-Tools。</span>")
                                        else -> append("<code style='display:block; color:#10b981; margin-top:8px;'>sudo apt update && sudo apt install android-sdk-platform-tools</code>")
                                    }

                                    if (checkResult.adbCandidates.isNotEmpty()) {
                                        append("<br><small style='color:#fcd34d;'>检测到可能的 adb 路径：${escapeHtml(checkResult.adbCandidates.joinToString(", "))}</small>")
                                    }
                                    append("</div>")
                                }

                                append("</div>")
                            },
                            canRetry = true
                        )
                        targetBrowser.loadHTML(errorHtml)
                    }
                    return@Thread
                }

                // 依赖检查通过，更新状态并启动服务
                javax.swing.SwingUtilities.invokeLater {
                    targetBrowser.loadHTML("""
                        <html>
                        <body style="background:#1a1a1a; color:white; display:flex; justify-content:center; align-items:center; height:100vh; font-family:sans-serif;">
                            <div style="text-align:center;">
                                <div style="border:4px solid #333; border-top:4px solid #10b981; border-radius:50%; width:40px; height:40px; animation:spin 1s linear infinite; margin: 0 auto 20px;"></div>
                                <h2 style="margin:0;">正在启动服务...</h2>
                                <p style="color:#888; margin-top:10px;">Python ${checkResult.pythonVersion ?: "3.x"} | 依赖已就绪</p>
                            </div>
                            <style>@keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }</style>
                        </body>
                        </html>
                    """.trimIndent())
                }

                // 启动Python服务
                val startError = PythonServerManager.start(pluginPath)
                if (startError != null) {
                    // 启动失败
                    javax.swing.SwingUtilities.invokeLater {
                        val errorHtml = buildErrorHtml(
                            title = "服务启动失败",
                            message = startError,
                            details = "请检查日志文件获取详细信息",
                            canRetry = true
                        )
                        targetBrowser.loadHTML(errorHtml)
                    }
                    return@Thread
                }

                // 等待服务启动
                var attempts = 0
                val maxAttempts = 30 // 增加到30秒超时
                while (attempts < maxAttempts) {
                    if (PythonServerManager.isServerRunning()) {
                        // 服务启动成功
                        javax.swing.SwingUtilities.invokeLater {
                            val serverURL = PythonServerManager.getServerURL()
                            val timestamp = System.currentTimeMillis()
                            targetBrowser.loadURL("$serverURL/static/index.html?_t=$timestamp")
                        }
                        break
                    }

                    Thread.sleep(1000)
                    attempts++

                    // 显示进度
                    if (attempts % 5 == 0) {
                        javax.swing.SwingUtilities.invokeLater {
                            targetBrowser.loadHTML("""
                                <html>
                                <body style="background:#1a1a1a; color:white; display:flex; justify-content:center; align-items:center; height:100vh; font-family:sans-serif;">
                                    <div style="text-align:center;">
                                        <div style="border:4px solid #333; border-top:4px solid #3b82f6; border-radius:50%; width:40px; height:40px; animation:spin 1s linear infinite; margin: 0 auto 20px;"></div>
                                        <h2 style="margin:0;">等待服务响应...</h2>
                                        <p style="color:#888; margin-top:10px;">已等待 ${attempts} 秒 / $maxAttempts 秒</p>
                                    </div>
                                    <style>@keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }</style>
                                </body>
                                </html>
                            """.trimIndent())
                        }
                    }

                    if (attempts == maxAttempts) {
                        // 超时
                        val serverLog = PythonServerManager.getServerLog()
                        javax.swing.SwingUtilities.invokeLater {
                            val errorHtml = buildErrorHtml(
                                title = "服务启动超时",
                                message = "服务在${maxAttempts}秒内未能响应",
                                details = buildString {
                                    append("最近的服务日志：<br>")
                                    append("<pre style='background:#2d2d2d; padding:12px; border-radius:4px; font-size:11px; text-align:left; max-height:300px; overflow-y:auto; color:#aaa;'>")
                                    append(serverLog.replace("<", "&lt;").replace(">", "&gt;"))
                                    append("</pre>")
                                },
                                canRetry = true
                            )
                            targetBrowser.loadHTML(errorHtml)
                        }
                    }
                }
            }.start()
        }

        fun ensureBrowserRunning() {
            if (browser != null) return
            val newBrowser = JBCefBrowser()
            newBrowser.loadHTML(loadingHtml)
            browser = newBrowser
            attachBrowser(newBrowser)
            startServerAndLoadPage(newBrowser)
        }

        // Step 1: Create Web Browser View with initial loading state
        ensureBrowserRunning()

        // Step 2: Register Content
        val content = ContentFactory.getInstance().createContent(panel, "", false)
        toolWindow.contentManager.addContent(content)

        // Step 2.5: “瞬间杀死一切” - 当 ToolWindow 被隐藏/关闭时，彻底销毁浏览器 + 强杀 Python 服务
        // 说明：IDE 的 ToolWindow 关闭默认只是隐藏，内容不会自动 dispose；所以你看到“再次打开还是原页面/原数据”。
        // 我们在 visible=false 时做 hard reset。
        val connection = project.messageBus.connect(content)
        connection.subscribe(ToolWindowManagerListener.TOPIC, object : ToolWindowManagerListener {
            override fun stateChanged(toolWindowManager: com.intellij.openapi.wm.ToolWindowManager) {
                // 只关心本 ToolWindow
                val tw = toolWindowManager.getToolWindow(toolWindow.id) ?: return
                if (tw !== toolWindow) return

                if (!toolWindow.isVisible) {
                    // 1) kill server now
                    PythonServerManager.hardStop(clearPortFile = true, clearRestartFlag = true)

                    // 2) dispose browser (release page memory/caches)
                    val b = browser
                    browser = null
                    try {
                        b?.dispose()
                    } catch (_: Exception) {
                    }

                    // 3) UI 上清空旧的 component（避免 re-show 时仍显示旧页面）
                    ApplicationManager.getApplication().invokeLater {
                        try {
                            panel.removeAll()
                            panel.add(toolbar, BorderLayout.NORTH)
                            panel.revalidate()
                            panel.repaint()
                        } catch (_: Exception) {
                        }
                    }
                } else {
                    // ToolWindow 再次显示：重新创建 Browser + 重启服务 + 重新加载页面
                    ApplicationManager.getApplication().invokeLater {
                        ensureBrowserRunning()
                    }
                }
            }
        })
    }

    private fun escapeHtml(raw: String): String {
        return raw
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("\"", "&quot;")
    }
    
    private fun buildErrorHtml(title: String, message: String, details: String, canRetry: Boolean): String {
        return """
            <html>
            <head>
                <style>
                    body {
                        background: #1a1a1a;
                        color: white;
                        display: flex;
                        justify-content: center;
                        align-items: center;
                        height: 100vh;
                        font-family: sans-serif;
                        margin: 0;
                        padding: 20px;
                    }
                    .error-container {
                        max-width: 600px;
                        text-align: center;
                    }
                    .error-icon {
                        font-size: 48px;
                        margin-bottom: 20px;
                    }
                    h2 {
                        color: #ef4444;
                        margin: 0 0 15px 0;
                    }
                    .message {
                        color: #f87171;
                        margin-bottom: 20px;
                        font-size: 16px;
                    }
                    .details {
                        background: #262626;
                        padding: 20px;
                        border-radius: 8px;
                        margin: 20px 0;
                        text-align: left;
                        font-size: 14px;
                        line-height: 1.6;
                    }
                    code {
                        font-family: 'Consolas', 'Monaco', monospace;
                    }
                    .actions {
                        margin-top: 30px;
                    }
                    .btn {
                        background: #3b82f6;
                        color: white;
                        border: none;
                        padding: 10px 20px;
                        border-radius: 6px;
                        font-size: 14px;
                        cursor: pointer;
                        margin: 0 5px;
                    }
                    .btn:hover {
                        background: #2563eb;
                    }
                </style>
            </head>
            <body>
                <div class="error-container">
                    <div class="error-icon">⚠️</div>
                    <h2>$title</h2>
                    <div class="message">$message</div>
                    ${if (details.isNotEmpty()) "<div class='details'>$details</div>" else ""}
                    ${if (canRetry) """
                        <div class="actions">
                            <p style="color:#888; font-size:13px;">请按照上述说明安装依赖后，重新打开此工具窗口</p>
                        </div>
                    """ else ""}
                </div>
            </body>
            </html>
        """.trimIndent()
    }
}
