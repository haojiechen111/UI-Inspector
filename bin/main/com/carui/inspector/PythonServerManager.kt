package com.carui.inspector

import com.intellij.openapi.diagnostic.Logger
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.io.BufferedReader
import java.io.InputStreamReader

data class DependencyCheckResult(
    val success: Boolean,
    val pythonVersion: String?,
    val pythonVersionFromCmd: String?,  // 使用 python --version 获取的版本
    val pythonOk: Boolean,
    val missingPackages: List<String>,
    val missingPackagesWithCmd: Map<String, String>,  // 包名 -> 安装命令的映射
    val errorMessage: String?,
    val osType: String?,  // Windows, Darwin, Linux
    val osName: String?,   // 详细的操作系统信息
    val pipCmd: String?,  // 可用的 pip 命令
    val pipMethods: List<String>,  // 所有可用的 pip 安装方法
    val sysPath: List<String>  // Python sys.path，用于诊断
)

object PythonServerManager {
    private val LOG = Logger.getInstance(PythonServerManager::class.java)
    private var process: Process? = null
    private const val DEFAULT_PORT = 18888
    private var serverDir: File? = null
    private var lastCheckResult: DependencyCheckResult? = null

    fun getServerPort(): Int {
        // Try to read port from file
        serverDir?.let { dir ->
            val portFile = File(dir, "server_port.txt")
            if (portFile.exists()) {
                try {
                    val port = portFile.readText().trim().toInt()
                    LOG.info("Read server port from file: $port")
                    return port
                } catch (e: Exception) {
                    LOG.warn("Failed to read port from file: ${e.message}")
                }
            }
        }
        // Fallback to default port
        return DEFAULT_PORT
    }

    fun getServerURL(): String {
        return "http://127.0.0.1:${getServerPort()}"
    }

