package com.carui.inspector

import com.intellij.openapi.diagnostic.Logger
import java.io.File
import java.net.HttpURLConnection
import java.net.URL

object PythonServerManager {
    private val LOG = Logger.getInstance(PythonServerManager::class.java)
    private var process: Process? = null
    private const val DEFAULT_PORT = 18888
    private var serverDir: File? = null

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

    fun start(pluginPath: String) {
        serverDir = File(pluginPath, "server")
        val mainScript = File(serverDir, "main.py")
        
        if (isServerRunning()) {
            LOG.info("Car UI Server is already running on ${getServerURL()}")
            return
        }

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
