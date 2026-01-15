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

        // Step 1: Start Python Server
        PythonServerManager.start(pluginPath)

        // Step 2: Create Web Browser View with initial loading state
        val browser = JBCefBrowser()
        browser.loadHTML("""
            <html>
            <body style="background:#1a1a1a; color:white; display:flex; justify-content:center; align-items:center; height:100vh; font-family:sans-serif;">
                <div style="text-align:center;">
                    <div style="border:4px solid #333; border-top:4px solid #3b82f6; border-radius:50%; width:40px; height:40px; animation:spin 1s linear infinite; margin: 0 auto 20px;"></div>
                    <h2 style="margin:0;">正在启动 Car UI 服务...</h2>
                    <p style="color:#888; margin-top:10px;">首次运行可能需要几秒钟加载环境</p>
                </div>
                <style>@keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }</style>
            </body>
            </html>
        """.trimIndent())
        
        val panel = JPanel(BorderLayout())
        panel.add(browser.component, BorderLayout.CENTER)

        // Step 3: Register Content
        val content = ContentFactory.getInstance().createContent(panel, "", false)
        toolWindow.contentManager.addContent(content)

        // Step 4: Wait for server in background and reload
        Thread {
            var attempts = 0
            while (attempts < 20) {
                if (PythonServerManager.isServerRunning()) {
                    javax.swing.SwingUtilities.invokeLater {
                        val serverURL = PythonServerManager.getServerURL()
                        browser.loadURL("$serverURL/static/index.html")
                    }
                    break
                }
                Thread.sleep(1000)
                attempts++
                if (attempts == 20) {
                    javax.swing.SwingUtilities.invokeLater {
                        browser.loadHTML("<div style='background:#1a1a1a; color:red; display:flex; justify-content:center; align-items:center; height:100vh; text-align:center;'><h2>服务启动超时</h2><p>请检查系统是否安装了 python3 以及 adbutils、fastapi、uvicorn 等依赖。</p></div>")
                    }
                }
            }
        }.start()
    }
}
