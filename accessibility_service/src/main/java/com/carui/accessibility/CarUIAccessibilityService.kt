package com.carui.accessibility

import android.accessibilityservice.AccessibilityService
import android.content.Intent
import android.graphics.Rect
import android.os.Build
import android.util.Log
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import android.view.accessibility.AccessibilityWindowInfo
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
     * 获取指定 displayId 的可访问窗口列表，含虚拟 Display 支持。
     *
     * 策略（优先级从高到低）：
     * 1. Android 13+ (API 33)：使用 windowsOnAllDisplays
     *    - 可直接精确访问虚拟 display（如 displayId=10）的窗口树
     *    - 不会把其他 display 的窗口混入
     * 2. Android < 13：使用 windows 属性并按 displayId 过滤
     *    - 若目标 display 无专属窗口（SS4/多 SoC 场景），回退到全部窗口（displayFallback=true）
     *
     * @param displayId 目标 display id（0=主屏，10=虚拟 display 等）
     * @return Pair(窗口列表, displayFallback)
     *   displayFallback=true 表示未找到专属窗口、已回退为所有窗口
     */
    private fun getWindowsForDisplay(displayId: Int): Pair<List<AccessibilityWindowInfo>, Boolean> {

        // ── 策略 1：Android 13+ windowsOnAllDisplays ────────────────────────────
        // windowsOnAllDisplays 是 API 33 新增 API，可直接按 displayId 获取任意 display
        // 的窗口，包括 VirtualDisplay（如 displayId=10）。
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            try {
                val allDisplayWindows = windowsOnAllDisplays          // SparseArray<List<AccessibilityWindowInfo>>
                val allIds = (0 until allDisplayWindows.size()).map { allDisplayWindows.keyAt(it) }
                Log.d(TAG, "API33+ windowsOnAllDisplays: ${allDisplayWindows.size()} 个 display，IDs=$allIds")

                val windowList = allDisplayWindows.get(displayId)
                if (!windowList.isNullOrEmpty()) {
                    Log.d(TAG, "API33+: display $displayId 精确找到 ${windowList.size} 个窗口")
                    return Pair(windowList, false)
                }
                // 指定 display 无专属窗口 —— 对虚拟 display 而言可能是内容尚未渲染
                Log.w(TAG, "API33+: display $displayId 无专属窗口 (所有可用IDs=$allIds)")
                // 非 display0 且无匹配时返回空（不降级，避免混入 display0 节点）
                if (displayId != 0) {
                    return Pair(emptyList(), false)
                }
            } catch (e: Exception) {
                Log.w(TAG, "API33+ windowsOnAllDisplays 调用失败: ${e.message}，降级到 windows 属性")
            }
        }

        // ── 策略 2：Android < 13，使用 windows 并按 displayId 过滤 ─────────────
        val allWindows = windows ?: emptyList()
        val allDisplayIds = allWindows.map { it.displayId }.distinct()
        Log.d(TAG, "windows 属性: 共 ${allWindows.size} 个窗口，所有 displayIds=$allDisplayIds")

        allWindows.forEachIndexed { idx, w ->
            Log.d(TAG, "  窗口$idx: displayId=${w.displayId}, title=${w.title}, type=${w.type}")
        }

        val matched = allWindows.filter { it.displayId == displayId }
        Log.d(TAG, "按 displayId=$displayId 过滤: 找到 ${matched.size}/${allWindows.size} 个窗口")

        // 非 display0 且无匹配：回退到所有窗口（SS4/多 SoC 兜底）
        if (matched.isEmpty() && displayId != 0) {
            Log.w(TAG, "⚠️ display=$displayId 无匹配窗口（可用IDs=$allDisplayIds）" +
                       "，回退到所有窗口（SS4/多SoC兜底）")
            return Pair(allWindows, true)
        }
        return Pair(matched, false)
    }

    /**
     * 获取当前UI树
     *
     * @param displayId 目标 display id。0=主屏，10=虚拟 display（内容通过 SurfaceView 渲染）
     */
    fun getCurrentUITree(displayId: Int = 0): UITreeResponse {
        val rootNodes = mutableListOf<UINode>()
        
        try {
            val (windowsToProcess, displayFallback) = getWindowsForDisplay(displayId)
            Log.d(TAG, "请求 display=$displayId UI树：处理 ${windowsToProcess.size} 个窗口，fallback=$displayFallback")

            for (window in windowsToProcess) {
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
            
            Log.d(TAG, "成功获取UI树，共 ${rootNodes.size} 个根节点 (displayFallback=$displayFallback)")
            
            return UITreeResponse(
                success = true,
                error = null,
                nodes = rootNodes,
                displayFallback = displayFallback
            )
        } catch (e: Exception) {
            Log.e(TAG, "获取UI树失败", e)
            return UITreeResponse(
                success = false,
                error = e.message ?: "未知错误",
                nodes = emptyList()
            )
        }
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
            visibleToUser = node.isVisibleToUser,
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

    private fun getBoundsRect(window: AccessibilityWindowInfo): BoundsInfo {
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
    val nodes: List<UINode>,
    val displayFallback: Boolean = false  // SS4多SoC兜底：返回的节点包含所有display的数据
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
    val visibleToUser: Boolean,
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
