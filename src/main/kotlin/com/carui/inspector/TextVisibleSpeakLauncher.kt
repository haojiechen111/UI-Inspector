package com.carui.inspector

import com.intellij.openapi.diagnostic.Logger
import com.intellij.openapi.project.Project
import com.intellij.openapi.ui.Messages
import java.io.File
import java.nio.charset.Charset

/**
 * 在 IDE 插件里"一键启动" Text Visible Speak（外部 Python GUI）。
 *
 * 【重要约束 / Contract】
 * 1) 该工具只能通过"手动运行脚本"或"点击插件按钮"启动；禁止任何自动/常驻启动。
 * 2) 该工具必须与车端 voice 项目线上逻辑强隔离：不启动时不产生任何副作用。
 *
 * 约定：脚本被打包在 <plugin>/server/tools/text_visible_speak_pc.py
 */
object TextVisibleSpeakLauncher {
    private val LOG = Logger.getInstance(TextVisibleSpeakLauncher::class.java)

    private const val REL_SCRIPT_PATH = "server/tools/text_visible_speak_pc.py"

    private val osName: String get() = System.getProperty("os.name", "").lowercase()
    private val isMac: Boolean get() = osName.contains("mac")
    private val isLinux: Boolean get() = osName.contains("linux") || osName.contains("nix")
    private val isWindows: Boolean get() = osName.contains("win")

    /**
     * @param pluginPath 插件安装目录（PluginManagerCore.getPlugin(...).pluginPath）
     */
    fun launch(project: Project?, pluginPath: String) {
        try {
            val scriptFile = File(pluginPath, REL_SCRIPT_PATH)
            if (!scriptFile.exists()) {
                Messages.showErrorDialog(
                    project,
                    buildScriptNotFoundHtml(scriptFile.absolutePath),
                    "可见即可说 — 脚本文件未找到"
                )
                return
            }

            if (!whichOk("adb")) {
                Messages.showErrorDialog(
                    project,
                    buildAdbGuideHtml(),
                    "可见即可说 — 缺少 adb"
                )
                return
            }

            val pythonCmd = findPythonCmd()
            if (pythonCmd.isEmpty()) {
                Messages.showErrorDialog(
                    project,
                    buildPythonGuideHtml(),
                    "可见即可说 — 缺少 Python 3"
                )
                return
            }

            // tkinter 自检（避免用户点了没反应）
            val tkCheck = runAndCapture(listOf(pythonCmd, "-c", "import tkinter"), timeoutMs = 5_000)
            if (tkCheck.exitCode != 0) {
                Messages.showErrorDialog(
                    project,
                    buildTkinterGuideHtml(tkCheck.output),
                    "可见即可说 — tkinter 不可用"
                )
                return
            }

            val cmd = listOf(pythonCmd, scriptFile.absolutePath)
            LOG.info("Launching TextVisibleSpeak: ${cmd.joinToString(" ")}")

            // 重要：不要阻塞 UI 线程
            Thread {
                try {
                    ProcessBuilder(cmd)
                        .redirectErrorStream(true)
                        .start()
                } catch (e: Exception) {
                    LOG.warn("Start process failed", e)
                    Messages.showErrorDialog(
                        project,
                        "<html><b>进程启动失败</b><br><br>" +
                            "错误：${escHtml(e.message ?: e.javaClass.simpleName)}<br>" +
                            "命令：<code>${escHtml(cmd.joinToString(" "))}</code></html>",
                        "可见即可说 — 启动失败"
                    )
                }
            }.start()

            Messages.showInfoMessage(
                project,
                buildSuccessHtml(),
                "可见即可说 — 已启动 ✓"
            )
        } catch (t: Throwable) {
            LOG.warn("launch failed", t)
            Messages.showErrorDialog(
                project,
                "<html><b>启动失败</b><br><br>${escHtml(t.message ?: t.javaClass.simpleName)}</html>",
                "可见即可说 — 错误"
            )
        }
    }

    // ======== HTML 提示构建 ========

    private fun escHtml(s: String) = s
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")