    /**
     * 检查Python环境和依赖
     * 返回检查结果，包含详细的错误信息
     */
    fun checkDependencies(pluginPath: String): DependencyCheckResult {
        serverDir = File(pluginPath, "server")
        val checkScript = File(serverDir, "check_dependencies.py")

        if (!checkScript.exists()) {
            LOG.warn("Dependency check script not found, skipping check")
            return DependencyCheckResult(
                success = true,
                pythonVersion = null,
                pythonVersionFromCmd = null,
                pythonOk = true,
                missingPackages = emptyList(),
                missingPackagesWithCmd = emptyMap(),
                errorMessage = null,
                osType = null,
                osName = null,
                pipCmd = null,
                pipMethods = emptyList(),
                sysPath = emptyList()
            )
        }

        // 尝试多个可能的Python路径
        val pythonCandidates = listOf(
            "/opt/homebrew/bin/python3.9",  // Homebrew Python 3.9 (Apple Silicon)
            "/opt/homebrew/bin/python3",     // Homebrew Python 3 (Apple Silicon)
            "/usr/local/bin/python3.9",      // Homebrew Python 3.9 (Intel)
            "/usr/local/bin/python3",        // Homebrew Python 3 (Intel)
            "python3",                       // System Python 3
            "python"                         // Fallback to python
        )

        var pythonCmd: String? = null
        var process: Process? = null

        for (candidate in pythonCandidates) {
            try {
                val pb = ProcessBuilder(candidate, checkScript.absolutePath)
                pb.directory(serverDir)
                pb.redirectErrorStream(true)
                process = pb.start()
                pythonCmd = candidate
                LOG.info("Found working Python: $candidate")
                break
            } catch (e: Exception) {
                LOG.info("$candidate not available: ${e.message}")
            }
        }

        if (process == null || pythonCmd == null) {
            LOG.error("No working Python found")
            return DependencyCheckResult(
                success = false,
                pythonVersion = null,
                pythonVersionFromCmd = null,
                pythonOk = false,
                missingPackages = emptyList(),
                missingPackagesWithCmd = emptyMap(),
                errorMessage = "未找到Python命令。请确保已安装Python 3.7+并添加到系统PATH。",
                osType = null,
                osName = null,
                pipCmd = null,
                pipMethods = emptyList(),
                sysPath = emptyList()
            )
        }

        // 读取检查结果
        process?.let { p ->
            try {
                val reader = BufferedReader(InputStreamReader(p.inputStream))
                val output = reader.readText()
                p.waitFor()

                LOG.info("Dependency check output: $output")

                // 手动解析JSON结果
                try {
                    // 简单的JSON解析
                    val allOk = output.contains("\"all_ok\": true")
                    val pythonOk = output.contains("\"python_ok\": true")

                    // 提取Python版本（使用完整版本号）
                    val pythonVersion = Regex("\"python_version\":\\s*\"([^\"]+)\"")
                        .find(output)?.groupValues?.get(1) ?: "unknown"

                    // 提取使用 python --version 获取的版本
                    val pythonVersionFromCmd = Regex("\"python_version_from_cmd\":\\s*\"([^\"]+)\"")
                        .find(output)?.groupValues?.get(1)

                    // 提取OS信息
                    val osType = Regex("\"os_type\":\\s*\"([^\"]+)\"")
                        .find(output)?.groupValues?.get(1)
                    val osName = Regex("\"os_name\":\\s*\"([^\"]+)\"")
                        .find(output)?.groupValues?.get(1)

                    // 提取 pip 命令信息
                    val pipCmd = Regex("\"pip_cmd\":\\s*\"([^\"]+)\"")
                        .find(output)?.groupValues?.get(1)

                    // 提取所有可用的 pip 方法
                    val pipMethods = mutableListOf<String>()
                    val pipMethodsPattern = Regex("\"pip_methods\":\\s*\\[([^\\]]+)\\]")
                    val pipMethodsMatch = pipMethodsPattern.find(output)
                    if (pipMethodsMatch != null) {
                        val methodsStr = pipMethodsMatch.groupValues[1]
                        Regex("\"([^\"]+)\"").findAll(methodsStr).forEach { match ->
                            pipMethods.add(match.groupValues[1])
                        }
                    }

                    // 提取 sys.path（用于诊断）
                    val sysPath = mutableListOf<String>()
                    val sysPathPattern = Regex("\"sys_path\":\\s*\\[([^\\]]+)\\]")
                    val sysPathMatch = sysPathPattern.find(output)
                    if (sysPathMatch != null) {
                        val pathsStr = sysPathMatch.groupValues[1]
                        Regex("\"([^\"]+)\"").findAll(pathsStr).forEach { match ->
                            sysPath.add(match.groupValues[1])
                        }
                    }

                    // 提取缺失的包及其安装命令
                    val missingPackages = mutableListOf<String>()
                    val missingPackagesWithCmd = mutableMapOf<String, String>()

                    // 找到 dependencies 块
                    val depsBlockPattern = Regex("\"dependencies\":\\s*\\{([^}]+(?:\\{[^}]*\\}[^}]*)*)\\}")
                    val depsBlockMatch = depsBlockPattern.find(output)
                    if (depsBlockMatch != null) {
                        val depsBlock = depsBlockMatch.groupValues[1]

                        // 对于每个依赖包，检查是否已安装
                        val packagePattern = Regex("\"(\\w+)\":\\s*\\{([^}]+)\\}")
                        packagePattern.findAll(depsBlock).forEach { match ->
                            val packageName = match.groupValues[1]
                            val packageInfo = match.groupValues[2]

                            if (packageInfo.contains("\"installed\":\\s*false".toRegex())) {
                                missingPackages.add(packageName)

                                // 提取该包的安装命令
                                val installCmdPattern = Regex("\"install_cmd\":\\s*\"([^\"]+)\"")
                                val installCmd = installCmdPattern.find(packageInfo)?.groupValues?.get(1)
                                if (installCmd != null) {
                                    missingPackagesWithCmd[packageName] = installCmd
                                }
                            }
                        }
                    }

                    // 优先使用从 Python 脚本检测到的 pip 命令，否则根据OS类型生成
                    val pipCommand = pipCmd ?: when (osType) {
                        "Windows" -> "pip"
                        "Darwin" -> "pip3"  // macOS
                        else -> "pip3"  // Linux/Ubuntu
                    }

                    val errorMsg = if (!allOk) {
                        buildString {
                            if (!pythonOk) {
                                append("Python版本过低（需要3.7+，当前：$pythonVersion）\n")
                            }
                            if (missingPackages.isNotEmpty()) {
                                append("缺少依赖包：${missingPackages.joinToString(", ")}\n\n")

                                // 根据不同系统提供不同的安装指引
                                when (osType) {
                                    "Windows" -> {
                                        append("Windows安装命令：\n")
                                        append("$pipCommand install ${missingPackages.joinToString(" ")}\n\n")
                                        append("如果pip不可用，请先安装Python 3.7+：\n")
                                        append("https://www.python.org/downloads/")
                                    }
                                    "Darwin" -> {
                                        append("macOS安装命令：\n")
                                        append("$pipCommand install ${missingPackages.joinToString(" ")}\n\n")
                                        append("如果未安装Python，推荐使用Homebrew安装：\n")
                                        append("brew install python3")
                                    }
                                    "Linux" -> {
                                        val distro = osName?.lowercase() ?: ""
                                        if (distro.contains("ubuntu") || distro.contains("debian")) {
                                            append("Ubuntu/Debian安装命令：\n")
                                            append("sudo apt update && sudo apt install python3 python3-pip\n")
                                            append("$pipCommand install ${missingPackages.joinToString(" ")}")
                                        } else {
                                            append("Linux安装命令：\n")
                                            append("$pipCommand install ${missingPackages.joinToString(" ")}\n\n")
                                            append("如果pip3不可用，请使用包管理器安装：\n")
                                            append("sudo yum install python3-pip  # CentOS/RHEL\n")
                                            append("sudo apt install python3-pip   # Ubuntu/Debian")
                                        }
                                    }
                                    else -> {
                                        append("安装命令：\n")
                                        append("$pipCommand install ${missingPackages.joinToString(" ")}")
                                    }
                                }
                            }
                        }
                    } else null

                    val result = DependencyCheckResult(
                        success = allOk,
                        pythonVersion = pythonVersion,
                        pythonVersionFromCmd = pythonVersionFromCmd,
                        pythonOk = pythonOk,
                        missingPackages = missingPackages,
                        missingPackagesWithCmd = missingPackagesWithCmd,
                        errorMessage = errorMsg,
                        osType = osType,
                        osName = osName,
                        pipCmd = pipCmd,
                        pipMethods = pipMethods,
                        sysPath = sysPath
                    )
                    lastCheckResult = result
                    return result

                } catch (e: Exception) {
                    LOG.error("Failed to parse dependency check result: ${e.message}")
                    return DependencyCheckResult(
                        success = false,
                        pythonVersion = null,
                        pythonVersionFromCmd = null,
                        pythonOk = false,
                        missingPackages = emptyList(),
                        missingPackagesWithCmd = emptyMap(),
                        errorMessage = "依赖检查失败：${e.message}",
                        osType = null,
                        osName = null,
                        pipCmd = null,
                        pipMethods = emptyList(),
                        sysPath = emptyList()
                    )
                }
            } catch (e: Exception) {
                LOG.error("Failed to read dependency check output: ${e.message}")
                return DependencyCheckResult(
                    success = false,
                    pythonVersion = null,
                    pythonVersionFromCmd = null,
                    pythonOk = false,
                    missingPackages = emptyList(),
                    missingPackagesWithCmd = emptyMap(),
                    errorMessage = "依赖检查异常：${e.message}",
                    osType = null,
                    osName = null,
                    pipCmd = null,
                    pipMethods = emptyList(),
                    sysPath = emptyList()
                )
            }
        }

        return DependencyCheckResult(
            success = false,
            pythonVersion = null,
            pythonVersionFromCmd = null,
            pythonOk = false,
            missingPackages = emptyList(),
            missingPackagesWithCmd = emptyMap(),
            errorMessage = "依赖检查进程启动失败",
            osType = null,
            osName = null,
            pipCmd = null,
            pipMethods = emptyList(),
            sysPath = emptyList()
        )
    }
    fun getLastCheckResult(): DependencyCheckResult? = lastCheckResult

