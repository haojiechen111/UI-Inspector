package com.carui.inspector

import com.intellij.openapi.project.Project
import com.intellij.openapi.wm.ToolWindow
import com.intellij.openapi.wm.ToolWindowFactory
import com.intellij.openapi.wm.ex.ToolWindowManagerListener
import com.intellij.ui.content.ContentFactory
import com.intellij.ui.jcef.JBCefApp
import com.intellij.ui.jcef.JBCefBrowser
import com.intellij.ide.plugins.PluginManagerCore
import com.intellij.openapi.extensions.PluginId
import com.intellij.openapi.application.ApplicationManager
import java.awt.BorderLayout
import java.awt.Color
import java.awt.FlowLayout
import java.awt.Font
import javax.swing.BorderFactory
import javax.swing.JButton
import javax.swing.JLabel
import javax.swing.JPanel
import javax.swing.JScrollPane
import javax.swing.JTextArea
import javax.swing.SwingConstants

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

        fun showSwingErrorPanel(title: String, body: String) {
            panel.removeAll()
            panel.add(toolbar, BorderLayout.NORTH)
            val bg = Color(26, 26, 26)
            val errPanel = JPanel(BorderLayout()).apply { background = bg; border = BorderFactory.createEmptyBorder(30, 30, 30, 30) }
            val titleLabel = JLabel("⚠️  $title", SwingConstants.CENTER).apply {
                foreground = Color(239, 68, 68); font = Font("SansSerif", Font.BOLD, 16); border = BorderFactory.createEmptyBorder(0, 0, 12, 0)
            }
            val textArea = JTextArea(body).apply {
                isEditable = false; lineWrap = true; wrapStyleWord = true
                background = Color(38, 38, 38); foreground = Color(200, 200, 200)
                font = Font("Monospaced", Font.PLAIN, 13); border = BorderFactory.createEmptyBorder(12, 12, 12, 12)
            }
            errPanel.add(titleLabel, BorderLayout.NORTH)
            errPanel.add(JScrollPane(textArea).apply { background = bg; border = BorderFactory.createLineBorder(Color(63, 63, 70)) }, BorderLayout.CENTER)
            panel.add(errPanel, BorderLayout.CENTER)
            panel.revalidate()
            panel.repaint()
        }

        fun ensureBrowserRunning() {
            if (browser != null) return

            // ① 检查 JCEF 是否可用
            // 注意：Registry 里的 ide.browser.jcef.enabled 只是软开关，
            // 如果当前 JBR（JetBrains Runtime）本身不含 JCEF 运行时，isSupported() 仍然返回 false。
            // 真正的解决办法是切换到带 JCEF 的 JBR。
            if (!JBCefApp.isSupported()) {
                showSwingErrorPanel(
                    "JCEF 内嵌浏览器不可用",
                    "该插件需要 JCEF（内嵌 Chromium）来渲染 UI。\n" +
                    "当前 Android Studio 使用的 JBR 运行时不包含 JCEF，\n" +
                    "仅开启 Registry 开关无效，需要切换到含 JCEF 的 JBR。\n\n" +
                    "【推荐修复方法】切换 JBR（含 JCEF 版本）：\n" +
                    "1. 打开 Help > Find Action（⌘+Shift+A）\n" +
                    "2. 搜索并打开 \"Choose Boot Java Runtime for the IDE\"\n" +
                    "3. 在列表中选择名称含 \"jcef\" 的版本（如 JetBrains Runtime 17 with JCEF）\n" +
                    "4. 点击 OK，IDE 会自动下载并重启\n\n" +
                    "【备用方法】如果没有 jcef 选项，升级 Android Studio 到最新版：\n" +
                    "https://developer.android.com/studio\n\n" +
                    "【验证】重启后再次打开此插件，若不再显示本提示则修复成功。"
                )
                return
            }

            // ② 创建浏览器（加 try-catch，防止 Mac 上 JCEF 初始化异常导致 panel 完全空白）
            val newBrowser = try {
                JBCefBrowser()
            } catch (e: Exception) {
                showSwingErrorPanel(
                    "内嵌浏览器初始化失败",
                    "JBCefBrowser 初始化时发生异常，插件 UI 无法显示。\n\n" +
                    "错误信息：${e.message ?: e.javaClass.simpleName}\n\n" +
                    "可能原因：\n" +
                    "• Mac 上 GPU 驱动/显示配置异常\n" +
                    "• IDE 版本与插件不兼容\n\n" +
                    "建议：\n" +
                    "1. 尝试重启 Android Studio\n" +
                    "2. 关闭 IDE 后删除 ~/Library/Caches/Google/AndroidStudio*/tmp/jcef_cache\n" +
                    "3. 在 Help > Diagnostic Tools > GPU Diagnostics 查看 GPU 状态"
                )
                return
            }

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