    private fun buildScriptNotFoundHtml(path: String): String = """
        <html>
        <b>未找到脚本文件：</b><br>
        <code>${escHtml(path)}</code><br><br>
        <b>可能原因：</b><br>
        &nbsp;• 插件未正确构建，<code>server/tools/</code> 目录未被打包进插件<br>
        &nbsp;• 插件路径为空（PluginManagerCore 未能识别本插件 ID）<br><br>
        <b>解决方法：</b><br>
        &nbsp;1. 重新执行 <code>./gradlew buildPlugin</code> 构建插件<br>
        &nbsp;2. 在 Settings → Plugins → Install plugin from disk 重新安装<br>
        &nbsp;3. 重启 Android Studio 后再试
        </html>
    """.trimIndent()

    private fun buildAdbGuideHtml(): String {
        val installBlock = when {
            isMac -> """
                <b>方法 1（推荐）— Homebrew：</b><br>
                &nbsp;&nbsp;<code>brew install android-platform-tools</code><br><br>
                <b>方法 2 — Android Studio SDK Manager：</b><br>
                &nbsp;&nbsp;SDK Manager → SDK Tools → Android SDK Platform-Tools → 安装<br>
                &nbsp;&nbsp;然后将以下路径加入 PATH（~/.zshrc 或 ~/.bash_profile）：<br>
                &nbsp;&nbsp;<code>export PATH="${'$'}PATH:${'$'}HOME/Library/Android/sdk/platform-tools"</code>
            """.trimIndent()
            isLinux -> """
                <b>Ubuntu / Debian：</b><br>
                &nbsp;&nbsp;<code>sudo apt update &amp;&amp; sudo apt install adb</code><br><br>
                <b>或通过 Android Studio SDK Manager 安装后，将路径加入 PATH：</b><br>
                &nbsp;&nbsp;<code>export PATH="${'$'}PATH:${'$'}HOME/Android/Sdk/platform-tools"</code><br>
                &nbsp;&nbsp;（添加到 ~/.bashrc 或 ~/.profile，然后 source 或重启终端）
            """.trimIndent()
            isWindows -> """
                <b>通过 Android Studio SDK Manager：</b><br>
                &nbsp;&nbsp;SDK Manager → SDK Tools → Android SDK Platform-Tools → 安装<br><br>
                <b>安装后添加 PATH：</b><br>
                &nbsp;&nbsp;系统属性 → 高级 → 环境变量 → Path → 添加 platform-tools 路径<br>
                &nbsp;&nbsp;（通常为：<code>%LOCALAPPDATA%\Android\Sdk\platform-tools</code>）
            """.trimIndent()
            else -> """
                <b>请安装 Android SDK Platform-Tools 并加入 PATH</b>
            """.trimIndent()
        }

        return """
            <html>
            <b>未在 PATH 中找到 adb</b><br><br>
            该工具需要 adb 与设备建立端口转发通信。<br><br>
            $installBlock<br><br>
            ⚠️ 安装完成后，<b>请重启 Android Studio</b> 使 PATH 生效，然后再次点击按钮。
            </html>
        """.trimIndent()
    }

    private fun buildPythonGuideHtml(): String {
        val installBlock = when {
            isMac -> """
                <b>方法 1 — Homebrew（推荐）：</b><br>
                &nbsp;&nbsp;<code>brew install python3</code><br><br>
                <b>方法 2 — 官方安装包：</b><br>
                &nbsp;&nbsp;访问 <a href="https://www.python.org/downloads/">python.org/downloads</a> 下载 macOS 安装器<br>
                &nbsp;&nbsp;（安装后自动配置 PATH）
            """.trimIndent()
            isLinux -> """
                <b>Ubuntu / Debian：</b><br>
                &nbsp;&nbsp;<code>sudo apt update &amp;&amp; sudo apt install python3 python3-pip</code><br><br>
                <b>验证安装：</b><br>
                &nbsp;&nbsp;<code>python3 --version</code>
            """.trimIndent()
            isWindows -> """
                <b>官方安装包：</b><br>
                &nbsp;&nbsp;访问 <a href="https://www.python.org/downloads/windows/">python.org/downloads/windows</a><br>
                &nbsp;&nbsp;⚠️ 安装时务必勾选 <b>"Add Python to PATH"</b>
            """.trimIndent()
            else -> """
                <b>请安装 Python 3.8+</b>：访问 <a href="https://python.org">python.org</a>
            """.trimIndent()
        }

        return """
            <html>
            <b>未找到 python3 / python</b><br><br>
            该工具使用 Python 3 运行桌面 GUI 脚本（仅用标准库，无需额外 pip 安装）。<br><br>
            $installBlock<br><br>
            安装完成后，<b>请重启 Android Studio</b> 使 PATH 生效，然后再次点击按钮。
            </html>
        """.trimIndent()
    }

