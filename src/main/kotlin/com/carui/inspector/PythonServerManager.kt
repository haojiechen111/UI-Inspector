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
    private var monitorThread: Thread? = null
    private var shouldMonitor = false
    private var pluginPathCache: String? = null

    private const val RESTART_FLAG_FILE = "restart_requested.flag"

    private fun getRestartFlagFile(): File? {
        val dir = serverDir ?: return null
        return File(dir, RESTART_FLAG_FILE)
    }

    /**
     * Python 端收到 /api/restart-server 后会落盘该标记文件，用于让插件侧快速感知“这次退出是主动重启”。
     */
    private fun isRestartRequestedFlagPresent(): Boolean {
        return try {
            val f = getRestartFlagFile() ?: return false
            f.exists() && f.length() > 0
        } catch (_: Exception) {
            false
        }
    }

    private fun clearRestartRequestedFlag() {
        try {
            val f = getRestartFlagFile() ?: return
            if (f.exists()) f.delete()
        } catch (_: Exception) {
        }
    }

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

        // 尝试python3
        var pythonCmd = "python3"
        var process: Process? = null
        
        try {
            val pb = ProcessBuilder(pythonCmd, checkScript.absolutePath)
            pb.directory(serverDir)
            pb.redirectErrorStream(true)
            process = pb.start()
        } catch (e: Exception) {
            LOG.info("python3 not found, trying python: ${e.message}")
            // 尝试python
            try {
                pythonCmd = "python"
                val pb = ProcessBuilder(pythonCmd, checkScript.absolutePath)
                pb.directory(serverDir)
                pb.redirectErrorStream(true)
                process = pb.start()
            } catch (e2: Exception) {
                LOG.error("Both python3 and python not found: ${e2.message}")
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

        val pb = ProcessBuilder("python3", "main.py")
        pb.directory(serverDir)
        pb.redirectErrorStream(true)
        pb.redirectOutput(ProcessBuilder.Redirect.appendTo(logFile))
        
        try {
            process = pb.start()
            LOG.info("Server process started successfully")
            if (enableMonitoring) {
                startMonitoring()
            }
            return null
        } catch (e: Exception) {
            LOG.error("Failed to start with 'python3', trying 'python': ${e.message}")
            try {
               val pb2 = ProcessBuilder("python", "main.py")
               pb2.directory(serverDir)
               pb2.redirectErrorStream(true)
               pb2.redirectOutput(ProcessBuilder.Redirect.appendTo(logFile))
               process = pb2.start()
               LOG.info("Server process started successfully with 'python'")
               if (enableMonitoring) {
                   startMonitoring()
               }
               return null
            } catch (e2: Exception) {
               val error = "Python启动失败：${e2.message}"
               LOG.error(error)
               return error
            }
        }
    }

    /**
     * 启动进程监控线程
     * 自动检测服务崩溃并重启
     */
    private fun startMonitoring() {
        // 如果已经在监控，先停止
        stopMonitoring()
        
        shouldMonitor = true
        monitorThread = Thread {
            LOG.info("Server monitoring thread started")
            var consecutiveFailures = 0
            val maxConsecutiveFailures = 3
            
            while (shouldMonitor) {
                try {
                    Thread.sleep(1000) // 每1秒检查一次（提高重启响应速度）
                    
                    if (!shouldMonitor) break
                    
                    // 允许通过“重启标记文件”快速触发重启（避免 5s*3 的长等待）
                    val restartFlag = isRestartRequestedFlagPresent()

                    // 检查进程是否存活
                    val processAlive = process?.isAlive ?: false
                    val serverResponding = isServerRunning()
                    
                    if (!processAlive || !serverResponding || restartFlag) {
                        if (restartFlag) {
                            LOG.warn("Restart flag detected, triggering immediate restart")
                        } else {
                            consecutiveFailures++
                            LOG.warn("Server not responding (attempt $consecutiveFailures/$maxConsecutiveFailures)")
                        }

                        // 触发条件：有 restart flag 或达到失败阈值
                        val shouldRestartNow = restartFlag || consecutiveFailures >= maxConsecutiveFailures
                        
                        if (shouldRestartNow) {
                            LOG.error("Attempting restart... (restartFlag=$restartFlag, failures=$consecutiveFailures)")
                            
                            // 尝试重启
                            pluginPathCache?.let { path ->
                                // 清理标记文件，避免重复触发
                                if (restartFlag) clearRestartRequestedFlag()

                                stop()
                                Thread.sleep(800) // 等待端口释放（缩短等待）
                                
                                val restartError = start(path, enableMonitoring = false) // 重启时暂不启动监控，避免递归
                                if (restartError == null) {
                                    LOG.info("Server restarted successfully")
                                    consecutiveFailures = 0
                                    // 重新启动监控
                                    if (shouldMonitor) {
                                        Thread.sleep(1200) // 等待服务器稳定
                                    }
                                } else {
                                    LOG.error("Failed to restart server: $restartError")
                                }
                            }
                        }
                    } else {
                        // 服务正常，重置失败计数
                        if (consecutiveFailures > 0) {
                            LOG.info("Server recovered, resetting failure count")
                            consecutiveFailures = 0
                        }

                        // 服务已恢复，若有残留标记文件则清理
                        if (restartFlag) clearRestartRequestedFlag()
                    }
                } catch (e: InterruptedException) {
                    LOG.info("Monitoring thread interrupted")
                    break
                } catch (e: Exception) {
                    LOG.error("Error in monitoring thread: ${e.message}")
                }
            }
            LOG.info("Server monitoring thread stopped")
        }.apply {
            isDaemon = true
            name = "CarUI-ServerMonitor"
            start()
        }
    }

    /**
     * 停止进程监控
     */
    private fun stopMonitoring() {
        shouldMonitor = false
        monitorThread?.interrupt()
        monitorThread = null
    }

    /**
     * 手动重启服务器
     */
    fun restart(): String? {
        LOG.info("Manual server restart requested")
        pluginPathCache?.let { path ->
            stop()
            Thread.sleep(2000) // 等待端口释放
            return start(path, enableMonitoring = true)
        }
        return "无法重启：插件路径未缓存"
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
        try {
            process?.destroy()
        } catch (_: Exception) {
        } finally {
            process = null
        }
    }

    /**
     * “瞬间杀死一切”版停止：
     * - 直接 destroyForcibly
     * - 尽量杀掉子进程（ProcessHandle descendants）
     * - 可选清理 server_port.txt / restart_requested.flag，让下次启动更干净
     */
    fun hardStop(clearPortFile: Boolean = true, clearRestartFlag: Boolean = true) {
        stopMonitoring()

        val p = process
        if (p != null) {
            try {
                val handle = p.toHandle()
                try {
                    // Kill children first
                    handle.descendants().forEach {
                        try {
                            it.destroyForcibly()
                        } catch (_: Exception) {
                        }
                    }
                } catch (_: Exception) {
                    // ignore - ProcessHandle may not be available on some JREs
                }

                try {
                    p.destroyForcibly()
                } catch (_: Exception) {
                }

                try {
                    // Best-effort wait a bit to release port
                    p.waitFor(300, java.util.concurrent.TimeUnit.MILLISECONDS)
                } catch (_: Exception) {
                }
            } catch (e: Exception) {
                LOG.warn("Hard stop failed: ${e.message}")
            }
        }

        process = null

        // Clean files (best-effort)
        try {
            val dir = serverDir
            if (dir != null) {
                if (clearPortFile) {
                    val portFile = File(dir, "server_port.txt")
                    if (portFile.exists()) portFile.delete()
                }
                if (clearRestartFlag) {
                    val f = File(dir, RESTART_FLAG_FILE)
                    if (f.exists()) f.delete()
                }
            }
        } catch (_: Exception) {
        }
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
