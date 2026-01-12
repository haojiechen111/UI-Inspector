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

        LOG.info("Starting Car UI Server from ${serverDir.absolutePath}")

        val pb = ProcessBuilder("python3", "main.py")
        pb.directory(serverDir)
        pb.redirectErrorStream(true)
        
        try {
            process = pb.start()
            // Optional: Start a thread to read logs if needed
        } catch (e: Exception) {
            LOG.error("Failed to start Python server: ${e.message}")
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
