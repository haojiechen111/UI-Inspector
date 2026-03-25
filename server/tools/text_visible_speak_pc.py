#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Text Visible Speak - PC 端 GUI。

功能：文本输入 → adb 端口转发 → 发送到设备端 TextVisibleSpeakRemoteService。

说明：
- 该脚本会被 UI-Inspector 插件打包到 <plugin>/server/tools/ 下。
- 为避免插件安装目录无写权限，历史/常用语数据会写入用户目录：
  ~/.carui_inspector/text_visible_speak/

依赖：Python 3（标准库 tkinter/socket/subprocess/json 即可）。
"""

import json
import os
import socket
import subprocess
import threading
import time
import tkinter as tk
from tkinter import ttk
from typing import Optional


PORT = 27183
PKG = "com.chehejia.car.voice"
SERVICE = f"{PKG}/.TextVisibleSpeakRemoteService"

# ── 屏幕选项（显示文本 → 实际 screen 值）──────────────────────────────────
SCREEN_OPTIONS: list[tuple[str, str]] = [
    ("主驾 (driver)",    "driver"),
    ("副驾 (passenger)", "passenger"),
    ("后排 (rear)",      "rear"),
    ("后排W (rear_w)",   "rear_w"),
]
SCREEN_DISPLAY_VALUES   = [s[0] for s in SCREEN_OPTIONS]
SCREEN_DISPLAY_TO_VALUE = {s[0]: s[1] for s in SCREEN_OPTIONS}
SCREEN_VALUE_TO_DISPLAY = {s[1]: s[0] for s in SCREEN_OPTIONS}

# TextVisibleSpeak 调试工具：UI 不暴露“声源位置”，但为了提升多屏执行成功率，
# 这里根据 screen 自动选择一个更合理的 sourceLocation：
# - driver -> 1 (VRC_L1_1_LOCATION)
# - passenger -> 2 (VRC_R1_2_LOCATION)
# - rear/rear_w -> 10000 (REAR_UNKNOWN_LOCATION)
# 说明：sourceLocation 主要用于一些跨屏/按键兜底逻辑、以及埋点；点击执行本身以 screenId 为准。
def default_location_by_screen(screen: str) -> int:
    s = (screen or "").strip()
    if s == "passenger":
        return 2
    if s in ("rear", "rear_w"):
        return 10000
    return 1


DEFAULT_ADB_TIMEOUT_S = float(os.environ.get("TEXT_VISIBLE_SPEAK_ADB_TIMEOUT_S", "6"))



def run_cmd(cmd: list[str], *, timeout_s: Optional[float] = None) -> tuple[int, str]:
    try:
        p = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout_s,
        )
        return p.returncode, (p.stdout or "").strip()
    except subprocess.TimeoutExpired:
        return 124, f"timeout after {timeout_s}s: {' '.join(cmd)}"


def run_adb(serial: str, args: list[str], *, timeout_s: float = DEFAULT_ADB_TIMEOUT_S) -> tuple[int, str]:
    """统一的 adb 调用：
    - 强制 `-s <serial>`，避免多设备时出现 `more than one device/emulator`
    - 同时设置 ANDROID_SERIAL，兼容某些环境/别名场景
    - 返回 (code, combined_output)
    """
    env = os.environ.copy()
    if serial:
        env["ANDROID_SERIAL"] = serial
    cmd = ["adb"]
    if serial:
        cmd += ["-s", serial]
    cmd += args
    try:
        p = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            timeout=timeout_s,
        )
        out = (p.stdout or "").strip()
        if p.returncode != 0:
            out = ("cmd=" + " ".join(cmd) + "\n" + out).strip()
        return p.returncode, out
    except subprocess.TimeoutExpired:
        return 124, f"timeout after {timeout_s}s: cmd={' '.join(cmd)}"


def list_adb_devices() -> list[str]:
    code, out = run_cmd(["adb", "devices"], timeout_s=DEFAULT_ADB_TIMEOUT_S)
    if code != 0:
        return []
    lines = out.splitlines()
    serials: list[str] = []
    for line in lines[1:]:
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            serials.append(parts[0])
    return serials


def ensure_serial_connected(serial: str) -> tuple[bool, str]:
    """校验当前选择的 serial 是否仍在线。"""
    serial = (serial or "").strip()
    devs = list_adb_devices()
    if not serial:
        return False, f"empty serial, devices={devs}"
    if serial not in devs:
        return False, f"device not connected: serial={serial}, devices={devs}"
    return True, ""


def has_forward_mapping(forward_list_out: str, serial: str, *, port: int = PORT) -> bool:
    """判断 forward --list 输出中是否已存在目标端口映射。"""
    if not forward_list_out or not serial:
        return False
    needle = f"{serial} tcp:{port} tcp:{port}"
    for line in forward_list_out.splitlines():
        if line.strip() == needle:
            return True
    return False


def adb_forward_list_global() -> tuple[int, str]:
    """列出 adb-server 维度的所有 forward（不绑定某个 serial）。"""
    return run_cmd(["adb", "forward", "--list"], timeout_s=2)


def _parse_forward_serials_for_local_port(forward_list_out: str, *, local_port: int) -> list[str]:
    """从 `adb forward --list` 输出中解析出占用指定本地端口的 serial 列表。"""
    out: list[str] = []
    if not forward_list_out:
        return out
    needle = f"tcp:{local_port}"
    for line in forward_list_out.splitlines():
        line = line.strip()
        if not line:
            continue
        # 格式：<serial> tcp:<local> tcp:<remote>
        parts = line.split()
        if len(parts) >= 3 and parts[1] == needle:
            out.append(parts[0])
    return out


def remove_adbd_proxy_forward_if_any() -> Optional[str]:
    """清理错误用法：用 `adb forward tcp:5559 ...` 去代理 adbd 协议。

    这种 forward 会让 `adb connect localhost:5559` 出现自我回环/半通等问题，
    进而导致后续 adb 命令异常（典型：明明带了 -s localhost:5559，仍报 more than one device/emulator）。

    返回：若检测并清理了，返回提示文本；否则返回 None。
    """
    c, out = adb_forward_list_global()
    if c != 0 or not out:
        return None

    serials = _parse_forward_serials_for_local_port(out, local_port=5559)
    if not serials:
        return None

    rms: list[str] = []
    for dev in serials:
        code_rm, out_rm = run_adb(dev, ["forward", "--remove", "tcp:5559"], timeout_s=2)
        rms.append(f"{dev}: rm_code={code_rm}, rm_out={out_rm}")

    # 二次确认（best-effort）
    c2, out2 = adb_forward_list_global()
    remaining = _parse_forward_serials_for_local_port(out2 if c2 == 0 else "", local_port=5559)
    global_rm_line = ""
    if remaining:
        code_g, out_g = run_cmd(["adb", "forward", "--remove", "tcp:5559"], timeout_s=2)
        global_rm_line = f"global: rm_code={code_g}, rm_out={out_g}"
        c3, out3 = adb_forward_list_global()
        remaining = _parse_forward_serials_for_local_port(out3 if c3 == 0 else "", local_port=5559)

    msg = [
        "[auto-fix] 检测到 tcp:5559 的 adb forward（疑似用 forward 代理 adbd），已尝试清理：",
        "  " + "\n  ".join(rms),
    ]
    if global_rm_line:
        msg.append("  " + global_rm_line)
    if remaining:
        msg.append(f"  [warn] 清理后仍残留 tcp:5559 forward, serials={remaining}（建议手动执行：adb -s <serial> forward --remove tcp:5559）")
    return "\n".join(msg)


def adb_forward(serial: str) -> tuple[int, str]:
    """确保本机 tcp:PORT 只转发到当前选择的设备。

    SS4 场景经常同时存在 USB 序列号和 tcp 序列号（localhost:5559）。
    如果两边都保留了 tcp:27183 转发，PC 端实际连到的设备可能不是 GUI 选择的那个，
    会表现为：startservice 是对的，但 ping/list 返回空响应或 no dcs。

    因此这里在设置转发前，先对 *所有设备* 尝试 remove 一次该端口，再为目标设备建立转发。
    """
    serial = (serial or "").strip()

    ok, why = ensure_serial_connected(serial)
    if not ok:
        return 1, why

    # 先清理可能存在的“adbd 代理 forward”（tcp:5559），否则会影响 adb 的稳定性。
    tip = remove_adbd_proxy_forward_if_any()

    # 先清理所有设备上对该本地端口的转发，避免“连到另一台设备”的假象。
    for dev in list_adb_devices():
        run_adb(dev, ["forward", "--remove", f"tcp:{PORT}"], timeout_s=2)

    run_adb(serial, ["forward", "--remove", f"tcp:{PORT}"], timeout_s=2)
    code, out = run_adb(serial, ["forward", f"tcp:{PORT}", f"tcp:{PORT}"], timeout_s=2)
    if tip:
        out = (tip + "\n" + out).strip()
    if code == 0:
        return code, out

    # 若出现 more than one device/emulator：通常是 adb 被 tcp:5559 forward 污染；这里再清理一次并重试。
    if "more than one device" in out:
        tip2 = remove_adbd_proxy_forward_if_any()
        if tip2:
            out = (out + "\n" + tip2).strip()
        code_fix, out_fix = run_adb(serial, ["forward", f"tcp:{PORT}", f"tcp:{PORT}"], timeout_s=2)
        if code_fix == 0:
            return 0, (out + "\n" + out_fix).strip()

    # 兼容极端情况：adb 返回非 0 但 mapping 实际已存在。
    for _ in range(3):
        c_list, o_list = adb_forward_list(serial)
        if c_list == 0 and has_forward_mapping(o_list, serial):
            return 0, (out + "\n[warn] adb forward returned non-zero, but mapping already exists; continue").strip()
        time.sleep(0.05)

    # 最后兜底：用 ANDROID_SERIAL 再尝试一次（部分环境下 -s 仍会异常）。
    if serial:
        env = os.environ.copy()
        env["ANDROID_SERIAL"] = serial
        try:
            p = subprocess.run(
                ["adb", "forward", f"tcp:{PORT}", f"tcp:{PORT}"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=env,
                timeout=2,
            )
            out2 = (p.stdout or "").strip()
            if p.returncode == 0:
                return 0, out2
        except subprocess.TimeoutExpired:
            return 124, f"timeout after 2s: cmd=adb forward tcp:{PORT} tcp:{PORT} (ANDROID_SERIAL={serial})"

    return code, out


def adb_start_service(serial: str) -> tuple[int, str]:
    # 注意：设备端 TextVisibleSpeakRemoteService 默认不做任何事，必须显式携带 enable extra 才会真正启动监听。
    code, out = run_adb(
        serial,
        [
            "shell",
            "am",
            "startservice",
            "-n",
            SERVICE,
            "--ez",
            "text_visible_speak_enable",
            "true",
        ],
        timeout_s=DEFAULT_ADB_TIMEOUT_S,
    )
    if code != 0 and ("Not found" in out or "not found" in out):
        code2, out2 = run_adb(
            serial,
            [
                "shell",
                "am",
                "startservice",
                "--user",
                "0",
                "-n",
                SERVICE,
                "--ez",
                "text_visible_speak_enable",
                "true",
            ],
            timeout_s=DEFAULT_ADB_TIMEOUT_S,
        )
        out = (out + "\n" + out2).strip()
        code = code2
    return code, out


def hint_when_adb_shell_timeout(serial: str, out: str):
    tips = [
        "[TIPS] adb shell timeout (startservice/pm path 可能无法执行)",
        f"  serial={serial}",
        "  可能原因：tcp adb 通道不稳定/设备侧 adbd shell 不可用/连接半断开",
        "  你可以尝试：",
        "    1) 换用另一个可用设备 serial（例如 USB 那个）",
        "    2) 重新连接 tcp 设备：adb disconnect localhost:5559 && adb connect localhost:5559",
        "    3) 或 adb kill-server && adb start-server（会影响所有 adb 会话）",
        f"  raw={out}",
    ]
    return "\n".join(tips)


def is_service_not_in_manifest(out: str) -> bool:
    """判断 am startservice 是否因 Service 未在 AndroidManifest.xml 注册而失败。

    "Not found; no service started" 是 Android am 命令在找不到 Service 组件时的标准输出，
    与包已安装但版本不包含该 Service（如线上/Release 包）的场景高度相关。
    """
    return "Not found; no service started" in (out or "")


def hint_when_service_not_found(serial: str) -> str:
    """当 am startservice 因 Service 未在 manifest 中注册而失败时，给出可操作的提示。"""
    lines = [
        "[原因] am startservice 返回 'Not found; no service started'",
        f"  → 当前 APK（{PKG}）的 AndroidManifest.xml 中未声明 TextVisibleSpeakRemoteService",
        "  常见原因：",
        "    • 设备上装的是 Release/正式包，调试 Service 已被 proguard/配置去除",
        "    • 需要刷入包含该 Service 的 Debug/开发包",
        f"  Service 全名：{SERVICE}",
        "  [继续] 仍将尝试 TCP 直连 —— 若 Service 已通过其他途径运行则仍可响应",
    ]
    return "\n".join(lines)


def adb_forward_list(serial: str) -> tuple[int, str]:
    return run_adb(serial, ["forward", "--list"], timeout_s=2)


def adb_pm_path(serial: str, pkg: str) -> tuple[int, str]:
    return run_adb(serial, ["shell", "pm", "path", pkg], timeout_s=DEFAULT_ADB_TIMEOUT_S)


def send_tcp(payload: dict) -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(2.0)
    s.connect(("127.0.0.1", PORT))
    s.sendall((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
    data = b""
    while not data.endswith(b"\n"):
        chunk = s.recv(4096)
        if not chunk:
            break
        data += chunk
    s.close()
    return data.decode("utf-8", errors="replace").strip()


def send_tcp_with_retry(payload: dict, *, retries: int = 8, delay_s: float = 0.25) -> str:
    """对端口转发/Service 冷启动做容错。

    现象：startservice 后立刻连端口，adb forward 可能先 accept 本地连接、
    但远端 ServerSocket 尚未 listen，导致客户端读到空响应（EOF）。
    """
    last_err: Optional[Exception] = None
    for _ in range(retries):
        try:
            resp = send_tcp(payload)
            if resp.strip():
                return resp
        except Exception as e:
            last_err = e
        import time as _time

        _time.sleep(delay_s)
    if last_err:
        raise last_err
    return ""


def is_no_dcs_yet(resp_obj: Optional[dict]) -> bool:
    if not isinstance(resp_obj, dict):
        return False
    if resp_obj.get("ok") is True:
        return False
    return resp_obj.get("msg") == "no dcs recognition yet"


def send_with_no_dcs_retry(payload: dict, *, retries: int = 6, delay_s: float = 0.25) -> tuple[str, Optional[dict]]:
    """当切屏后 DCS 还没来得及推到 TextVisibleSpeakRemoteService 时，短暂重试。"""
    import time as _time

    last_resp = ""
    last_obj: Optional[dict] = None
    for _ in range(retries):
        last_resp = send_tcp_with_retry(payload)
        obj, _err = safe_json_loads(last_resp)
        last_obj = obj
        if not is_no_dcs_yet(obj):
            return last_resp, last_obj
        _time.sleep(delay_s)
    return last_resp, last_obj


def list_with_auto_poke(screen: str, *, retries: int = 4, delay_s: float = 0.25) -> tuple[str, Optional[dict]]:
    """拉取可执行列表。

    若返回 "no dcs recognition yet"，先发一次 poke（触发 mock windows changed），再重试 list。
    说明：poke 是 best-effort（依赖设备侧新版本 TextVisibleSpeakRemoteService 支持 cmd=poke）。
    """
    last_resp = ""
    last_obj: Optional[dict] = None
    for _ in range(retries):
        last_resp = send_tcp_with_retry({"cmd": "list", "screen": screen})
        obj, _err = safe_json_loads(last_resp)
        last_obj = obj
        if not is_no_dcs_yet(obj):
            return last_resp, last_obj
        # no dcs：尝试 poke 一次
        try:
            send_tcp_with_retry({"cmd": "poke", "screen": screen}, retries=2, delay_s=0.1)
        except Exception:
            pass
        time.sleep(delay_s)
    return last_resp, last_obj


def wait_remote_ready(serial: str, *, timeout_s: float = 2.0) -> tuple[bool, str]:
    """等待设备端 TCP server 真正 ready（通过 ping/pong 探测）。"""
    import time as _time

    deadline = _time.time() + timeout_s
    last = ""
    while _time.time() < deadline:
        try:
            last = send_tcp_with_retry({"cmd": "ping"}, retries=1, delay_s=0.0)
            if last and ("pong" in last or "PONG" in last):
                return True, last
        except Exception as e:
            last = str(e)
        _time.sleep(0.1)
    return False, last


def hint_when_empty_resp(serial: str):
    """当 TCP 返回空响应时，给出可操作的排查提示。"""
    print("\n[TIPS] empty response, please check:")
    print("  1) Service started with enable extra:")
    print(f"     adb -s {serial} shell am startservice -n {SERVICE} --ez text_visible_speak_enable true")
    print("  2) Device logcat (service tag):")
    print(f"     adb -s {serial} logcat -s voice.text_visible_speak.remote")
    print("  3) Port forward:")
    print(f"     adb -s {serial} forward --list | grep tcp:{PORT}")


def safe_json_loads(s: str) -> tuple[Optional[dict], Optional[str]]:
    raw = (s or "").strip()
    if not raw:
        return None, "empty response"
    try:
        obj = json.loads(raw)
    except Exception as e:
        return None, f"json decode failed: {e}; raw={raw[:200]}"
    if not isinstance(obj, dict):
        return None, f"not a json object: type={type(obj)}"
    return obj, None


def uniq_keep_order(items: list[str]) -> list[str]:
    seen = set()
    out: list[str] = []
    for x in items:
        x = x.strip()
        if not x:
            continue
        if x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out




# ─────────────────────────────────────────────────────────────────────────────
#  App  —  主界面（重构版：深色主题 + 彩色日志 + 快捷键 + 状态指示）
# ─────────────────────────────────────────────────────────────────────────────

class App:
    # 日志颜色标签（对应 Text widget tag）
    _LOG_COLORS = {
        "info":    "#d4d4d4",  # 正常文本
        "success": "#4ade80",  # 成功 · 绿色
        "error":   "#f87171",  # 错误 · 红色
        "warn":    "#fbbf24",  # 警告 · 黄色
        "sep":     "#60a5fa",  # 分隔 · 蓝色
        "dim":     "#6b7280",  # 次要 · 深灰
        "cmd":     "#a78bfa",  # 命令 · 紫色
    }

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("可见即可说模拟 — TextVisibleSpeak")
        self.root.minsize(960, 620)
        self.root.geometry("1100x720")

        self._setup_theme()

        # ── 状态变量 ──────────────────────────────────────────────────
        self.serial_var = tk.StringVar()
        # screen_var 存储显示文本如"主驾 (driver)"；发送时通过 get_screen_value() 取英文值
        self.screen_var = tk.StringVar(value=SCREEN_DISPLAY_VALUES[0])
        self.text_var   = tk.StringVar()
        self.search_var = tk.StringVar()
        self.pause_var  = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="就绪")

        # 当前选中的可执行项（用于 cmd=exec 精确执行同 label 多 action）
        self.selected_label:   Optional[str] = None
        self.selected_view_id: Optional[str] = None
        self.selected_action:  Optional[str] = None
        self.selected_xbnf:    Optional[str] = None   # 当前 action 的完整 XBNF 说法

        self.executable_items:  list[dict] = []
        self.executable_labels: list[str]  = []

        self._build_ui()

        self.logln(f"script={__file__}", "dim")
        self.logln(f"adb_timeout={DEFAULT_ADB_TIMEOUT_S}s", "dim")
        self.logln("─" * 60, "sep")
        self.logln("【可信度说明】以下两种模式可信度不同，请注意区分：", "warn")
        self.logln("  🟢 精确执行（从列表选中后发送）→ viewId 直点，100% 可信", "success")
        self.logln("  🟡 文本匹配（手动输入发送）→ DCS 管道匹配，~75% 可信（需与屏幕可见标签完全一致）", "warn")
        self.logln("  \u274c 不可信场景：语义别名（'听歌' != '打开音乐'）/ 动态推荐内容（歌曲名/联系人）", "error")
        self.logln("─" * 60, "sep")

        self.refresh_devices()
        self.update_autocomplete_values()

    # ──────────────────────────────────────────────────────────────────
    # 主题 & 布局
    # ──────────────────────────────────────────────────────────────────

    def _setup_theme(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass

        BG, FG = "#1e1e1e", "#d4d4d4"
        EB  = "#2d2d2d"  # entry bg
        SEL = "#264f78"  # selection bg
        BTN = "#3b3b3b"
        PBTN = "#3b82f6"  # primary button

        self.root.configure(bg=BG)
        self._BG, self._FG, self._EB, self._SEL = BG, FG, EB, SEL

        style.configure("TFrame",    background=BG)
        style.configure("TLabel",    background=BG, foreground=FG)
        style.configure("TButton",   background=BTN, foreground=FG, padding=(8, 4), relief="flat")
        style.map("TButton",
                  background=[("active", "#4a4a4a"), ("pressed", "#555")])
        style.configure("Primary.TButton",
                        background=PBTN, foreground="#fff", padding=(10, 5))
        style.map("Primary.TButton",
                  background=[("active", "#2563eb"), ("pressed", "#1d4ed8"),
                               ("disabled", "#1e3a5f")])
        style.configure("TCombobox", fieldbackground=EB, background=EB,
                        foreground=FG, selectbackground=SEL, arrowcolor=FG)
        style.map("TCombobox",
                  fieldbackground=[("readonly", EB)],
                  selectbackground=[("readonly", SEL)])
        style.configure("TEntry",       fieldbackground=EB, foreground=FG, insertcolor=FG)
        style.configure("TPanedwindow", background=BG)
        style.configure("TCheckbutton", background=BG, foreground=FG)
        style.map("TCheckbutton",
                  background=[("active", BG)],
                  foreground=[("active", "#fff")])

    def _build_ui(self):
        BG, FG, EB, SEL = self._BG, self._FG, self._EB, self._SEL
        root = self.root
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)

        frm = ttk.Frame(root, padding=(10, 8, 10, 4))
        frm.grid(row=0, column=0, sticky="nsew")
        frm.columnconfigure(1, weight=1)

        # ── 行0：设备 ────────────────────────────────────────────────
        ttk.Label(frm, text="ADB 设备：").grid(row=0, column=0, sticky="w")
        self.combo = ttk.Combobox(frm, textvariable=self.serial_var, state="readonly")
        self.combo.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        dev_row = ttk.Frame(frm)
        dev_row.grid(row=0, column=2, sticky="ew", padx=(8, 0))
        ttk.Button(dev_row, text="刷新设备", command=self.refresh_devices).pack(side="left")
        # 连接状态指示灯
        self.conn_var = tk.StringVar(value="⬤ 未选择")
        self.conn_lbl = tk.Label(frm, textvariable=self.conn_var,
                                 bg=BG, fg="#6b7280", font=("SansSerif", 10))
        self.conn_lbl.grid(row=0, column=3, sticky="w", padx=(10, 0))

        # ── 行1：屏幕 ────────────────────────────────────────────────
        ttk.Label(frm, text="屏幕：").grid(row=1, column=0, sticky="w", pady=(6, 0))
        screen_cb = ttk.Combobox(frm, textvariable=self.screen_var,
                                 values=SCREEN_DISPLAY_VALUES, state="readonly")
        screen_cb.grid(row=1, column=1, sticky="ew", pady=(6, 0), padx=(4, 0))
        screen_cb.bind("<<ComboboxSelected>>", lambda e: self.refresh_executables_async())

        # ── 行2：文本输入 + 发送 ──────────────────────────────────────
        ttk.Label(frm, text="发送文本：").grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.text_combo = ttk.Combobox(frm, textvariable=self.text_var, state="normal")
        self.text_combo.grid(row=2, column=1, sticky="ew", pady=(6, 0), padx=(4, 0))
        self.text_combo.bind("<KeyRelease>", self.on_autocomplete_key)
        self.text_combo.bind("<Return>", lambda e: self.on_send())  # Enter 发送

        self.btn_send = ttk.Button(frm, text="发 送  ↵",
                                   style="Primary.TButton", command=self.on_send)
        self.btn_send.grid(row=2, column=2, columnspan=2,
                           padx=(8, 0), pady=(6, 0), sticky="ew")

        # ── 行3：工具栏 ──────────────────────────────────────────────
        ctrl = ttk.Frame(frm)
        ctrl.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        ttk.Button(ctrl, text="▶ 拉取可执行列表",
                   command=self.refresh_executables_async).pack(side="left")
        ttk.Checkbutton(ctrl, text="⏸ 暂停",
                        variable=self.pause_var,
                        command=self.apply_pause).pack(side="left", padx=(12, 0))
        ttk.Button(ctrl, text="⎘ 复制文本",
                   command=self.copy_current_text).pack(side="right")

        # ── 行4：可信度说明栏 ─────────────────────────────────────────
        trust_frm = tk.Frame(frm, bg="#0d1f33", padx=10, pady=6)
        trust_frm.grid(row=4, column=0, columnspan=4, sticky="ew", pady=(6, 0))
        tk.Label(trust_frm, text="⚙ 执行链：",
                 bg="#0d1f33", fg="#60a5fa",
                 font=("SansSerif", 9, "bold")).pack(side="left")
        tk.Label(trust_frm,
                 text="文本 → AsrBean → SsaiDCSEngine.charge() → AccessibilityExecutorImpl",
                 bg="#0d1f33", fg="#475569",
                 font=("SansSerif", 9)).pack(side="left", padx=(4, 0))
        self.trust_var = tk.StringVar(
            value="🟡 文本匹配  ·  可信度 ~75%  ·  需与屏幕可见标签完全一致"
        )
        self.trust_lbl = tk.Label(trust_frm, textvariable=self.trust_var,
                                  bg="#0d1f33", fg="#fbbf24",
                                  font=("SansSerif", 9, "bold"))
        self.trust_lbl.pack(side="right")

        # ── 行5：可执行列表（全宽）──────────────────────────────────
        exec_frm = ttk.Frame(frm, padding=4)
        exec_frm.grid(row=5, column=0, columnspan=4, sticky="nsew", pady=(8, 0))
        frm.rowconfigure(5, weight=2)

        # 标题 + 计数
        hdr_l = ttk.Frame(exec_frm)
        hdr_l.pack(fill="x")
        ttk.Label(hdr_l, text="可执行项", font=("SansSerif", 10, "bold")).pack(side="left")
        self.exec_count_var = tk.StringVar(value="(0)")
        ttk.Label(hdr_l, textvariable=self.exec_count_var,
                  foreground="#9ca3af").pack(side="left", padx=(6, 0))

        # 过滤框
        sf = ttk.Frame(exec_frm)
        sf.pack(fill="x", pady=(6, 0))
        ttk.Label(sf, text="过滤：").pack(side="left")
        se = ttk.Entry(sf, textvariable=self.search_var)
        se.pack(side="left", fill="x", expand=True, padx=(6, 0))
        se.bind("<KeyRelease>", lambda e: self.render_executables())

        # ── pack 顺序说明（side="bottom" 下，先 pack 的在视觉底部）：
        #   1. eb (buttons)   → 绝对底部
        #   2. xbnf_row       → eb 之上（XBNF 说法区）
        #   3. action_row     → xbnf_row 之上（action 选择）
        #   4. lf_l (listbox) → 最后 expand 填充剩余空间（顶部）
        #   视觉从上到下：[listbox][action_row][xbnf_row][eb]

        # 按钮行：先以 side="bottom" pack，确保列表框 expand 时不会把它挤掉
        eb = ttk.Frame(exec_frm)
        eb.pack(fill="x", pady=(6, 0), side="bottom")
        ttk.Button(eb, text="填充", command=self.exec_fill).pack(side="left")
        ttk.Button(eb, text="⎘ 复制", command=self.exec_copy).pack(side="left", padx=(6, 0))
        ttk.Button(eb, text="⎘ 复制ID", command=self.exec_copy_id).pack(side="left", padx=(6, 0))
        ttk.Button(eb, text="▶ 发送", style="Primary.TButton",
                   command=self.exec_send).pack(side="left", padx=(6, 0))
        ttk.Label(eb, text="双击直接发送", foreground="#6b7280").pack(side="right")

        # ── XBNF 说法展示区（side="bottom"，紧挨按钮行之上）
        #   告知测试者：如果用麦克风操作，应该如何朗读该指令
        xbnf_row = ttk.Frame(exec_frm)
        xbnf_row.pack(fill="x", pady=(2, 0), side="bottom")
        ttk.Label(xbnf_row, text="麦克风说法：",
                  foreground="#6b7280").pack(side="left", anchor="nw", pady=(2, 0))
        xbnf_inner = ttk.Frame(xbnf_row)
        xbnf_inner.pack(side="left", fill="x", expand=True, padx=(4, 0))
        self.xbnf_text = tk.Text(
            xbnf_inner, height=3,
            bg="#1a1a1a", fg="#a5b4fc",
            font=("Monospace", 8),
            wrap="word", state="disabled",
            borderwidth=0, highlightthickness=1,
            highlightcolor="#3f3f46",
            cursor="arrow",
        )
        xbnf_sb = ttk.Scrollbar(xbnf_inner, orient="vertical", command=self.xbnf_text.yview)
        self.xbnf_text.configure(yscrollcommand=xbnf_sb.set)
        self.xbnf_text.pack(side="left", fill="x", expand=True)
        xbnf_sb.pack(side="right", fill="y")
        ttk.Button(xbnf_row, text="⎘ 复制说法",
                   command=self.copy_xbnf).pack(side="right", padx=(8, 0), anchor="nw", pady=(1, 0))

        # ── 操作选择行：side="bottom"，在 xbnf_row 之上
        action_row = ttk.Frame(exec_frm)
        action_row.pack(fill="x", pady=(4, 0), side="bottom")
        ttk.Label(action_row, text="操作 (Action)：").pack(side="left")
        self.action_var = tk.StringVar()
        self.action_combo = ttk.Combobox(action_row, textvariable=self.action_var,
                                         state="disabled", width=28)
        self.action_combo.pack(side="left", padx=(6, 0))
        # 使用 after_idle 延迟回调，避免 Linux Tcl/Tk <<ComboboxSelected>> 引发的
        # "Tcl_Release couldn't find reference" 崩溃（已知 Tkinter 版本 bug）
        self.action_combo.bind(
            "<<ComboboxSelected>>",
            lambda e: self.root.after_idle(self.on_action_select)
        )
        ttk.Label(action_row, text="← 选中列表项后可切换执行动作",
                  foreground="#6b7280").pack(side="left", padx=(10, 0))

        # 列表框：最后 pack + expand=True，填充 header/filter 下方到 action_row 上方的剩余空间
        lf_l = ttk.Frame(exec_frm)
        lf_l.pack(fill="both", expand=True, pady=(6, 0))
        self.exec_list = tk.Listbox(lf_l, bg=EB, fg=FG,
                                    selectbackground=SEL, selectforeground="#fff",
                                    borderwidth=0, highlightthickness=1,
                                    highlightcolor="#3f3f46", activestyle="none",
                                    exportselection=False)
        sb_el = ttk.Scrollbar(lf_l, orient="vertical", command=self.exec_list.yview)
        self.exec_list.configure(yscrollcommand=sb_el.set)
        self.exec_list.pack(side="left", fill="both", expand=True)
        sb_el.pack(side="right", fill="y")
        self.exec_list.bind("<<ListboxSelect>>", self.on_exec_select)
        self.exec_list.bind("<Double-Button-1>", lambda e: self.exec_send())

        # ── 行6：日志标题 ────────────────────────────────────────────
        log_hdr = ttk.Frame(frm)
        log_hdr.grid(row=6, column=0, columnspan=4, sticky="ew", pady=(8, 2))
        ttk.Label(log_hdr, text="运行日志", font=("SansSerif", 9, "bold")).pack(side="left")
        ttk.Button(log_hdr, text="清空日志", command=self.clear_log).pack(side="right")

        # ── 行6：日志区 ──────────────────────────────────────────────
        self.log = tk.Text(
            frm, height=8,
            bg="#111111", fg="#d4d4d4",
            insertbackground="#d4d4d4",
            font=("Monospace", 9),
            borderwidth=0, highlightthickness=1,
            highlightcolor="#3f3f46", wrap="word",
        )
        log_sb = ttk.Scrollbar(frm, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=log_sb.set)
        self.log.grid(row=7, column=0, columnspan=3, sticky="nsew")
        log_sb.grid(row=7, column=3, sticky="ns")
        frm.rowconfigure(7, weight=1)

        for tag, color in self._LOG_COLORS.items():
            self.log.tag_configure(tag, foreground=color)

        # 右键菜单：清空日志
        log_menu = tk.Menu(root, tearoff=0, bg="#2d2d2d", fg=FG)
        log_menu.add_command(label="清空日志", command=self.clear_log)
        self.log.bind("<Button-3>", lambda e: log_menu.tk_popup(e.x_root, e.y_root))

        # ── 行7：状态栏 ──────────────────────────────────────────────
        self.status_bar = tk.Label(
            frm, textvariable=self.status_var,
            bg="#252526", fg="#9ca3af",
            anchor="w", padx=8, pady=3,
            font=("SansSerif", 9),
        )
        self.status_bar.grid(row=8, column=0, columnspan=4, sticky="ew", pady=(4, 0))

    # ──────────────────────────────────────────────────────────────────
    # 日志 & 状态
    # ──────────────────────────────────────────────────────────────────

    def logln(self, s: str, level: str = "info"):
        """向日志区写一行；线程安全（支持从子线程调用）。"""
        def _do():
            tag = level if level in self._LOG_COLORS else "info"
            self.log.insert("end", s + "\n", tag)
            self.log.see("end")
        self.root.after(0, _do)

    def set_status(self, msg: str, ok: Optional[bool] = None):
        """更新状态栏。ok=True→绿，ok=False→红，None→灰。"""
        color = "#4ade80" if ok is True else "#f87171" if ok is False else "#9ca3af"
        def _do():
            self.status_var.set(msg)
            self.status_bar.config(fg=color)
        self.root.after(0, _do)

    def clear_log(self):
        self.log.delete("1.0", "end")
        self.logln("日志已清空", "dim")

    # ──────────────────────────────────────────────────────────────────
    # 设备 & 屏幕
    # ──────────────────────────────────────────────────────────────────

    def get_screen_value(self) -> str:
        """将显示文本（如"主驾 (driver)"）转换为实际 screen 值（如"driver"）。"""
        display = self.screen_var.get()
        return SCREEN_DISPLAY_TO_VALUE.get(display, display)

    def refresh_devices(self):
        serials = list_adb_devices()
        self.combo["values"] = serials
        if serials and (self.serial_var.get() not in serials):
            # SS4 场景：通常需要走 tcp 设备（localhost:5559）才能正确执行/操作。
            if "localhost:5559" in serials:
                self.serial_var.set("localhost:5559")
            else:
                self.serial_var.set(serials[0])
        if serials:
            self.logln(f"已刷新设备：{serials}", "info")
        else:
            self.logln("⚠️ 未检测到 adb 设备，请确认设备已连接", "warn")
        self._update_conn_indicator()

    def _update_conn_indicator(self):
        serial = self.serial_var.get().strip()
        if not serial:
            self.conn_var.set("⬤ 未选择")
            self.conn_lbl.config(fg="#6b7280")
            return
        devs = list_adb_devices()
        if serial in devs:
            self.conn_var.set(f"⬤ {serial}")
            self.conn_lbl.config(fg="#4ade80")
        else:
            self.conn_var.set(f"⬤ 离线  {serial}")
            self.conn_lbl.config(fg="#f87171")

    def apply_pause(self):
        paused = self.pause_var.get()
        if paused:
            self.btn_send.state(["disabled"])
            self.logln("⏸ 已暂停：发送/刷新功能暂时停用", "warn")
            self.set_status("⏸ 已暂停")
        else:
            self.btn_send.state(["!disabled"])
            self.logln("▶ 已恢复：发送/刷新功能恢复正常", "success")
            self.set_status("就绪")

    def copy_current_text(self):
        txt = self.text_var.get().strip()
        if not txt:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(txt)
        self.logln(f"⎘ 已复制：{txt}", "dim")
        self.set_status(f"已复制：{txt[:50]}")

    # ──────────────────────────────────────────────────────────────────
    # 自动补全
    # ──────────────────────────────────────────────────────────────────

    def update_autocomplete_values(self):
        values = uniq_keep_order(self.executable_labels)
        self.text_combo["values"] = values

    def on_autocomplete_key(self, _event=None):
        prefix = self.text_var.get().strip()
        if not prefix:
            self.update_autocomplete_values()
            return
        all_vals = uniq_keep_order(self.executable_labels)
        self.text_combo["values"] = [x for x in all_vals if prefix in x]
        # 若用户手动修改了文字，不再命中精确选中的 label，重置可信度提示
        if self.selected_label and prefix != self.selected_label:
            self.selected_label = self.selected_view_id = self.selected_action = None
            if hasattr(self, "action_combo"):
                self.action_combo.configure(state="disabled")
                self.action_combo.set("")
            self._reset_trust_indicator()

    # ──────────────────────────────────────────────────────────────────
    # 可执行列表
    # ──────────────────────────────────────────────────────────────────

    def refresh_executables_async(self):
        if self.pause_var.get():
            self.logln("⏸ 已暂停：跳过拉取", "warn")
            return
        serial = self.serial_var.get().strip()
        if not serial:
            self.logln("⚠️ 请先选择 adb 设备", "warn")
            return
        screen = self.get_screen_value()
        screen_display = self.screen_var.get()

        def worker():
            self.logln("─" * 50, "sep")
            self.logln(f"▶ 拉取可执行列表  serial={serial}  screen={screen} ({screen_display})", "cmd")
            self.set_status(f"拉取中... {screen_display}")

            code, out = adb_forward(serial)
            if code == 0:
                self.logln("  adb forward ✓", "success")
            else:
                self.logln(f"  adb forward ✗ code={code}", "error")
                self.logln(f"  {out}", "warn")

            svc_not_found = False
            code, out = adb_start_service(serial)
            if code == 0:
                self.logln("  startservice ✓", "success")
            elif code == 124:
                self.logln(hint_when_adb_shell_timeout(serial, out), "warn")
                self.set_status("拉取失败：adb shell 超时", False)
                return
            else:
                self.logln(f"  startservice ✗ code={code}", "error")
                self.logln(f"  {out}", "dim")
                svc_not_found = is_service_not_in_manifest(out)
                if svc_not_found:
                    self.logln(hint_when_service_not_found(serial), "warn")
                else:
                    c3, o3 = adb_pm_path(serial, PKG)
                    self.logln(f"  pm path: code={c3}  {o3}", "dim")
                    if "package:" not in o3:
                        self.logln("  ⚠️ 未检测到目标包，设备可能未安装对应 App", "warn")
                    if c3 == 124:
                        self.logln(hint_when_adb_shell_timeout(serial, o3), "warn")
                        self.set_status("拉取失败：adb shell 超时", False)
                        return

            try:
                ok_ready, last = wait_remote_ready(serial)
                if not ok_ready:
                    self.logln(f"  远端未就绪（可能正常）：{last}", "warn")
                resp, obj = list_with_auto_poke(screen)
                if not resp:
                    if svc_not_found:
                        self.logln("  ✗ TCP 无响应（符合预期）：Service 未在当前 APK manifest 中注册，无法启动", "error")
                        self.logln(f"  → 请安装包含 {SERVICE} 的版本（通常为 Debug/开发包）", "error")
                    else:
                        self.logln(f"  ⚠️ 空响应 — 请检查 Service 是否监听 tcp:{PORT}", "warn")
                    self.set_status(
                        "拉取失败：Service 未在 APK 中注册" if svc_not_found else "拉取失败：空响应",
                        False,
                    )
                    return
                if obj is None:
                    _obj, err = safe_json_loads(resp)
                    if err:
                        self.logln(f"  解析失败：{err}", "error")
                        self.set_status("拉取失败：响应解析错误", False)
                        return
                if obj is None or (not obj.get("ok")):
                    self.logln(f"  列表返回失败：{resp[:200]}", "error")
                    self.set_status("拉取失败", False)
                    return
                items = obj.get("items", [])
                if not isinstance(items, list):
                    self.logln(f"  items 格式异常：{resp[:100]}", "error")
                    self.set_status("拉取失败：数据格式异常", False)
                    return
                self.executable_items  = items
                self.executable_labels = uniq_keep_order(
                    [it.get("label", "") for it in items if isinstance(it, dict)]
                )
                self.root.after(0, self.after_executable_update)
                self.set_status(f"✓ 已获取 {len(items)} 条可执行项  ({screen_display})", True)
            except Exception as e:
                self.logln(f"  列表拉取异常：{e}", "error")
                self.set_status(f"拉取失败：{e}", False)

        threading.Thread(target=worker, daemon=True).start()

    def after_executable_update(self):
        self.render_executables()
        self.update_autocomplete_values()
        self.logln(f"✓ 可执行列表已更新：共 {len(self.executable_items)} 条", "success")

    def render_executables(self):
        self.exec_list.delete(0, "end")
        keyword = self.search_var.get().strip()
        count = 0
        for it in self.executable_items:
            if not isinstance(it, dict):
                continue
            label   = str(it.get("label",  ""))
            view_id = str(it.get("viewId", ""))
            action  = str(it.get("action", ""))
            scene   = str(it.get("scene",  ""))
            line    = f"{label} | {action} | {view_id} | {scene}"
            if keyword and keyword not in line:
                continue
            self.exec_list.insert("end", line)
            count += 1
        self.exec_count_var.set(f"({count})")

    # ──────────────────────────────────────────────────────────────────
    # 列表选中 / 操作辅助
    # ──────────────────────────────────────────────────────────────────

    def _listbox_single_index(self, lb: tk.Listbox):
        sel = lb.curselection()
        if not sel:
            return None
        return int(sel[0])

    def _parse_exec_line(self, line: str) -> str:
        parts = [p.strip() for p in line.split("|")]
        return parts[0] if parts else line

    def _parse_exec_fields(self, line: str) -> tuple[str, str, str, str]:
        parts = [p.strip() for p in (line or "").split("|")]
        while len(parts) < 4:
            parts.append("")
        return parts[0], parts[1], parts[2], parts[3]

    def on_exec_select(self, _evt=None):
        idx = self._listbox_single_index(self.exec_list)
        if idx is None:
            self.selected_label = self.selected_view_id = self.selected_action = None
            self.action_combo.configure(state="disabled")
            self.action_combo.set("")
            self._reset_trust_indicator()
            return
        line  = self.exec_list.get(idx)
        label, action, view_id, _scene = self._parse_exec_fields(line)

        # 同一条目重复触发时（如 double-click 先触发 ListboxSelect）保留用户已选的 action
        same_item = (
            self.selected_label == (label or None) and
            self.selected_view_id == (view_id or None)
        )
        prev_display = self.action_var.get() if same_item else ""

        self.selected_label   = label   or None
        self.selected_view_id = view_id or None
        if label:
            self.text_var.set(label)

        # ── 填充 action 下拉框 ──────────────────────────────────────
        pairs = self._get_action_pairs_for_item(view_id, label)
        if pairs:
            display_list = [self._action_display(k, v) for k, v in pairs]
            self.action_combo.configure(state="readonly")
            self.action_combo["values"] = display_list
            # 同一条目且当前已选值仍有效时保留；否则默认选 default action
            if same_item and prev_display in display_list:
                chosen = prev_display
            else:
                chosen = next(
                    (d for d in display_list if self._action_key_from_display(d) == action),
                    display_list[0],
                )
            self.action_var.set(chosen)
            self.selected_action = self._action_key_from_display(chosen)
        elif action:
            self.action_combo.configure(state="readonly")
            self.action_combo["values"] = [action]
            self.action_var.set(action)
            self.selected_action = action
        else:
            self.action_combo.configure(state="disabled")
            self.action_combo.set("")
            self.selected_action = None

        # ── 更新可信度指示 ──────────────────────────────────────────
        act_key = self.selected_action or ""
        if label and view_id and act_key:
            self.trust_var.set(
                f"🟢 精确执行（viewId 直点）·  可信度 100%  ·  {label}  [{act_key}]"
            )
            self.trust_lbl.config(fg="#4ade80")
        else:
            self._reset_trust_indicator()

        # ── 刷新 XBNF 说法展示区 ─────────────────────────────────────
        xbnf = next((v for k, v in pairs if k == act_key), "") if pairs else ""
        self._update_xbnf_display(xbnf)

    def _get_action_pairs_for_item(self, view_id, label):
        """返回 [(action_key, xbnf_desc), ...] 列表（default action 排第一）。
        兼容两种服务端格式：
          新格式：actions = [{"key": "open", "xbnf": "..."}, ...]
          旧格式：actions = ["open", "play", ...]（仅 key 字符串）
        """
        for it in self.executable_items:
            if not isinstance(it, dict):
                continue
            if it.get("viewId", "") == view_id and it.get("label", "") == label:
                actions_raw = it.get("actions", [])
                if isinstance(actions_raw, list) and actions_raw:
                    pairs = []
                    for a in actions_raw:
                        if isinstance(a, dict):
                            k = str(a.get("key", "")).strip()
                            v = str(a.get("xbnf", "")).strip()
                            if k:
                                pairs.append((k, v))
                        elif isinstance(a, str) and a.strip():
                            pairs.append((a.strip(), ""))
                    if pairs:
                        return pairs
                # 兼容旧服务端：仅有单 action 字段
                act = str(it.get("action", "")).strip()
                return [(act, "")] if act else []
        return []

    @staticmethod
    def _action_display(key, xbnf):
        """将 (key, xbnf) 格式化为 Combobox 显示字符串。
        例："open（打开|开启）" 或 "open"（无 xbnf 时）
        """
        if xbnf:
            return "{}（{}）".format(key, xbnf)
        return key

    @staticmethod
    def _action_key_from_display(display):
        """从 Combobox 显示字符串中提取实际 action key。
        "open（打开|开启）" → "open"   /   "open" → "open"
        """
        return display.split("（")[0].strip()

    def on_action_select(self, _evt=None):
        """用户在 action combo 中主动切换 action 时更新 selected_action 并刷新 XBNF 展示。"""
        display = self.action_var.get()
        key = self._action_key_from_display(display)
        self.selected_action = key if key else None
        if self.selected_label and self.selected_view_id and key:
            self.trust_var.set(
                "🟢 精确执行（viewId 直点）·  可信度 100%  ·  {}  [{}]".format(
                    self.selected_label, key
                )
            )
            self.trust_lbl.config(fg="#4ade80")
        # 根据新选 action 刷新 XBNF 说法
        pairs = self._get_action_pairs_for_item(
            self.selected_view_id or "", self.selected_label or ""
        )
        xbnf = next((v for k, v in pairs if k == key), "") if pairs else ""
        self._update_xbnf_display(xbnf)

    def exec_fill(self):
        idx = self._listbox_single_index(self.exec_list)
        if idx is None:
            return
        line = self.exec_list.get(idx)
        self.text_var.set(self._parse_exec_line(line))

    def exec_copy(self):
        idx = self._listbox_single_index(self.exec_list)
        if idx is None:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self.exec_list.get(idx))
        self.logln("⎘ 已复制可执行项", "dim")

    def exec_copy_id(self):
        """复制当前选中项的 viewId 到剪贴板。"""
        idx = self._listbox_single_index(self.exec_list)
        if idx is None:
            self.logln("⚠️ 请先选中列表项", "warn")
            return
        line = self.exec_list.get(idx)
        _, _, view_id, _ = self._parse_exec_fields(line)
        if not view_id:
            self.logln("⚠️ 该项无 viewId", "warn")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(view_id)
        self.logln(f"⎘ 已复制 viewId：{view_id}", "dim")
        self.set_status(f"已复制 viewId：{view_id[:80]}")

    def exec_send(self):
        if self.pause_var.get():
            self.logln("⏸ 已暂停：发送被阻止", "warn")
            return
        idx = self._listbox_single_index(self.exec_list)
        if idx is None:
            return
        self.on_exec_select()
        self.text_var.set(self._parse_exec_line(self.exec_list.get(idx)))
        self.on_send()

    # ──────────────────────────────────────────────────────────────────
    # 发送
    # ──────────────────────────────────────────────────────────────────

    def on_send(self):
        serial = self.serial_var.get().strip()
        if not serial:
            self.logln("⚠️ 请先选择 adb 设备", "warn")
            return
        text = self.text_var.get().strip()
        if not text:
            self.logln("⚠️ 文本为空，请输入发送内容", "warn")
            return
        if self.pause_var.get():
            self.logln("⏸ 已暂停：发送被阻止", "warn")
            return

        # 禁用发送按钮防止重复提交
        self.btn_send.state(["disabled"])
        self.btn_send.configure(text="发送中...")

        def worker():
            screen         = self.get_screen_value()
            screen_display = self.screen_var.get()
            self.logln("─" * 50, "sep")
            self.logln(f"▶ 发送  serial={serial}  screen={screen} ({screen_display})", "cmd")
            self.logln(f"  text={text!r}", "cmd")
            self.set_status(f"发送中：{text[:50]}")

            code, out = adb_forward(serial)
            if code == 0:
                self.logln("  adb forward ✓", "success")
            else:
                self.logln(f"  adb forward ✗ code={code}", "error")
                self.logln(f"  {out}", "warn")

            svc_not_found = False
            code, out = adb_start_service(serial)
            if code == 0:
                self.logln("  startservice ✓", "success")
            elif code == 124:
                self.logln(hint_when_adb_shell_timeout(serial, out), "warn")
                self._restore_send_btn()
                self.set_status("发送失败：adb shell 超时", False)
                return
            else:
                self.logln(f"  startservice ✗ code={code}", "error")
                self.logln(f"  {out}", "dim")
                svc_not_found = is_service_not_in_manifest(out)
                if svc_not_found:
                    self.logln(hint_when_service_not_found(serial), "warn")
                else:
                    c3, o3 = adb_pm_path(serial, PKG)
                    self.logln(f"  pm path: code={c3}  {o3}", "dim")
                    if "package:" not in o3:
                        self.logln("  ⚠️ 未检测到目标包，设备可能未安装对应 App", "warn")
                    if c3 == 124:
                        self.logln(hint_when_adb_shell_timeout(serial, o3), "warn")
                        self._restore_send_btn()
                        self.set_status("发送失败：adb shell 超时", False)
                        return

            # action_var 存储显示字符串（如 "open（打开|开启）"），需提取实际 key
            _cur_action = (
                self._action_key_from_display(self.action_var.get().strip())
                if hasattr(self, "action_var") else ""
            )
            use_exec = (
                self.selected_view_id
                and self.selected_label
                and _cur_action
                and (self.selected_label == text)
            )
            if use_exec:
                payload = {
                    "cmd":      "exec",
                    "screen":   screen,
                    "location": default_location_by_screen(screen),
                    "viewId":   self.selected_view_id,
                    "label":    self.selected_label,
                    "action":   _cur_action,
                }
                self.logln(f"  模式：精确执行 (cmd=exec  viewId={self.selected_view_id}  action={_cur_action})", "success")
                self.logln(f"  🟢 可信度 100%：viewId 直接定位 UI 控件并执行「{_cur_action}」，与真实语音执行完全等价", "success")
            else:
                payload = {
                    "text":     text,
                    "location": default_location_by_screen(screen),
                    "screen":   screen,
                }
                self.logln("  模式：文本匹配 (cmd=text → AsrBean → SsaiDCSEngine → Accessibility)", "dim")
                self.logln("  🟡 可信度 ~75%：需与屏幕可见 slotLabel 完全一致；动态推荐/语义别名场景不适用", "warn")

            try:
                ok_ready, last = wait_remote_ready(serial)
                if not ok_ready:
                    self.logln(f"  远端未就绪（可能正常）：{last}", "warn")

                resp, obj = send_with_no_dcs_retry(payload)
                if not resp:
                    if svc_not_found:
                        self.logln("  ✗ TCP 无响应（符合预期）：Service 未在当前 APK manifest 中注册，无法启动", "error")
                        self.logln(f"  → 请安装包含 {SERVICE} 的版本（通常为 Debug/开发包）", "error")
                    else:
                        self.logln(f"  ⚠️ 空响应 — 请检查 Service 是否监听 tcp:{PORT}", "warn")
                    self._restore_send_btn()
                    self.set_status(
                        "发送失败：Service 未在 APK 中注册" if svc_not_found else "发送失败：空响应",
                        False,
                    )
                    return

                if obj is None:
                    _obj, err = safe_json_loads(resp)
                    if err:
                        self.logln(f"  响应解析失败：{err}", "error")
                        self._restore_send_btn()
                        self.set_status("发送失败：响应解析错误", False)
                        return
                elif not obj.get("ok"):
                    msg = obj.get("msg", "unknown")
                    self.logln(f"  ✗ 服务返回失败：{msg}", "error")
                    self.logln(f"  raw={resp[:200]}", "dim")
                    self._restore_send_btn()
                    self.set_status(f"发送失败：{msg}", False)
                    return
                else:
                    self.logln(f"  ✓ 发送成功！resp={resp[:120]}", "success")
                    self._restore_send_btn()
                    self.set_status(f"✓ 已发送：{text[:60]}", True)
                    return

                # 兜底（obj is None 但 raw 有内容）
                self.logln(f"  resp={resp[:200]}", "dim")
                self._restore_send_btn()
                self.set_status(f"已发送（状态未知）：{text[:40]}")

            except Exception as e:
                self.logln(f"  发送异常：{e}", "error")
                c2, o2 = adb_forward_list(serial)
                self.logln(f"  adb forward --list: code={c2}  {o2}", "dim")
                self._restore_send_btn()
                self.set_status(f"发送失败：{e}", False)

        threading.Thread(target=worker, daemon=True).start()

    def _update_xbnf_display(self, xbnf: str):
        """更新"麦克风说法"展示区内容。
        xbnf 为完整 XBNF 语法字符串；空时尝试根据 label/action 自动生成提示。
        自动生成的提示以浅灰色显示，以与真实 XBNF 数据（紫色）区分。
        """
        if not hasattr(self, "xbnf_text"):
            return

        # 若 xbnf 为空，根据当前 action 和 label 自动生成说法提示
        auto_generated = False
        if not xbnf:
            act = getattr(self, "selected_action", "") or ""
            lbl = getattr(self, "selected_label", "") or ""
            if act.startswith("slide_direction"):
                # 滑动类：按方向生成固定说法
                _slide_hint = {
                    "slide_direction_up_page": "上翻页|向上翻页|上一页",
                    "slide_direction_up":      "上滑|向上滑动",
                    "slide_direction_down":    "下翻页|向下翻页|下一页",
                    "slide_direction_left":    "左滑|向左滑动",
                    "slide_direction_right":   "右滑|向右滑动",
                }
                xbnf = _slide_hint.get(act, act)
                auto_generated = True
            elif lbl:
                # 点击/播放类：根据 label 生成
                if act == "play":
                    xbnf = f"播放{lbl}|{lbl}"
                else:
                    xbnf = f"{lbl}|点击{lbl}|打开{lbl}"
                auto_generated = True

        self.xbnf_text.configure(state="normal")
        self.xbnf_text.delete("1.0", "end")
        if xbnf:
            self.xbnf_text.insert("1.0", xbnf)
            # 自动生成的说法用浅灰色，真实 XBNF 用紫色
            self.xbnf_text.configure(fg="#9ca3af" if auto_generated else "#a5b4fc")
            self.selected_xbnf = xbnf
        else:
            self.xbnf_text.insert("1.0", "（该动作暂无 XBNF 说法数据）")
            self.xbnf_text.configure(fg="#4b5563")
            self.selected_xbnf = None
        self.xbnf_text.configure(state="disabled")

    def copy_xbnf(self):
        """复制当前 action 的 XBNF 说法到剪贴板。"""
        xbnf = self.selected_xbnf
        if not xbnf:
            self.logln("⚠️ 当前无 XBNF 说法数据", "warn")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(xbnf)
        preview = xbnf[:60] + ("…" if len(xbnf) > 60 else "")
        self.logln(f"⎘ 已复制 XBNF 说法（{len(xbnf)} 字符）", "dim")
        self.set_status(f"已复制说法：{preview}")

    def _restore_send_btn(self):
        """恢复发送按钮文字和可用状态（线程安全）。"""
        def _do():
            self.btn_send.configure(text="发 送  ↵")
            if not self.pause_var.get():
                self.btn_send.state(["!disabled"])
        self.root.after(0, _do)

    def _reset_trust_indicator(self):
        """重置为文本匹配模式的可信度提示（线程安全）。"""
        def _do():
            self.trust_var.set(
                "🟡 文本匹配（DCS 管道）·  可信度 ~75%  ·  输入需与屏幕可见标签完全一致"
            )
            self.trust_lbl.config(fg="#fbbf24")
        self.root.after(0, _do)


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
