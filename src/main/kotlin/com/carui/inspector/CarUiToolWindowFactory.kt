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

        // Step 2: Create Web Browser View
        val browser = JBCefBrowser("http://127.0.0.1:8000/static/index.html")
        
        val panel = JPanel(BorderLayout())
        panel.add(browser.component, BorderLayout.CENTER)

        // Step 3: Register Content
        val content = ContentFactory.getInstance().createContent(panel, "", false)
        toolWindow.contentManager.addContent(content)
        
        // Ensure server stops when plugin/project closes if desired
        // (Usually handled by a project listener or IDE close listener)
    }
}
