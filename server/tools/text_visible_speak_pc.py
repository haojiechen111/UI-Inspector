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

# 不能写插件目录：统一落到用户目录
APP_DATA_DIR = os.path.join(os.path.expanduser("~"), ".carui_inspector", "text_visible_speak")
HISTORY_PATH = os.path.join(APP_DATA_DIR, "history.json")
PHRASES_PATH = os.path.join(APP_DATA_DIR, "phrases.json")


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


def ensure_data_dir():
    os.makedirs(APP_DATA_DIR, exist_ok=True)


def load_json_list(path: str) -> list[str]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [str(x) for x in data if str(x).strip()]
    except Exception:
        pass
    return []


def save_json_list(path: str, data: list[str]):
    ensure_data_dir()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


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


def merge_autocomplete(executable_labels: list[str], history: list[str], phrases: list[str]) -> list[str]:
    return uniq_keep_order(phrases + history + executable_labels)


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Text Visible Speak (PC)")

        self.serial_var = tk.StringVar()
        self.screen_var = tk.StringVar(value="driver")
        self.text_var = tk.StringVar()
        self.search_var = tk.StringVar()
        self.pause_var = tk.BooleanVar(value=False)

        # 当前选中的可执行项（用于 cmd=exec 精确执行同 label 多 action）
        self.selected_label: Optional[str] = None
        self.selected_view_id: Optional[str] = None
        self.selected_action: Optional[str] = None

        self.executable_items: list[dict] = []
        self.executable_labels: list[str] = []
        self.history: list[str] = load_json_list(HISTORY_PATH)
        self.phrases: list[str] = load_json_list(PHRASES_PATH)

        frm = ttk.Frame(root, padding=10)
        frm.grid(row=0, column=0, sticky="nsew")

        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        frm.columnconfigure(1, weight=1)

        ttk.Label(frm, text="ADB设备:").grid(row=0, column=0, sticky="w")
        self.combo = ttk.Combobox(frm, textvariable=self.serial_var, values=[], state="readonly")
        self.combo.grid(row=0, column=1, sticky="ew")
        ttk.Button(frm, text="刷新", command=self.refresh_devices).grid(row=0, column=2, padx=(8, 0))

        ttk.Label(frm, text="screen:").grid(row=1, column=0, sticky="w", pady=(8, 0))
        screen_cb = ttk.Combobox(
            frm,
            textvariable=self.screen_var,
            values=["driver", "passenger", "rear", "rear_w"],
            state="readonly",
        )
        screen_cb.grid(row=1, column=1, sticky="ew", pady=(8, 0))
        screen_cb.bind("<<ComboboxSelected>>", lambda e: self.refresh_executables_async())

        ttk.Label(frm, text="文本:").grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.text_combo = ttk.Combobox(frm, textvariable=self.text_var, values=[], state="normal")
        self.text_combo.grid(row=2, column=1, sticky="ew", pady=(8, 0))
        self.text_combo.bind("<KeyRelease>", self.on_autocomplete_key)

        self.btn_send = ttk.Button(frm, text="发送", command=self.on_send)
        self.btn_send.grid(row=2, column=2, padx=(8, 0), pady=(8, 0))

        ctrl = ttk.Frame(frm)
        ctrl.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        ctrl.columnconfigure(1, weight=1)
        ttk.Button(ctrl, text="拉取可执行列表", command=self.refresh_executables_async).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(ctrl, text="Pause（暂停刷新/发送）", variable=self.pause_var, command=self.apply_pause).grid(
            row=0, column=1, sticky="w", padx=(12, 0)
        )
        ttk.Button(ctrl, text="复制当前文本", command=self.copy_current_text).grid(row=0, column=2, sticky="e")

        paned = ttk.Panedwindow(frm, orient=tk.HORIZONTAL)
        paned.grid(row=4, column=0, columnspan=3, sticky="nsew", pady=(10, 0))
        frm.rowconfigure(4, weight=1)

        left = ttk.Frame(paned, padding=4)
        mid = ttk.Frame(paned, padding=4)
        right = ttk.Frame(paned, padding=4)
        paned.add(left, weight=2)
        paned.add(mid, weight=1)
        paned.add(right, weight=1)

        ttk.Label(left, text="可执行数据（label/viewId/action）").pack(anchor="w")
        search_row = ttk.Frame(left)
        search_row.pack(fill="x", pady=(6, 0))
        ttk.Label(search_row, text="过滤:").pack(side="left")
        search_entry = ttk.Entry(search_row, textvariable=self.search_var)
        search_entry.pack(side="left", fill="x", expand=True, padx=(6, 0))
        search_entry.bind("<KeyRelease>", lambda e: self.render_executables())

        self.exec_list = tk.Listbox(left, height=12)
        self.exec_list.pack(fill="both", expand=True, pady=(6, 0))
        self.exec_list.bind("<<ListboxSelect>>", self.on_exec_select)

        exec_btns = ttk.Frame(left)
        exec_btns.pack(fill="x", pady=(6, 0))
        ttk.Button(exec_btns, text="填充", command=self.exec_fill).pack(side="left")
        ttk.Button(exec_btns, text="复制", command=self.exec_copy).pack(side="left", padx=(8, 0))
        ttk.Button(exec_btns, text="发送", command=self.exec_send).pack(side="left", padx=(8, 0))

        ttk.Label(mid, text="历史记录").pack(anchor="w")
        self.hist_list = tk.Listbox(mid, height=12)
        self.hist_list.pack(fill="both", expand=True, pady=(6, 0))
        self.hist_list.bind("<<ListboxSelect>>", self.on_hist_select)
        hist_btns = ttk.Frame(mid)
        hist_btns.pack(fill="x")
        ttk.Button(hist_btns, text="复制", command=self.hist_copy).pack(side="left")
        ttk.Button(hist_btns, text="发送", command=self.hist_send).pack(side="left", padx=(8, 0))
        ttk.Button(hist_btns, text="清空", command=self.hist_clear).pack(side="left", padx=(8, 0))

        ttk.Label(right, text="常用语").pack(anchor="w")
        self.phrase_list = tk.Listbox(right, height=12)
        self.phrase_list.pack(fill="both", expand=True, pady=(6, 0))
        self.phrase_list.bind("<<ListboxSelect>>", self.on_phrase_select)

        phrase_edit = ttk.Frame(right)
        phrase_edit.pack(fill="x")
        self.phrase_entry = ttk.Entry(phrase_edit)
        self.phrase_entry.pack(side="left", fill="x", expand=True)
        ttk.Button(phrase_edit, text="添加", command=self.phrase_add).pack(side="left", padx=(8, 0))
        ttk.Button(phrase_edit, text="删除", command=self.phrase_remove).pack(side="left", padx=(8, 0))

        self.log = tk.Text(frm, height=8)
        self.log.grid(row=5, column=0, columnspan=3, sticky="nsew", pady=(10, 0))
        frm.rowconfigure(5, weight=1)

        # 版本/路径信息：用于快速确认你到底在跑哪一份脚本（repo 还是插件目录里的拷贝）
        self.logln(f"script={__file__}")
        self.logln(f"adb_timeout={DEFAULT_ADB_TIMEOUT_S}s (env TEXT_VISIBLE_SPEAK_ADB_TIMEOUT_S)")

        self.refresh_devices()
        self.render_history()
        self.render_phrases()
        self.update_autocomplete_values()

    def logln(self, s: str):
        self.log.insert("end", s + "\n")
        self.log.see("end")

    def refresh_devices(self):
        serials = list_adb_devices()
        self.combo["values"] = serials
        if serials and (self.serial_var.get() not in serials):
            # SS4 场景：通常需要走 tcp 设备（localhost:5559）才能正确执行/操作。
            if "localhost:5559" in serials:
                self.serial_var.set("localhost:5559")
            else:
                self.serial_var.set(serials[0])
        self.logln(f"devices={serials}")

    def apply_pause(self):
        paused = self.pause_var.get()
        if paused:
            self.btn_send.state(["disabled"])
            self.logln("PAUSE: 发送/刷新已暂停")
        else:
            self.btn_send.state(["!disabled"])
            self.logln("PAUSE: 已恢复")

    def copy_current_text(self):
        txt = self.text_var.get().strip()
        if not txt:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(txt)
        self.logln(f"已复制: {txt}")

    def add_history(self, text: str):
        text = text.strip()
        if not text:
            return
        self.history = uniq_keep_order([text] + self.history)[:200]
        save_json_list(HISTORY_PATH, self.history)
        self.render_history()
        self.update_autocomplete_values()

    def render_history(self):
        self.hist_list.delete(0, "end")
        for x in self.history:
            self.hist_list.insert("end", x)

    def render_phrases(self):
        self.phrase_list.delete(0, "end")
        for x in self.phrases:
            self.phrase_list.insert("end", x)

    def phrase_add(self):
        text = self.phrase_entry.get().strip()
        if not text:
            return
        self.phrases = uniq_keep_order([text] + self.phrases)[:200]
        save_json_list(PHRASES_PATH, self.phrases)
        self.phrase_entry.delete(0, "end")
        self.render_phrases()
        self.update_autocomplete_values()

    def phrase_remove(self):
        idx = self._listbox_single_index(self.phrase_list)
        if idx is None:
            return
        val = self.phrase_list.get(idx)
        self.phrases = [x for x in self.phrases if x != val]
        save_json_list(PHRASES_PATH, self.phrases)
        self.render_phrases()
        self.update_autocomplete_values()

    def hist_clear(self):
        self.history = []
        save_json_list(HISTORY_PATH, self.history)
        self.render_history()
        self.update_autocomplete_values()

    def update_autocomplete_values(self):
        values = merge_autocomplete(self.executable_labels, self.history, self.phrases)
        self.text_combo["values"] = values

    def on_autocomplete_key(self, _event=None):
        prefix = self.text_var.get().strip()
        if not prefix:
            self.update_autocomplete_values()
            return
        all_vals = merge_autocomplete(self.executable_labels, self.history, self.phrases)
        filt = [x for x in all_vals if prefix in x]
        self.text_combo["values"] = filt

    def refresh_executables_async(self):
        if self.pause_var.get():
            self.logln("PAUSE: 跳过拉取")
            return
        serial = self.serial_var.get().strip()
        if not serial:
            self.logln("请选择 adb 设备")
            return
        screen = self.screen_var.get()

        def worker():
            self.logln("---")
            self.logln(f"pull executables: serial={serial}, screen={screen}")
            code, out = adb_forward(serial)
            self.logln(f"adb forward: code={code}, out={out}")
            if code != 0:
                c2, o2 = adb_forward_list(serial)
                self.logln(f"adb forward --list: code={c2}, out={o2}")
            code, out = adb_start_service(serial)
            self.logln(f"startservice: code={code}, out={out}")
            if code == 124:
                self.logln(hint_when_adb_shell_timeout(serial, out))
                return
            if code != 0 or ("Not found" in out or "not found" in out):
                c3, o3 = adb_pm_path(serial, PKG)
                self.logln(f"pm path {PKG}: code={c3}, out={o3}")
                self.logln("提示：如果 pm path 有结果但 startservice 'Not found'，通常是 Service 在 Manifest 里 exported=false 或未集成")
                if c3 == 124:
                    self.logln(hint_when_adb_shell_timeout(serial, o3))
                    return

            try:
                ok, last = wait_remote_ready(serial)
                if not ok:
                    self.logln(f"wait remote ready timeout: last={last}")
                resp, obj = list_with_auto_poke(screen)
                if not resp:
                    hint_when_empty_resp(serial)
                if obj is None:
                    _obj, err = safe_json_loads(resp)
                    if err:
                        self.logln(f"list error: {err}")
                        return
                if obj is None or (not obj.get("ok")):
                    self.logln(f"list failed: {resp}")
                    return
                items = obj.get("items", [])
                if not isinstance(items, list):
                    self.logln(f"bad items: {resp}")
                    return
                self.executable_items = items
                self.executable_labels = uniq_keep_order([it.get("label", "") for it in items if isinstance(it, dict)])
                self.root.after(0, self.after_executable_update)
            except Exception as e:
                self.logln(f"list error: {e}")

        threading.Thread(target=worker, daemon=True).start()

    def after_executable_update(self):
        self.render_executables()
        self.update_autocomplete_values()
        self.logln(f"executables updated: {len(self.executable_items)}")

    def render_executables(self):
        self.exec_list.delete(0, "end")
        keyword = self.search_var.get().strip()
        for it in self.executable_items:
            if not isinstance(it, dict):
                continue
            label = str(it.get("label", ""))
            view_id = str(it.get("viewId", ""))
            action = str(it.get("action", ""))
            scene = str(it.get("scene", ""))
            line = f"{label} | {action} | {view_id} | {scene}"
            if keyword and (keyword not in line):
                continue
            self.exec_list.insert("end", line)

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
        # label | action | viewId | scene
        while len(parts) < 4:
            parts.append("")
        return parts[0], parts[1], parts[2], parts[3]

    def on_exec_select(self, _evt=None):
        idx = self._listbox_single_index(self.exec_list)
        if idx is None:
            self.selected_label = None
            self.selected_view_id = None
            self.selected_action = None
            return
        line = self.exec_list.get(idx)
        label, action, view_id, _scene = self._parse_exec_fields(line)
        self.selected_label = label or None
        self.selected_view_id = view_id or None
        self.selected_action = action or None

        # 同步输入框
        if label:
            self.text_var.set(label)

    def exec_fill(self):
        idx = self._listbox_single_index(self.exec_list)
        if idx is None:
            return
        line = self.exec_list.get(idx)
        # 复用选中逻辑（会设置 selected_*）
        self.on_exec_select()
        self.text_var.set(self._parse_exec_line(line))

    def exec_copy(self):
        idx = self._listbox_single_index(self.exec_list)
        if idx is None:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self.exec_list.get(idx))
        self.logln("已复制可执行项")

    def exec_send(self):
        if self.pause_var.get():
            self.logln("PAUSE: 发送已暂停")
            return
        idx = self._listbox_single_index(self.exec_list)
        if idx is None:
            return
        line = self.exec_list.get(idx)
        # 复用选中逻辑（会设置 selected_*）
        self.on_exec_select()
        self.text_var.set(self._parse_exec_line(line))
        self.on_send()

    def on_hist_select(self, _evt=None):
        idx = self._listbox_single_index(self.hist_list)
        if idx is None:
            return
        self.text_var.set(self.hist_list.get(idx))

    def hist_copy(self):
        idx = self._listbox_single_index(self.hist_list)
        if idx is None:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self.hist_list.get(idx))
        self.logln("已复制历史")

    def hist_send(self):
        if self.pause_var.get():
            self.logln("PAUSE: 发送已暂停")
            return
        idx = self._listbox_single_index(self.hist_list)
        if idx is None:
            return
        self.text_var.set(self.hist_list.get(idx))
        self.on_send()

    def on_phrase_select(self, _evt=None):
        idx = self._listbox_single_index(self.phrase_list)
        if idx is None:
            return
        self.text_var.set(self.phrase_list.get(idx))

    def on_send(self):
        serial = self.serial_var.get().strip()
        if not serial:
            self.logln("请选择 adb 设备")
            return
        text = self.text_var.get().strip()
        if not text:
            self.logln("请输入文本")
            return

        if self.pause_var.get():
            self.logln("PAUSE: 发送已暂停")
            return

        def worker():
            self.logln("---")
            self.logln(f"serial={serial}")
            code, out = adb_forward(serial)
            self.logln(f"adb forward: code={code}, out={out}")
            if code != 0:
                c2, o2 = adb_forward_list(serial)
                self.logln(f"adb forward --list: code={c2}, out={o2}")
            code, out = adb_start_service(serial)
            self.logln(f"startservice: code={code}, out={out}")
            if code == 124:
                self.logln(hint_when_adb_shell_timeout(serial, out))
                return
            if code != 0 or ("Not found" in out or "not found" in out):
                c3, o3 = adb_pm_path(serial, PKG)
                self.logln(f"pm path {PKG}: code={c3}, out={o3}")
                self.logln("提示：如果 pm path 有结果但 startservice 'Not found'，通常是 Service 在 Manifest 里 exported=false 或未集成")
                if c3 == 124:
                    self.logln(hint_when_adb_shell_timeout(serial, o3))
                    return

            screen = self.screen_var.get()
            use_exec = (
                self.selected_view_id
                and self.selected_label
                and self.selected_action
                and (self.selected_label == text)
            )
            if use_exec:
                payload = {
                    "cmd": "exec",
                    "screen": screen,
                    "location": default_location_by_screen(screen),
                    "viewId": self.selected_view_id,
                    "label": self.selected_label,
                    "action": self.selected_action,
                }
            else:
                payload = {
                    "text": text,
                    "location": default_location_by_screen(screen),
                    "screen": screen,
                }
            try:
                ok, last = wait_remote_ready(serial)
                if not ok:
                    self.logln(f"wait remote ready timeout: last={last}")
                resp, obj = send_with_no_dcs_retry(payload)
                if not resp:
                    hint_when_empty_resp(serial)
                self.logln(f"resp: {resp}")
                if obj is None:
                    _obj, err = safe_json_loads(resp)
                    if err:
                        self.logln(f"resp parse error: {err}")
                elif not obj.get("ok"):
                    self.logln(f"resp not ok: {resp}")
                self.root.after(0, lambda: self.add_history(text))
            except Exception as e:
                self.logln(f"send failed: {e}")
                c2, o2 = adb_forward_list(serial)
                self.logln(f"adb forward --list: code={c2}, out={o2}")

        threading.Thread(target=worker, daemon=True).start()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
