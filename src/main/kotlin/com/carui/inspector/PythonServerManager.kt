package com.carui.inspector

import com.intellij.openapi.diagnostic.Logger
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.TimeUnit

object PythonServerManager {
    private val LOG = Logger.getInstance(PythonServerManager::class.java)
    private var process: Process? = null
    private const val SERVER_URL = "http://127.0.0.1:8000"

    fun start(pluginPath: String) {
        if (isServerRunning()) {
            LOG.info("Car UI Server is already running.")
            return
        }

        val serverDir = File(pluginPath, "server")
        val mainScript = File(serverDir, "main.py")

        if (!mainScript.exists()) {
            LOG.error("Server script not found at ${mainScript.absolutePath}")
            return
        }

        val logFile = File(serverDir, "server_log.txt")
        LOG.info("Starting Car UI Server. Logs at: ${logFile.absolutePath}")

        val pb = ProcessBuilder("python3", "main.py")
        pb.directory(serverDir)
        pb.redirectErrorStream(true)
        pb.redirectOutput(ProcessBuilder.Redirect.appendTo(logFile))
        
        try {
            process = pb.start()
        } catch (e: Exception) {
            LOG.error("Failed to start with 'python3', trying 'python': ${e.message}")
            try {
               val pb2 = ProcessBuilder("python", "main.py")
               pb2.directory(serverDir)
               pb2.redirectErrorStream(true)
               pb2.redirectOutput(ProcessBuilder.Redirect.appendTo(logFile))
               process = pb2.start()
            } catch (e2: Exception) {
               LOG.error("Both python3 and python failed: ${e2.message}")
            }
        }
    }

    fun stop() {
        process?.destroy()
        process = null
    }

    fun isServerRunning(): Boolean {
        return try {
            val url = URL(SERVER_URL)
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