    private fun buildTkinterGuideHtml(rawOutput: String): String {
        val installBlock = when {
            isMac -> """
                <b>macOS：</b><br>
                &nbsp;• 系统自带 Python：通常内置 tkinter，无需处理<br>
                &nbsp;• Homebrew Python 3.x：<code>brew install python-tk@3.x</code>（替换 3.x 为你的版本）<br>
                &nbsp;• 官方 python.org 安装包：已内置 tkinter，如遇问题请重新下载安装
            """.trimIndent()
            isLinux -> """
                <b>Ubuntu / Debian：</b><br>
                &nbsp;&nbsp;<code>sudo apt update &amp;&amp; sudo apt install python3-tk</code><br><br>
                <b>其他发行版（Fedora/CentOS）：</b><br>
                &nbsp;&nbsp;<code>sudo dnf install python3-tkinter</code>
            """.trimIndent()
            isWindows -> """
                <b>Windows：</b><br>
                &nbsp;重新运行 Python 安装器（修改/修复安装），<br>
                &nbsp;确保在可选组件中勾选 <b>tcl/tk and IDLE</b>
            """.trimIndent()
            else -> "<b>请为当前 Python 版本安装 tkinter 支持包</b>"
        }

        val outputSnippet = rawOutput.take(200).let { escHtml(it) }

        return """
            <html>
            <b>Python tkinter 不可用</b><br><br>
            该工具用 tkinter 构建桌面 GUI，但当前 Python 环境缺少 tkinter 支持。<br><br>
            <b>修复方法：</b><br>
            $installBlock<br><br>
            修复后<b>无需重启 IDE</b>，直接再次点击按钮即可。<br><br>
            <small><font color="gray">诊断输出：${outputSnippet}</font></small>
            </html>
        """.trimIndent()
    }

    private fun buildSuccessHtml(): String = """
        <html>
        GUI 窗口已在后台启动，即将弹出。<br><br>
        <b>使用前请确认：</b><br>
        &nbsp;✅ 设备已通过 USB 或 <code>adb connect</code> 连接<br>
        &nbsp;✅ 设备端已集成 <code>TextVisibleSpeakRemoteService</code><br>
        &nbsp;✅ 在 GUI 中刷新并选择正确的 adb 设备<br><br>
        <b>基本操作：</b><br>
        &nbsp;1. 选择设备 → 选择屏幕（主驾/副驾/后排）<br>
        &nbsp;2. 点击「拉取可执行列表」获取当前页面可执行指令<br>
        &nbsp;3. 输入文本后按 <b>Enter</b> 或点击「发送」<br>
        &nbsp;4. 也可双击列表项直接发送
        </html>
    """.trimIndent()

    // ======== 环境检测（与 PythonServerManager 保持一致）========

