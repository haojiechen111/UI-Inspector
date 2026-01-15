package com.carui.inspector

import com.intellij.openapi.project.Project
import com.intellij.openapi.wm.ToolWindow
import com.intellij.openapi.wm.ToolWindowFactory
import com.intellij.ui.content.ContentFactory
import com.intellij.ui.jcef.JBCefBrowser
import com.intellij.ide.plugins.PluginManagerCore
import com.intellij.openapi.extensions.PluginId
import java.awt.BorderLayout
import javax.swing.JPanel

class CarUiToolWindowFactory : ToolWindowFactory {

    override fun createToolWindowContent(project: Project, toolWindow: ToolWindow) {
        val plugin = PluginManagerCore.getPlugin(PluginId.getId("com.carui.inspector"))
        val pluginPath = plugin?.pluginPath?.toString() ?: ""

        // Step 1: Create Web Browser View with initial loading state
        val browser = JBCefBrowser()
        browser.loadHTML("""
            <html>
            <body style="background:#1a1a1a; color:white; display:flex; justify-content:center; align-items:center; height:100vh; font-family:sans-serif;">
                <div style="text-align:center;">
                    <div style="border:4px solid #333; border-top:4px solid #3b82f6; border-radius:50%; width:40px; height:40px; animation:spin 1s linear infinite; margin: 0 auto 20px;"></div>
                    <h2 style="margin:0;">正在检查环境...</h2>
                    <p style="color:#888; margin-top:10px;">正在检查Python环境和依赖包</p>
                </div>
                <style>@keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }</style>
            </body>
            </html>
        """.trimIndent())
        
        val panel = JPanel(BorderLayout())
        panel.add(browser.component, BorderLayout.CENTER)

        // Step 2: Register Content
        val content = ContentFactory.getInstance().createContent(panel, "", false)
        toolWindow.contentManager.addContent(content)

        // Step 3: Check dependencies and start server in background
        Thread {
            // 首先检查依赖
            val checkResult = PythonServerManager.checkDependencies(pluginPath)
            
            if (!checkResult.success) {
                // 依赖检查失败，显示详细错误
                javax.swing.SwingUtilities.invokeLater {
                    val errorHtml = buildErrorHtml(
                        title = "环境检查失败",
                        message = checkResult.errorMessage ?: "未知错误",
                        details = buildString {
                            // 显示Python版本信息
                            checkResult.pythonVersionFromCmd?.let { 
                                append("<strong>Python版本:</strong> $it<br>")
                            } ?: checkResult.pythonVersion?.let {
                                append("<strong>Python版本:</strong> $it<br>")
                            }
                            
                            // 显示操作系统信息
                            checkResult.osType?.let {
                                append("<strong>操作系统:</strong> $it<br>")
                            }
                            
                            // 如果没有检测到缺失的包，但检查失败，显示手动安装所有依赖的命令
                            if (checkResult.missingPackages.isEmpty() && !checkResult.success) {
                                append("<br><div style='background:#422006; border-left:3px solid #f59e0b; padding:12px; margin:10px 0;'>")
                                append("<strong style='color:#fbbf24;'>⚠️ 无法确定缺少的具体依赖</strong><br>")
                                append("<span style='color:#fcd34d; font-size:13px;'>")
                                append("建议手动安装所有依赖包：")
                                append("</span>")
                                append("</div>")
                                
                                append("<div style='background:#2d2d2d; padding:12px; border-radius:4px; margin-top:10px; text-align:left;'>")
                                append("<strong>推荐安装命令（请在终端中执行）：</strong><br>")
                                
                                when (checkResult.osType) {
                                    "Darwin" -> {
                                        // macOS
                                        append("<code style='display:block; color:#10b981; margin:8px 0; font-size:13px;'>")
                                        append("python3 -m pip install fastapi uvicorn adbutils pillow urllib3")
                                        append("</code>")
                                        append("<br><small style='color:#888;'>")
                                        append("如果上述命令失败，请尝试：<br>")
                                        append("<code style='color:#60a5fa;'>python -m pip install fastapi uvicorn adbutils pillow urllib3</code><br>")
                                        append("或者：<code style='color:#60a5fa;'>pip3 install fastapi uvicorn adbutils pillow urllib3</code>")
                                        append("</small>")
                                    }
                                    "Windows" -> {
                                        append("<code style='display:block; color:#10b981; margin:8px 0; font-size:13px;'>")
                                        append("python -m pip install fastapi uvicorn adbutils pillow urllib3")
                                        append("</code>")
                                    }
                                    else -> {
                                        // Linux
                                        append("<code style='display:block; color:#10b981; margin:8px 0; font-size:13px;'>")
                                        append("python3 -m pip install fastapi uvicorn adbutils pillow urllib3")
                                        append("</code>")
                                    }
                                }
                                append("</div>")
                            } else if (checkResult.missingPackages.isNotEmpty()) {
                                append("<br><strong style='color:#ef4444;'>缺少的依赖包：</strong><br>")
                                checkResult.missingPackages.forEach {
                                    append("• $it<br>")
                                }
                                
                                // 如果有每个包的安装命令，优先显示
                                if (checkResult.missingPackagesWithCmd.isNotEmpty()) {
                                    append("<br><strong>推荐安装方式（每个命令单独执行）：</strong><br>")
                                    append("<div style='background:#2d2d2d; padding:12px; border-radius:4px; margin-top:10px; text-align:left;'>")
                                    
                                    // 如果是 Mac 系统，添加特别说明
                                    if (checkResult.osType == "Darwin") {
                                        append("<div style='background:#422006; border-left:3px solid #f59e0b; padding:10px; margin-bottom:12px;'>")
                                        append("<strong style='color:#fbbf24;'>⚠️ macOS 用户注意：</strong><br>")
                                        append("<span style='color:#fcd34d; font-size:13px;'>")
                                        append("Mac系统可能有多个Python环境（系统自带、Homebrew、pyenv等）。<br>")
                                        append("请使用下面的 <code style='background:#1a1a1a; padding:2px 4px;'>python -m pip</code> 命令，")
                                        append("这样可以确保安装到IDE使用的Python环境中。")
                                        append("</span>")
                                        append("</div>")
                                    }
                                    
                                    checkResult.missingPackagesWithCmd.forEach { (pkg, cmd) ->
                                        append("<code style='display:block; color:#10b981; margin:5px 0; font-size:13px;'>")
                                        append(cmd.replace("<", "&lt;").replace(">", "&gt;"))
                                        append("</code>")
                                    }
                                    append("</div>")
                                    
                                    // 显示可用的pip方法
                                    if (checkResult.pipMethods.isNotEmpty()) {
                                        append("<br><small style='color:#888;'>")
                                        append("可用的pip命令: ${checkResult.pipMethods.joinToString(", ")}")
                                        append("</small>")
                                    }
                                } else {
                                    // 降级到通用安装命令
                                    append("<br><strong>安装命令：</strong><br>")
                                    append("<code style='background:#2d2d2d; padding:8px; border-radius:4px; display:block; margin-top:10px;'>")
                                    val pipCmd = checkResult.pipCmd ?: "pip3"
                                    append("$pipCmd install ${checkResult.missingPackages.joinToString(" ")}")
                                    append("</code>")
                                }
                            }
                        },
                        canRetry = true
                    )
                    browser.loadHTML(errorHtml)
                }
                return@Thread
            }
            
            // 依赖检查通过，更新状态并启动服务
            javax.swing.SwingUtilities.invokeLater {
                browser.loadHTML("""
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
                    browser.loadHTML(errorHtml)
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
                        browser.loadURL("$serverURL/static/index.html?_t=$timestamp")
                    }
                    break
                }
                
                Thread.sleep(1000)
                attempts++
                
                // 显示进度
                if (attempts % 5 == 0) {
                    javax.swing.SwingUtilities.invokeLater {
                        browser.loadHTML("""
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
                        browser.loadHTML(errorHtml)
                    }
                }
            }
        }.start()
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
