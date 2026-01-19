package com.carui.accessibility

import android.accessibilityservice.AccessibilityService
import android.content.Intent
import android.graphics.Rect
import android.util.Log
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import com.google.gson.Gson
import fi.iki.elonen.NanoHTTPD
import java.io.IOException

class CarUIAccessibilityService : AccessibilityService() {

    private var httpServer: UIHttpServer? = null
    private val gson = Gson()

    companion object {
        private const val TAG = "CarUIAccessibility"
        private const val HTTP_PORT = 8765
        var instance: CarUIAccessibilityService? = null
    }

    override fun onServiceConnected() {
        super.onServiceConnected()
        instance = this
        Log.d(TAG, "辅助服务已连接")
        
        // 启动HTTP服务器
        startHttpServer()
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        // 不需要主动监听事件，只在HTTP请求时获取UI树
    }

    override fun onInterrupt() {
        Log.d(TAG, "辅助服务被中断")
    }

    override fun onDestroy() {
        super.onDestroy()
        instance = null
        stopHttpServer()
        Log.d(TAG, "辅助服务已销毁")
    }

    private fun startHttpServer() {
        try {
            httpServer = UIHttpServer(HTTP_PORT)
            httpServer?.start()
            Log.d(TAG, "HTTP服务器启动成功，端口: $HTTP_PORT")
        } catch (e: IOException) {
            Log.e(TAG, "HTTP服务器启动失败", e)
        }
    }

    private fun stopHttpServer() {
        httpServer?.stop()
        httpServer = null
        Log.d(TAG, "HTTP服务器已停止")
    }

    /**
     * 获取当前UI树
     */
    fun getCurrentUITree(displayId: Int = 0): UITreeResponse {
        val rootNodes = mutableListOf<UINode>()
        
        try {
            // 获取所有窗口
            val windows = windows ?: emptyList()
            Log.d(TAG, "请求获取Display $displayId 的UI树")
            Log.d(TAG, "系统共有 ${windows.size} 个窗口")
            
            // 调试：打印所有窗口的displayId
            windows.forEachIndexed { index, window ->
                Log.d(TAG, "窗口$index: displayId=${window.displayId}, title=${window.title}, type=${window.type}")
            }

            for (window in windows) {
                if (window.displayId != displayId) {
                    Log.d(TAG, "跳过窗口: displayId=${window.displayId} (需要=$displayId)")
                    continue
                }
                
                Log.d(TAG, "处理窗口: displayId=${window.displayId}, title=${window.title}")
                
                val root = window.root
                if (root != null) {
                    val windowInfo = WindowInfo(
                        title = window.title?.toString() ?: "",
                        type = window.type,
                        displayId = window.displayId,
                        bounds = getBoundsRect(window)
                    )
                    
                    val uiNode = traverseNode(root, windowInfo)
                    rootNodes.add(uiNode)
                    
                    root.recycle()
                }
            }
            
            Log.d(TAG, "成功获取UI树，共 ${rootNodes.size} 个根节点")
            
        } catch (e: Exception) {
            Log.e(TAG, "获取UI树失败", e)
            return UITreeResponse(
                success = false,
                error = e.message ?: "未知错误",
                nodes = emptyList()
            )
        }
        
        return UITreeResponse(
            success = true,
            error = null,
            nodes = rootNodes
        )
    }

    /**
     * 遍历节点树
     */
    private fun traverseNode(
        node: AccessibilityNodeInfo,
        windowInfo: WindowInfo,
        depth: Int = 0
    ): UINode {
        val bounds = Rect()
        node.getBoundsInScreen(bounds)
        
        val children = mutableListOf<UINode>()
        for (i in 0 until node.childCount) {
            val child = node.getChild(i)
            if (child != null) {
                children.add(traverseNode(child, windowInfo, depth + 1))
                child.recycle()
            }
        }
        
        return UINode(
            className = node.className?.toString() ?: "",
            packageName = node.packageName?.toString() ?: "",
            text = node.text?.toString() ?: "",
            contentDescription = node.contentDescription?.toString() ?: "",
            resourceId = node.viewIdResourceName ?: "",
            bounds = BoundsInfo(
                left = bounds.left,
                top = bounds.top,
                right = bounds.right,
                bottom = bounds.bottom
            ),
            clickable = node.isClickable,
            longClickable = node.isLongClickable,
            enabled = node.isEnabled,
            focusable = node.isFocusable,
            focused = node.isFocused,
            selected = node.isSelected,
            checkable = node.isCheckable,
            checked = node.isChecked,
            scrollable = node.isScrollable,
            window = windowInfo,
            children = children,
            depth = depth
        )
    }

    private fun getBoundsRect(window: android.view.accessibility.AccessibilityWindowInfo): BoundsInfo {
        val bounds = Rect()
        window.getBoundsInScreen(bounds)
        return BoundsInfo(
            left = bounds.left,
            top = bounds.top,
            right = bounds.right,
            bottom = bounds.bottom
        )
    }

    /**
     * 内嵌HTTP服务器
     */
    inner class UIHttpServer(port: Int) : NanoHTTPD(port) {
        override fun serve(session: IHTTPSession): Response {
            val uri = session.uri
            val params = session.parms
            
            Log.d(TAG, "HTTP请求: $uri")
            
            return when (uri) {
                "/api/hierarchy" -> {
                    val displayId = params["display"]?.toIntOrNull() ?: 0
                    val uiTree = getCurrentUITree(displayId)
                    val json = gson.toJson(uiTree)
                    newFixedLengthResponse(Response.Status.OK, "application/json", json)
                }
                "/api/status" -> {
                    val status = mapOf(
                        "service" to "running",
                        "port" to HTTP_PORT
                    )
                    val json = gson.toJson(status)
                    newFixedLengthResponse(Response.Status.OK, "application/json", json)
                }
                else -> {
                    newFixedLengthResponse(Response.Status.NOT_FOUND, "text/plain", "Not Found")
                }
            }
        }
    }
}

// 数据类定义
data class UITreeResponse(
    val success: Boolean,
    val error: String?,
    val nodes: List<UINode>
)

data class UINode(
    val className: String,
    val packageName: String,
    val text: String,
    val contentDescription: String,
    val resourceId: String,
    val bounds: BoundsInfo,
    val clickable: Boolean,
    val longClickable: Boolean,
    val enabled: Boolean,
    val focusable: Boolean,
    val focused: Boolean,
    val selected: Boolean,
    val checkable: Boolean,
    val checked: Boolean,
    val scrollable: Boolean,
    val window: WindowInfo,
    val children: List<UINode>,
    val depth: Int
)

data class BoundsInfo(
    val left: Int,
    val top: Int,
    val right: Int,
    val bottom: Int
)

data class WindowInfo(
    val title: String,
    val type: Int,
    val displayId: Int,
    val bounds: BoundsInfo
)