    /**
     * 构建带 Mac 常用路径的 ProcessBuilder。
     * 从 Finder/Launchpad 启动的 IDE PATH 通常只含系统路径，缺 Homebrew/SDK 等。
     */
    private fun buildMacAwareProcessBuilder(vararg command: String): ProcessBuilder {
        val pb = ProcessBuilder(*command)
        val env = pb.environment()
        val currentPath = env["PATH"] ?: ""
        val home = System.getProperty("user.home") ?: ""
        val extraPaths = listOf(
            // pyenv（用户主动管理的 Python 版本，优先级最高）
            "$home/.pyenv/shims",
            "$home/.pyenv/bin",
            // Homebrew - Apple Silicon (M1/M2/M3)
            "/opt/homebrew/bin",
            "/opt/homebrew/sbin",
            // python.org 官方安装包 Framework 路径（新版优先）
            "/Library/Frameworks/Python.framework/Versions/3.13/bin",
            "/Library/Frameworks/Python.framework/Versions/3.12/bin",
            "/Library/Frameworks/Python.framework/Versions/3.11/bin",
            "/Library/Frameworks/Python.framework/Versions/3.10/bin",
            "/Library/Frameworks/Python.framework/Versions/3.9/bin",
            // Homebrew - Intel Mac / python.org 软链
            "/usr/local/bin",
            // conda / miniforge
            "$home/miniforge3/bin",
            "$home/miniconda3/bin",
            "$home/anaconda3/bin",
            // Android SDK
            "$home/Library/Android/sdk/platform-tools",
            "$home/Android/Sdk/platform-tools",
            "/Applications/Android Studio.app/Contents/platform-tools",
            // 系统 Python（兜底）
            "/usr/bin",
        )
        val merged = (extraPaths + currentPath.split(File.pathSeparator))
            .filter { it.isNotBlank() }.distinct().joinToString(File.pathSeparator)
        env["PATH"] = merged
        return pb
    }

    private fun whichOk(bin: String): Boolean {
        return try {
            if (isWindows) {
                val r = runAndCapture(listOf("cmd", "/c", "where", bin))
                r.exitCode == 0 && r.output.isNotBlank()
            } else {
                val home = System.getProperty("user.home") ?: ""
                // 直接检查已知路径，避免依赖 shell profile
                val searchPaths = listOf(
                    "$home/.pyenv/shims/$bin",
                    "/opt/homebrew/bin/$bin",
                    "/Library/Frameworks/Python.framework/Versions/3.13/bin/$bin",
                    "/Library/Frameworks/Python.framework/Versions/3.12/bin/$bin",
                    "/Library/Frameworks/Python.framework/Versions/3.11/bin/$bin",
                    "/Library/Frameworks/Python.framework/Versions/3.10/bin/$bin",
                    "/Library/Frameworks/Python.framework/Versions/3.9/bin/$bin",
                    "/usr/local/bin/$bin",
                    "$home/miniforge3/bin/$bin",
                    "$home/miniconda3/bin/$bin",
                    "/usr/bin/$bin",
                    "$home/Library/Android/sdk/platform-tools/$bin",
                    "$home/Android/Sdk/platform-tools/$bin",
                )
                if (searchPaths.any { java.io.File(it).canExecute() }) return true
                // 兜底：用注入了 Mac PATH 的 shell 查找
                val pb = buildMacAwareProcessBuilder("sh", "-c", "command -v $bin")
                pb.redirectErrorStream(true)
                val p = pb.start()
                val out = p.inputStream.bufferedReader().readText().trim()
                p.waitFor() == 0 && out.isNotBlank()
            }
        } catch (_: Exception) {
            false
        }
    }

    private fun findPythonCmd(): String {
        return when {
            whichOk("python3") -> "python3"
            whichOk("python") -> "python"
            else -> ""
        }
    }

    private data class RunResult(val exitCode: Int, val output: String)

    private fun runAndCapture(cmd: List<String>, timeoutMs: Long = 5_000): RunResult {
        val pb = ProcessBuilder(cmd)
        pb.redirectErrorStream(true)
        val p = pb.start()

        val sb = StringBuilder()
        val t = Thread {
            try {
                p.inputStream.bufferedReader(Charset.defaultCharset()).useLines { lines ->
                    lines.forEach { sb.append(it).append('\n') }
                }
            } catch (_: Exception) {}
        }
        t.isDaemon = true
        t.start()

        val finished = p.waitFor(timeoutMs, java.util.concurrent.TimeUnit.MILLISECONDS)
        if (!finished) {
            try { p.destroyForcibly() } catch (_: Exception) {}
        }
        try { t.join(300) } catch (_: Exception) {}

        val exit = if (finished) p.exitValue() else -1
        return RunResult(exit, sb.toString().trim())
    }
}
