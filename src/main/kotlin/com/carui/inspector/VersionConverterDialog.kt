package com.carui.inspector

import java.awt.Color
import java.awt.Dialog
import java.awt.Dimension
import java.awt.FlowLayout
import java.awt.Font
import java.awt.GridBagConstraints
import java.awt.GridBagLayout
import java.awt.Insets
import java.awt.Toolkit
import java.awt.Window
import java.awt.datatransfer.StringSelection
import javax.swing.BorderFactory
import javax.swing.Box
import javax.swing.BoxLayout
import javax.swing.JButton
import javax.swing.JComboBox
import javax.swing.JDialog
import javax.swing.JLabel
import javax.swing.JPanel
import javax.swing.JTextField
import javax.swing.Timer
import javax.swing.UIManager
import javax.swing.border.TitledBorder

/**
 * HU 版本号可视化转换工具对话框
 *
 * 还原 DevUtils.getFormatHuVersion() 的完整算法：
 *   number = 100000 + Σ( segment[i] × 1000^(segCount-1-i) )
 *
 * 示例：
 *   "12.4.0"  →  100000 + 12×1000000 + 4×1000 + 0×1  =  12104000
 *   "8.4.0"   →  100000 + 8×1000000  + 4×1000 + 0×1  =   8104000
 *   "4.0.0"   →  100000 + 4×1000000  + 0×1000 + 0×1  =   4100000
 */
