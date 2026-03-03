package com.carui.inspector

import com.intellij.openapi.diagnostic.Logger
import com.intellij.openapi.project.Project
import com.intellij.openapi.ui.Messages
import java.io.File
import java.nio.charset.Charset

/**
 * 在 IDE 插件里“一键启动” Text Visible Speak（外部 Python GUI）。
 *
 * 【重要约束 / Contract】
 * 1) 该工具只能通过“手动运行脚本”或“点击插件按钮”启动；禁止任何自动/常驻启动。
 * 2) 该工具必须与车端 voice 项目线上逻辑强隔离：不启动时不产生任何副作用。
 *
 * 详细契约见：
 * - voice-androidMin/tools/TEXT_VISIBLE_SPEAK_CONTRACT.md
 *
 * 约定：脚本被打包在 <plugin>/server/tools/text_visible_speak_pc.py
 */
object TextVisibleSpeakLauncher {
    private val LOG = Logger.getInstance(TextVisibleSpeakLauncher::class.java)

    private const val REL_SCRIPT_PATH = "server/tools/text_visible_speak_pc.py"

    /**
     * @param pluginPath 插件安装目录（PluginManagerCore.getPlugin(...).pluginPath）
     */
    fun launch(project: Project?, pluginPath: String) {
        try {
            val scriptFile = File(pluginPath, REL_SCRIPT_PATH)
            if (!scriptFile.exists()) {
                Messages.showErrorDialog(
                    project,
                    "未找到脚本：${scriptFile.absolutePath}\n" +
                        "请确认插件已重新 build/install，且 server/tools 已被打包。",
                    "启动可见即可说失败"
                )
                return
            }

            val adbOk = whichOk("adb")
            if (!adbOk) {
                Messages.showErrorDialog(
                    project,
                    "未在 PATH 中找到 adb。\n请确认 Android Studio/系统已配置 platform-tools。",
                    "启动可见即可说失败"
                )
                return
            }

            val pythonCmd = findPythonCmd()
            if (pythonCmd.isEmpty()) {
                Messages.showErrorDialog(
                    project,
                    "未找到 python3/python。\n请安装 Python 3，并确保命令可用。",
                    "启动可见即可说失败"
                )
                return
            }

            // tkinter 自检（避免用户点了没反应）
            val tkCheck = runAndCapture(listOf(pythonCmd, "-c", "import tkinter"))
            if (tkCheck.exitCode != 0) {
                Messages.showErrorDialog(
                    project,
                    "Python tkinter 不可用，无法启动 GUI。\n" +
                        "常见修复：Ubuntu 安装 python3-tk。\n\n" +
                        "输出：\n${tkCheck.output}",
                    "启动可见即可说失败"
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
                        "启动进程失败：${e.message}\n命令：${cmd.joinToString(" ")}",
                        "启动可见即可说失败"
                    )
                }
            }.start()

            Messages.showInfoMessage(
                project,
                "已尝试启动 Text Visible Speak GUI。\n" +
                    "\n提示：该工具需要设备端已集成 TextVisibleSpeakRemoteService，并在设备上启用无障碍服务。",
                "启动可见即可说"
            )
        } catch (t: Throwable) {
            LOG.warn("launch failed", t)
            Messages.showErrorDialog(
                project,
                "启动失败：${t.message}",
                "启动可见即可说失败"
            )
        }
    }

    private fun whichOk(bin: String): Boolean {
        // Windows 没有 which，这里用 'where' 兜底
        val isWindows = System.getProperty("os.name").lowercase().contains("win")
        val cmd = if (isWindows) {
            listOf("cmd", "/c", "where", bin)
        } else {
            // 避免用户机器上的 /etc/profile/.bash_profile 异常影响
            listOf("bash", "--noprofile", "--norc", "-lc", "command -v $bin")
        }
        return try {
            val r = runAndCapture(cmd)
            r.exitCode == 0 && r.output.isNotBlank()
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
                    lines.forEach {
                        sb.append(it).append('\n')
                    }
                }
            } catch (_: Exception) {
            }
        }
        t.isDaemon = true
        t.start()

        val finished = p.waitFor(timeoutMs, java.util.concurrent.TimeUnit.MILLISECONDS)
        if (!finished) {
            try {
                p.destroyForcibly()
            } catch (_: Exception) {
            }
        }
        try {
            t.join(300)
        } catch (_: Exception) {
        }

        val exit = if (finished) p.exitValue() else -1
        return RunResult(exit, sb.toString().trim())
    }
}