    fun start(pluginPath: String, enableMonitoring: Boolean = true): String? {
        pluginPathCache = pluginPath
        serverDir = File(pluginPath, "server")
        val mainScript = File(serverDir, "main.py")

        if (isServerRunning()) {
            LOG.info("Car UI Server is already running on ${getServerURL()}")
            if (enableMonitoring && !shouldMonitor) {
                startMonitoring()
            }
            return null
        }

        if (!mainScript.exists()) {
            val error = "服务脚本未找到：${mainScript.absolutePath}"
            LOG.error(error)
            return error
        }

        val logFile = File(serverDir, "server_log.txt")
        LOG.info("Starting Car UI Server. Logs at: ${logFile.absolutePath}")

        // 尝试多个可能的Python路径
        val pythonCandidates = listOf(
            "/opt/homebrew/bin/python3.9",  // Homebrew Python 3.9 (Apple Silicon)
            "/opt/homebrew/bin/python3",     // Homebrew Python 3 (Apple Silicon)
            "/usr/local/bin/python3.9",      // Homebrew Python 3.9 (Intel)
            "/usr/local/bin/python3",        // Homebrew Python 3 (Intel)
            "python3",                       // System Python 3
            "python"                         // Fallback to python
        )

        var lastError: String? = null
        for (pythonCmd in pythonCandidates) {
            try {
                val pb = ProcessBuilder(pythonCmd, "main.py")
                pb.directory(serverDir)
                pb.redirectErrorStream(true)
                pb.redirectOutput(ProcessBuilder.Redirect.appendTo(logFile))
                process = pb.start()
                LOG.info("Server process started successfully with '$pythonCmd'")
                if (enableMonitoring) {
                    startMonitoring()
                }
                return null
            } catch (e: Exception) {
                lastError = e.message
                LOG.info("Failed to start with '$pythonCmd': ${e.message}")
            }
        }

        val error = "Python启动失败。已尝试所有可能的Python路径。最后错误：$lastError"
        LOG.error(error)
        return error
    }
    fun getServerLog(): String {
        serverDir?.let { dir ->
            val logFile = File(dir, "server_log.txt")
            if (logFile.exists()) {
                try {
                    // 只读取最后50行日志
                    val lines = logFile.readLines()
                    val lastLines = lines.takeLast(50)
                    return lastLines.joinToString("\n")
                } catch (e: Exception) {
                    LOG.warn("Failed to read log file: ${e.message}")
                }
            }
        }
        return "日志文件不存在或无法读取"
    }

    fun stop() {
        process?.destroy()
        process = null
    }

    fun isServerRunning(): Boolean {
        return try {
            val url = URL(getServerURL())
            val connection = url.openConnection() as HttpURLConnection
            connection.connectTimeout = 500
            connection.requestMethod = "GET"
            connection.connect()
            val responseCode = connection.responseCode
            responseCode == 200
        } catch (e: Exception) {
            false
        }
    }
}