class VersionConverterDialog(owner: Window?) : JDialog(
    owner,
    "HU 版本号转换工具",
    Dialog.ModalityType.MODELESS
) {

    companion object {
        /** 算法基准偏移量，与 Java 侧一致 */
        private const val BASE_OFFSET = 100_000L

        /** 每段步长（1000 的幂次），与 Java 侧 tempVersionStep 一致 */
        private const val STEP = 1_000L

        /** 平台预设（平台描述、版本字符串、期望的格式化数字） */
        private val PRESETS = listOf(
            Triple("SS4 (高通 8797)",  "4.0.0",    4_100_000L),
            Triple("SS2 (高通 8155)", "8.4.0",    8_104_000L),
            Triple("SS3 (高通 8295)", "12.4.0",  12_104_000L),
        )

        /**
         * 版本字符串 → 数字
         *
         * 与 Java 侧 DevUtils.getFormatHuVersion() 逻辑完全一致：
         *   baseVersion(100000) + Σ( segment[i] × 1000^(length-1-i) )
         *
         * @param version 版本字符串，例如 "12.4.0"
         * @return 转换后的数字；格式非法时返回 null
         */
        fun versionToNumber(version: String): Long? {
            val trimmed = version.trim()
            if (trimmed.isEmpty()) return null
            val parts = trimmed.split(".")
            if (parts.isEmpty()) return null
            return try {
                var result = BASE_OFFSET
                for (i in parts.indices) {
                    val seg = parts[i].trim().toLong()
                    if (seg < 0) return null
                    val exp = (parts.size - 1 - i).toDouble()
                    result += (seg * Math.pow(STEP.toDouble(), exp)).toLong()
                }
                result
            } catch (_: Exception) {
                null
            }
        }

        /**
         * 数字 → 版本字符串（按位反推，需指定段数）
         *
         * 从高位到低位，依次除以 1000 的对应幂次。
         *
         * @param number   格式化后的版本数字，例如 12104000
         * @param segments 版本段数，例如 3 表示 "A.B.C" 格式
         * @return 版本字符串；数字非法时返回 null
         */
        fun numberToVersion(number: Long, segments: Int): String? {
            if (segments < 1 || segments > 6) return null
            if (number < BASE_OFFSET) return null
            var remaining = number - BASE_OFFSET
            val parts = mutableListOf<Long>()
            for (i in 0 until segments) {
                val power = Math.pow(STEP.toDouble(), (segments - 1 - i).toDouble()).toLong()
                parts.add(remaining / power)
                remaining %= power
            }
            return parts.joinToString(".")
        }

        /**
         * 生成正向转换的计算过程文字，用于可视化展示。
         *
         * 例：version="12.4.0", result=12104000
         *   →  "计算: 100000 + 12x1000000 + 4x1000 + 0x1  =  12104000"
         */
        fun buildFormula(version: String, result: Long): String {
            val parts = version.trim().split(".")
            return buildString {
                append("计算: $BASE_OFFSET")
                for (i in parts.indices) {
                    val seg   = parts[i].trim()
                    val power = Math.pow(STEP.toDouble(), (parts.size - 1 - i).toDouble()).toLong()
                    append(" + ${seg}x${power}")
                }
                append("  =  $result")
            }
        }
    }

    init {
        defaultCloseOperation = DISPOSE_ON_CLOSE
        isResizable = true
        buildUI()
        pack()
        minimumSize = Dimension(560, 390)
        setLocationRelativeTo(owner)
    }

    // ═══════════════════════════════════════════════════════
    //  顶层布局
    // ═══════════════════════════════════════════════════════

    private fun buildUI() {
        val root = JPanel()
        root.layout = BoxLayout(root, BoxLayout.Y_AXIS)
        root.border = BorderFactory.createEmptyBorder(12, 14, 10, 14)

        root.add(buildForwardPanel())
        root.add(Box.createVerticalStrut(10))
        root.add(buildReversePanel())
        root.add(Box.createVerticalStrut(10))
        root.add(buildPresetsPanel())
        root.add(Box.createVerticalStrut(6))
        root.add(buildBottomBar())

        contentPane = root
    }

    // ═══════════════════════════════════════════════════════
    //  区域 1：版本号字符串 → 数字
    // ═══════════════════════════════════════════════════════

    private fun buildForwardPanel(): JPanel {
        val panel = JPanel(GridBagLayout())
        panel.border = makeTitledBorder(" 版本号  →  数字 ")

        val inputField  = makeMonoField("12.4.0")
        val resultField = makeResultField()
        val formulaLbl  = JLabel("    ").apply {
            font       = Font("Monospaced", Font.PLAIN, 11)
            foreground = Color(115, 115, 115)
        }
        val copyBtn    = makeCopyButton { resultField.text }
        copyBtn.isEnabled = false
        val convertBtn = JButton("转换 →")

        fun doConvert() {
            val v = inputField.text
            val r = versionToNumber(v)
            if (r != null) {
                resultField.text       = r.toString()
                resultField.foreground = Color(0, 115, 0)
                formulaLbl.text        = buildFormula(v, r)
                copyBtn.isEnabled      = true
            } else {
                resultField.text       = "格式错误（示例：12.4.0）"
                resultField.foreground = Color(180, 0, 0)
                formulaLbl.text        = "    "
                copyBtn.isEnabled      = false
            }
        }

        convertBtn.addActionListener { doConvert() }
        inputField.addActionListener  { doConvert() }   // 按 Enter 触发

        // ── 行 0：输入 | 转换按钮 | 结果 | 复制 ──
        val g = GridBagConstraints()
        g.gridy  = 0
        g.insets = Insets(5, 6, 2, 6)

        g.gridx = 0; g.fill = GridBagConstraints.NONE;       g.weightx = 0.0
        panel.add(JLabel("版本号:"), g)

        g.gridx = 1; g.fill = GridBagConstraints.HORIZONTAL; g.weightx = 1.0
        panel.add(inputField, g)

        g.gridx = 2; g.fill = GridBagConstraints.NONE;       g.weightx = 0.0
        panel.add(convertBtn, g)

        g.gridx = 3; g.fill = GridBagConstraints.HORIZONTAL; g.weightx = 1.0
        panel.add(resultField, g)

        g.gridx = 4; g.fill = GridBagConstraints.NONE;       g.weightx = 0.0
        panel.add(copyBtn, g)

        // ── 行 1：计算过程说明（跨 5 列）──
        val gFormula = GridBagConstraints()
        gFormula.gridx    = 0
        gFormula.gridy    = 1
        gFormula.gridwidth = 5
        gFormula.fill     = GridBagConstraints.HORIZONTAL
        gFormula.weightx  = 1.0
        gFormula.insets   = Insets(0, 8, 6, 8)
        panel.add(formulaLbl, gFormula)

        return panel
    }

    // ═══════════════════════════════════════════════════════
    //  区域 2：数字 → 版本号字符串
    // ═══════════════════════════════════════════════════════

    private fun buildReversePanel(): JPanel {
        val panel = JPanel(GridBagLayout())
        panel.border = makeTitledBorder(" 数字  →  版本号 ")

        val inputField  = makeMonoField("12104000")
        val resultField = makeResultField()
        // 默认选 3 段（HU 版本号最常见格式）
        val segCombo = JComboBox(arrayOf("2 段 (A.B)", "3 段 (A.B.C)", "4 段 (A.B.C.D)"))
        segCombo.selectedIndex = 1
        val copyBtn    = makeCopyButton { resultField.text }
        copyBtn.isEnabled = false
        val convertBtn = JButton("转换 →")

        fun doConvert() {
            val n    = inputField.text.trim().toLongOrNull()
            val segs = segCombo.selectedIndex + 2   // combo 0→2段, 1→3段, 2→4段
            val r    = if (n != null) numberToVersion(n, segs) else null
            if (r != null) {
                resultField.text       = r
                resultField.foreground = Color(0, 115, 0)
                copyBtn.isEnabled      = true
            } else {
                resultField.text       = "数字无效（需 ≥ 100000）"
                resultField.foreground = Color(180, 0, 0)
                copyBtn.isEnabled      = false
            }
        }

        convertBtn.addActionListener { doConvert() }
        inputField.addActionListener  { doConvert() }   // 按 Enter 触发

        val g = GridBagConstraints()
        g.gridy  = 0
        g.insets = Insets(5, 6, 5, 6)

        g.gridx = 0; g.fill = GridBagConstraints.NONE;       g.weightx = 0.0
        panel.add(JLabel("数字:"), g)

        g.gridx = 1; g.fill = GridBagConstraints.HORIZONTAL; g.weightx = 1.0
        panel.add(inputField, g)

        g.gridx = 2; g.fill = GridBagConstraints.NONE;       g.weightx = 0.0
        panel.add(segCombo, g)

        g.gridx = 3; g.fill = GridBagConstraints.NONE;       g.weightx = 0.0
        panel.add(convertBtn, g)

        g.gridx = 4; g.fill = GridBagConstraints.HORIZONTAL; g.weightx = 1.0
        panel.add(resultField, g)

        g.gridx = 5; g.fill = GridBagConstraints.NONE;       g.weightx = 0.0
        panel.add(copyBtn, g)

        return panel
    }

    // ═══════════════════════════════════════════════════════
    //  区域 3：平台常见参考值
    // ═══════════════════════════════════════════════════════

    private fun buildPresetsPanel(): JPanel {
        val panel = JPanel(GridBagLayout())
        panel.border = makeTitledBorder(" 常见参考值 ")

        val g = GridBagConstraints()
        g.anchor = GridBagConstraints.WEST
        g.insets = Insets(3, 12, 3, 12)

        PRESETS.forEachIndexed { row, (platform, ver, num) ->
            g.gridy = row

            g.gridx = 0; g.weightx = 0.0
            panel.add(JLabel(platform).apply { foreground = Color(90, 90, 90) }, g)

            g.gridx = 1; g.weightx = 0.2
            panel.add(JLabel(ver).apply {
                font       = Font("Monospaced", Font.PLAIN, 13)
                foreground = Color(30, 100, 165)
            }, g)

            g.gridx = 2; g.weightx = 0.0
            panel.add(JLabel("  →  ").apply { foreground = Color(160, 160, 160) }, g)

            g.gridx = 3; g.weightx = 1.0
            panel.add(JLabel(num.toString()).apply {
                font = Font("Monospaced", Font.BOLD, 13)
            }, g)
        }

        return panel
    }

    // ═══════════════════════════════════════════════════════
    //  底部关闭按钮
    // ═══════════════════════════════════════════════════════

    private fun buildBottomBar(): JPanel {
        val bar = JPanel(FlowLayout(FlowLayout.RIGHT, 0, 0))
        val closeBtn = JButton("关闭")
        closeBtn.addActionListener { dispose() }
        bar.add(closeBtn)
        return bar
    }

    // ═══════════════════════════════════════════════════════
    //  UI 工厂辅助函数
    // ═══════════════════════════════════════════════════════

    /** 创建带标题的 Etched 边框 */
    private fun makeTitledBorder(title: String) = BorderFactory.createTitledBorder(
        BorderFactory.createEtchedBorder(), title,
        TitledBorder.LEFT, TitledBorder.TOP,
        Font("SansSerif", Font.BOLD, 12)
    )

    /** 创建等宽字体输入框 */
    private fun makeMonoField(text: String) = JTextField(text).apply {
        font    = Font("Monospaced", Font.PLAIN, 13)
        columns = 14
    }

    /** 创建只读结果显示框（灰底） */
    private fun makeResultField() = JTextField().apply {
        font       = Font("Monospaced", Font.PLAIN, 13)
        columns    = 14
        isEditable = false
        background = UIManager.getColor("TextField.inactiveBackground")
            ?: Color(242, 242, 242)
    }

    /**
     * 创建"复制"按钮
     * 点击后将 textProvider() 的返回值写入系统剪贴板，并短暂显示"已复制 ✓"反馈。
     *
     * @param textProvider 点击时调用，获取要复制的文字
     */
    private fun makeCopyButton(textProvider: () -> String): JButton {
        val btn = JButton("复制")
        btn.addActionListener {
            val text = textProvider()
            if (text.isNotBlank()) {
                Toolkit.getDefaultToolkit().systemClipboard
                    .setContents(StringSelection(text), null)
                val orig = btn.text
                btn.text = "已复制 ✓"
                val t = Timer(1400) { btn.text = orig }
                t.isRepeats = false
                t.start()
            }
        }
        return btn
    }
}
