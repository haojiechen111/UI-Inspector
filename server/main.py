import io
import uvicorn
import os
import sys
import subprocess
import re
from typing import Optional, List, Dict
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import adbutils
from adbutils import adb
from PIL import Image

# Optional dependency for local HTTP probing (best-effort)
try:
    import requests  # type: ignore
except Exception:  # pragma: no cover
    requests = None

app = FastAPI()

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Logging setup
print(f"Startup: sys.path[0]={sys.path[0]}")
print(f"Startup: __file__={__file__}")
try:
    print(f"Startup: CWD={os.getcwd()}")
except Exception as e:
    print(f"Startup: CWD Error={e}")

try:
    # sys.path[0] contains the directory of the script
    script_dir = sys.path[0]
    if not script_dir:
        # Fallback if sys.path[0] is empty
        script_dir = os.path.dirname(os.path.abspath(__file__))
    
    # Force CWD
    if os.path.exists(script_dir):
        os.chdir(script_dir)
        print(f"Fixed CWD to: {script_dir}")
    else:
        print(f"Error: script_dir does not exist: {script_dir}")

except Exception as e:
    print(f"Critical Path Error: {e}")
    # Last ditch effort
    script_dir = "."

static_dir = os.path.join(script_dir, "static")
print(f"Mounting static from: {static_dir}")

if not os.path.exists(static_dir):
    print(f"FATAL: Static dir not found!")
else:
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

# State
current_serial: Optional[str] = None
display_mapping: Dict[str, str] = {}
display_info_cache: List[Dict] = []
# hierarchy cache: key=display id, value=last successful xml
hierarchy_xml_cache: Dict[int, str] = {}
# SS4设备映射表：记住localhost:5559对应的原始SS4设备类型和原始序列号
# key: "localhost:5559", value: {"type": "SS4", "original_serial": "da157e15a1f"}
ss4_localhost_mapping: Dict[str, Dict[str, str]] = {}


def _adb_shell_run(serial: str, cmd: str, timeout: int = 6) -> subprocess.CompletedProcess:
    """Run adb shell and return CompletedProcess (stdout/stderr/returncode)."""
    return subprocess.run(
        ["adb", "-s", serial, "shell", cmd],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _is_accessibility_shell_supported(serial: str) -> bool:
    """Heuristic: whether this serial supports `settings`/`pm` shell commands.

    Some SS4 setups use `localhost:5559` as the real Android shell, while the
    original physical serial may not expose full Android shell commands.
    """
    try:
        r = _adb_shell_run(serial, "settings get secure accessibility_enabled", timeout=4)
        out = (r.stdout or "") + (r.stderr or "")
        if "not found" in out.lower():
            return False
        # returncode 127 often indicates command not found
        if r.returncode == 127:
            return False
        return True
    except Exception:
        return False


def resolve_accessibility_target_serial(serial: str) -> str:
    """辅助服务相关操作需要在“物理设备”上执行。

    对于 SS4 这类会被转换成 localhost:5559 的设备：
    - current_serial 用于截图/输入事件
    - 辅助服务 APK 仍运行在原始物理 serial 上
    """
    if serial == "localhost:5559":
        # 用户常见路径：直接连接 localhost:5559（本进程未走 init-ss4），
        # 这里也尽量自动推断并优先使用可执行 settings/pm 的物理 serial。
        cands = get_accessibility_candidate_serials(serial)
        for s in cands:
            if s != serial and _is_accessibility_shell_supported(s):
                return s
        return serial
    return serial


def _list_adb_device_serials() -> List[str]:
    """Best-effort parse `adb devices` and return online serials."""
    try:
        r = subprocess.run(
            ["adb", "devices"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        out = (r.stdout or "")
        serials: List[str] = []
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                serials.append(parts[0])
        return serials
    except Exception:
        return []


def infer_original_serial_from_localhost_forward() -> Optional[str]:
    """Infer original serial for localhost:5559 from `adb forward --list`.

    Typical SS4 mapping line:
      <orig_serial> tcp:5559 tcp:5557
    """
    try:
        r = subprocess.run(
            ["adb", "forward", "--list"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        out = (r.stdout or "")
        found: List[str] = []
        for line in out.splitlines():
            parts = line.split()
            if len(parts) < 3:
                continue
            dev, local_ep = parts[0], parts[1]
            if local_ep == "tcp:5559":
                found.append(dev)

        for s in found:
            if s and s != "localhost:5559":
                return s
    except Exception:
        pass
    return None


def get_accessibility_candidate_serials(serial: str) -> List[str]:
    """Return candidate serials for accessibility operations.

    SS4 场景可能同时存在：
    - localhost:5559（通常可执行完整 Android shell 命令）
    - original_serial（某些环境下才是真正安装/运行 APK 的 serial）

    为了避免“选错 serial 导致一直显示未运行”，这里返回两者并由上层逐个探测。
    """
    global ss4_localhost_mapping

    if serial != "localhost:5559":
        return [serial]

    cands: List[str] = []

    # 1) 首选：进程内已有映射
    if serial in ss4_localhost_mapping:
        orig = (ss4_localhost_mapping.get(serial) or {}).get("original_serial")
        if orig:
            cands.append(orig)

    # 2) 兜底：forward --list 推断
    inferred = infer_original_serial_from_localhost_forward()
    if inferred:
        cands.append(inferred)

    # 3) 兜底：仅一个非 localhost 在线时尝试它
    non_local = [s for s in _list_adb_device_serials() if s != "localhost:5559"]
    if len(non_local) == 1:
        cands.append(non_local[0])

    # 4) 最后保留 localhost 自身
    cands.append(serial)

    # de-duplicate while preserving order
    seen = set()
    out: List[str] = []
    for s in cands:
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def get_accessibility_target_serial_from_current() -> str:
    """统一计算“辅助服务相关操作”的目标 serial。

    这里必须使用 resolve_accessibility_target_serial，而不要在各处直接用
    ss4_localhost_mapping["localhost:5559"]["original_serial"]。

    原因：SS4 场景下 original_serial 有时不支持 settings/pm 等 shell 命令，
    或者端口转发/HTTP 探测应该对 localhost:5559 生效。
    """
    global current_serial
    if not current_serial:
        return ""
    return resolve_accessibility_target_serial(current_serial)


def pick_accessibility_shell_serial(serial: str) -> str:
    """Pick a serial that can run `settings/pm` shell commands."""
    cands = get_accessibility_candidate_serials(serial)
    for s in cands:
        if _is_accessibility_shell_supported(s):
            return s
    return cands[0] if cands else serial


def _adb_shell(serial: str, cmd: str, timeout: int = 5) -> str:
    """Run adb shell command and return stdout (best-effort)."""
    try:
        res = subprocess.run(
            ["adb", "-s", serial, "shell", cmd],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return (res.stdout or "")
    except Exception:
        return ""


def diagnose_secure_layers(serial: str) -> Dict:
    """Diagnose whether current UI is protected from screenshot.

    We mainly rely on SurfaceFlinger layer flags (isSecure=true / hasProtectedContent=true).
    This is more reliable than FLAG_SECURE in dumpsys window on some OEM builds.
    """
    result: Dict = {
        "serial": serial,
        "resumed_activities": [],
        "secure_layers": [],
        "has_secure_layer": False,
    }

    # 1) top/resumed activities
    try:
        act_out = _adb_shell(serial, "dumpsys activity activities", timeout=5)
        # keep a few lines only
        resumed = []
        for line in act_out.splitlines():
            if "mResumedActivity" in line:
                resumed.append(line.strip())
        result["resumed_activities"] = resumed[-3:]
    except Exception:
        pass

    # 2) SurfaceFlinger secure layer markers
    try:
        sf_out = _adb_shell(serial, "dumpsys SurfaceFlinger", timeout=6)
        layers = []
        # Find blocks like:
        # * Layer 0x... (pkg/Activity#0)
        #   isSecure=true ...
        current_name = None
        for line in sf_out.splitlines():
            if line.startswith("* Layer"):
                # Example: * Layer 0x... (xxx)
                m = re.search(r"\(([^)]+)\)", line)
                current_name = m.group(1) if m else line.strip()
                continue
            if "isSecure=true" in line or "hasProtectedContent=true" in line:
                if current_name:
                    layers.append({
                        "layer": current_name,
                        "flag_line": line.strip(),
                    })
        result["secure_layers"] = layers[:20]
        result["has_secure_layer"] = len(layers) > 0
    except Exception:
        pass

    return result


@app.get("/api/diagnose/secure")
def api_diagnose_secure():
    """Diagnose if current screen is protected from screenshot."""
    global current_serial
    if not current_serial:
        raise HTTPException(status_code=400, detail="Device not connected")
    return diagnose_secure_layers(current_serial)

def _parse_displays_from_dumpsys(dump_out: str) -> List[Dict]:
    """从 dumpsys display 输出中解析逻辑 display 列表（含分辨率）。

    策略优先级：
    1. 解析 mViewports 行（含 type/displayId/分辨率/displayName），过滤 VIRTUAL
       适用于新版 Android（输出格式含 DisplayViewport{...}）
    2. 按 "Display Device\\n" 块拆分，从每块提取 mDisplayId + 分辨率
       适用于 Android 10 旧格式
    3. Fallback：全文 mDisplayId= 扫描（无分辨率，兜底）
    """
    result: List[Dict] = []
    seen: set = set()

    # ── 策略 1：解析 mViewports 行 ────────────────────────────────────
    # 格式：DisplayViewport{type=INTERNAL, ..., displayId=0, ...,
    #         deviceWidth=6296, deviceHeight=1740, displayName=DP_0}
    # VIRTUAL 类型为 scrcpy / 投屏虚拟屏，直接跳过
    viewport_pat = re.compile(
        r"DisplayViewport\{type=(\w+)[^}]*?"
        r"displayId=(\d+)[^}]*?"
        r"deviceWidth=(\d+),\s*deviceHeight=(\d+),\s*displayName=([^,}\s]+)"
    )
    for m in viewport_pat.finditer(dump_out):
        vtype, did, w, h, name = (
            m.group(1), m.group(2), m.group(3), m.group(4), m.group(5).strip()
        )
        if vtype == "VIRTUAL":
            continue  # 过滤 scrcpy 等虚拟屏
        if did in seen:
            continue
        seen.add(did)
        result.append({"id": did, "description": f"Display {did} {w}x{h} [{name}]"})

    if result:
        return sorted(result, key=lambda x: int(x["id"]))

    # ── 策略 2：按 "Display Device" 块切分（Android 10 旧格式）────────
    blocks = re.split(r"Display Device\s+", dump_out)
    for block in blocks[1:]:
        id_m = re.search(r"mDisplayId=(\d+)", block)
        if not id_m:
            continue
        did = id_m.group(1)
        if did in seen:
            continue
        seen.add(did)
        # 分辨率：形如 "1920 x 720," 或 "1920 x 720 "
        res_m = re.search(r"(\d{3,5})\s+x\s+(\d{3,5})", block)
        res_str = f" {res_m.group(1)}x{res_m.group(2)}" if res_m else ""
        result.append({"id": did, "description": f"Display {did}{res_str}"})

    if result:
        return sorted(result, key=lambda x: int(x["id"]))

    # ── 策略 3：Fallback 全文扫描 mDisplayId=N（无分辨率）────────────
    for m in re.finditer(r"mDisplayId=(\d+)", dump_out):
        did = m.group(1)
        if did not in seen:
            seen.add(did)
            result.append({"id": did, "description": f"Display {did}"})

    return sorted(result, key=lambda x: int(x["id"]))


def refresh_display_mapping(serial: str):
    """更新 display_mapping / display_info_cache 全局变量（供截图坐标映射用）。

    返回解析到的 display 列表；解析失败返回 None。
    """
    global display_mapping, display_info_cache
    try:
        # 优先使用简单 dumpsys display 解析
        dump_r = subprocess.run(
            ["adb", "-s", serial, "shell", "dumpsys", "display"],
            capture_output=True, text=True, timeout=8, check=False,
        )
        dump_out = dump_r.stdout or ""
        info_list = _parse_displays_from_dumpsys(dump_out)

        if not info_list:
            return None

        # 补充物理ID映射（最佳努力，不阻塞返回）
        try:
            sf_output = subprocess.run(
                ["adb", "-s", serial, "shell", "dumpsys SurfaceFlinger --display-id"],
                capture_output=True, text=True, timeout=5, check=False,
            ).stdout or ""
            phys_to_name = dict(re.findall(
                r"Display (\d{10,20}).*?displayName=\"([^\"]+)\"", sf_output
            ))
            new_mapping: Dict[str, str] = {}
            # ⚠️ 优先从 DisplayViewport 行提取（同一行内 displayId= 和 uniqueId= 相邻，最可靠）
            # 格式：DisplayViewport{..., displayId=4, ..., uniqueId='local:4630947039749296163', ...}
            # 这台 SS4 设备 screencap -d 只接受物理 uniqueId，不接受 logical displayId
            for m in re.finditer(r"displayId=(\d+)[^}]*?uniqueId='local:(\d+)'", dump_out):
                logical, physical = m.group(1), m.group(2)
                new_mapping[logical] = physical
                print(f"[DISPLAY_MAPPING] 📍 logical {logical} → physical {physical}")
            # fallback：旧格式 mDisplayId=N ... mUniqueId=local:XXXXX（跨行，部分设备适用）
            if not new_mapping:
                _last_logical = None
                _last_physical = None
                for m in re.finditer(r"mDisplayId=(\d+).*?mUniqueId=local:(\d{10,20})", dump_out, re.DOTALL):
                    _last_logical, _last_physical = m.group(1), m.group(2)
                    new_mapping[_last_logical] = _last_physical
                # 用物理屏名称覆盖描述（可选，不覆盖分辨率）
                # 注意：仅当循环至少执行过一次（_last_physical 不为 None）时才访问
                if _last_physical and _last_physical in phys_to_name:
                    name = phys_to_name[_last_physical]
                    for item in info_list:
                        if item["id"] == _last_logical and name:
                            # 保留分辨率后缀
                            res_suffix = re.search(r"(\s+\d+x\d+)$", item["description"])
                            item["description"] = f"{name}{res_suffix.group(1) if res_suffix else ''}"
            if new_mapping:
                display_mapping = new_mapping
        except Exception:
            pass

        display_info_cache = info_list
        return info_list
    except Exception as e:
        print(f"[refresh_display_mapping] Error: {e}")
        return None

# ── SS 设备类型缓存（避免每帧截图都重复 getprop，节省 ADB 往返） ──────────────
_ss_type_cache: Dict[str, Optional[str]] = {}

def detect_ss_device(serial: str) -> Optional[str]:
    """Detect if device is SS series (SS4, SS3, etc.) by checking display.id property.
    Result is cached per serial to avoid redundant ADB calls on every screenshot frame."""
    if serial in _ss_type_cache:
        return _ss_type_cache[serial]
    try:
        # Use getprop directly to get ro.build.display.id
        result = subprocess.run(
            ["adb", "-s", serial, "shell", "getprop", "ro.build.display.id"],
            capture_output=True, text=True, timeout=5
        )
        
        if result.returncode != 0:
            print(f"[SS_DETECT] ❌ Failed to get display.id for {serial}: {result.stderr}")
            return None
            
        output = result.stdout.strip()
        print(f"[SS_DETECT] 📱 Device: {serial}")
        print(f"[SS_DETECT] 📋 Display ID: '{output}'")
        print(f"[SS_DETECT] 🔍 Raw repr: {repr(output)}")
        
        # Convert to uppercase for easier matching
        output_upper = output.upper()
        print(f"[SS_DETECT] 🔠 Uppercase: '{output_upper}'")
        
        # Direct string search - most reliable method
        ss_result: Optional[str] = None
        if 'SS4' in output_upper:
            print(f"[SS_DETECT] ✅✅✅ Detected SS4 device (string match): {serial}")
            ss_result = "SS4"
        elif 'SS3' in output_upper:
            print(f"[SS_DETECT] ✅✅✅ Detected SS3 device (string match): {serial}")
            ss_result = "SS3"
        elif 'SS2' in output_upper:
            print(f"[SS_DETECT] ✅✅✅ Detected SS2 device (string match): {serial}")
            ss_result = "SS2"
        elif 'SS5' in output_upper:
            print(f"[SS_DETECT] ✅✅✅ Detected SS5 device (string match): {serial}")
            ss_result = "SS5"
        else:
            print(f"[SS_DETECT] ❌ No SS device pattern found")
        _ss_type_cache[serial] = ss_result
        return ss_result
    except Exception as e:
        print(f"[SS_DETECT] ⚠️ Exception occurred: {e}")
        import traceback
        traceback.print_exc()
        _ss_type_cache[serial] = None  # cache negative to avoid retry storms
        return None

@app.get("/api/devices")
def get_devices():
    global ss4_localhost_mapping
    try:
        devices = []
        # 如果 adb 设备列表里已经有 localhost:5559（说明端口设备已建立），
        # 那么把 SS4 的“原始物理 serial”隐藏掉，避免 UI 里继续显示“未连接”。
        # 这是一个 UX 规则：SS4 用户只需要操作 localhost:5559 这个入口。
        has_localhost_5559 = any(d.serial == "localhost:5559" for d in adb.device_list())
        for d in adb.device_list():
            model = d.prop.get("ro.product.model", "Unknown")

            # 已经存在 localhost:5559 时：隐藏其他 SS4 物理 serial
            if has_localhost_5559 and d.serial != "localhost:5559":
                ss_type_tmp = detect_ss_device(d.serial)
                if ss_type_tmp == "SS4":
                    print(f"[GET_DEVICES] 🚫 localhost:5559 已存在，隐藏 SS4 物理设备 {d.serial}")
                    continue
            
            # 检查该设备是否已经被初始化为localhost:5559
            # 如果该serial作为original_serial存在于映射表中，说明已被初始化，跳过
            is_already_initialized = False
            for localhost_serial, mapping_info in ss4_localhost_mapping.items():
                if mapping_info.get("original_serial") == d.serial:
                    is_already_initialized = True
                    print(f"[GET_DEVICES] 🚫 跳过已初始化设备 {d.serial} (已转换为 {localhost_serial})")
                    break
            
            # 如果设备已被初始化，不显示在列表中
            if is_already_initialized:
                continue
            
            # 特殊处理：如果是localhost:5559，检查映射表
            if d.serial == "localhost:5559" and d.serial in ss4_localhost_mapping:
                ss_type = ss4_localhost_mapping[d.serial]["type"]  # 从字典中提取type
                print(f"[GET_DEVICES] 从映射表识别 {d.serial} 为 {ss_type}")
            else:
                ss_type = detect_ss_device(d.serial)
            
            # 判断是否需要初始化
            # 如果是SS4设备且不是localhost:5559，说明需要初始化
            needs_init = (ss_type == "SS4") and (d.serial != "localhost:5559")
            
            device_info = {
                "serial": d.serial,
                "model": model,
                "ss_type": ss_type,  # Will be "SS4", "SS3", etc. or None
                "needs_init": needs_init  # SS4设备且未初始化时为True
            }
            devices.append(device_info)
        return devices
    except Exception as e:
        print(f"Error listing devices: {e}")
        return []

class ConnectRequest(BaseModel):
    serial: Optional[str] = None
    init_ss4: bool = False

class SS4InitRequest(BaseModel):
    serial: str

@app.post("/api/init-ss4")
def init_ss4_device(req: SS4InitRequest):
    """Initialize SS4 device with required ADB commands"""
    global ss4_localhost_mapping
    try:
        serial = req.serial
        print(f"Initializing SS4 device: {serial}")
        
        # Step 1: adb root
        result = subprocess.run(["adb", "-s", serial, "root"], 
                              capture_output=True, text=True, timeout=10)
        print(f"adb root: {result.stdout}")
        if result.returncode != 0:
            print(f"Warning: adb root failed: {result.stderr}")
        
        # Wait a bit for root to take effect
        import time
        time.sleep(1)
        
        # Step 2: adb shell adbconnect.sh
        result = subprocess.run(["adb", "-s", serial, "shell", "adbconnect.sh"], 
                              capture_output=True, text=True, timeout=10)
        print(f"adbconnect.sh: {result.stdout}")
        if result.returncode != 0:
            print(f"Warning: adbconnect.sh failed: {result.stderr}")
        
        time.sleep(1)
        
        # Step 3: adb forward tcp:5559 tcp:5557
        result = subprocess.run(["adb", "-s", serial, "forward", "tcp:5559", "tcp:5557"], 
                              capture_output=True, text=True, timeout=10)
        print(f"adb forward: {result.stdout}")
        if result.returncode != 0:
            raise Exception(f"adb forward failed: {result.stderr}")
        
        time.sleep(1)
        
        # Step 4: adb connect localhost:5559
        result = subprocess.run(["adb", "connect", "localhost:5559"], 
                              capture_output=True, text=True, timeout=10)
        print(f"adb connect: {result.stdout}")
        if result.returncode != 0:
            print(f"Warning: adb connect failed: {result.stderr}")
        
        time.sleep(2)
        
        # Step 5: adb -s localhost:5559 root
        result = subprocess.run(["adb", "-s", "localhost:5559", "root"], 
                              capture_output=True, text=True, timeout=10)
        print(f"adb root (localhost): {result.stdout}")
        if result.returncode != 0:
            print(f"Warning: final root failed: {result.stderr}")
        
        time.sleep(1)
        
        # 记录映射关系：localhost:5559 -> {type: SS4, original_serial: xxx}
        ss4_localhost_mapping["localhost:5559"] = {
            "type": "SS4",
            "original_serial": serial  # 保存原始物理设备序列号
        }
        print(f"[INIT_SS4] ✅ 已记录映射: localhost:5559 -> SS4 (原始序列号: {serial})")
        
        return {
            "status": "success",
            "message": "SS4 device initialized successfully",
            "new_serial": "localhost:5559"
        }
    except Exception as e:
        print(f"SS4 initialization error: {e}")
        raise HTTPException(status_code=500, detail=f"SS4 initialization failed: {str(e)}")

@app.get("/api/displays")
def get_displays(serial: Optional[str] = None):
    global current_serial, display_info_cache, ss4_localhost_mapping
    target_serial = serial or current_serial

    # ⚠️ 关键修复：/api/displays 被前端直接带 serial 调用（如 ?serial=localhost:5559），
    # 但 /api/screenshot 等接口依赖全局 current_serial。
    # 若前端没有单独调用 /api/connect，current_serial 会始终为空，导致截图返回 400。
    if serial and serial != current_serial:
        current_serial = serial
        print(f"[DISPLAYS] 🔗 更新 current_serial = {serial}")
    
    if not target_serial:
        return []
    
    # 检测设备类型：优先从映射表获取（针对localhost:5559这类转换后的SS4设备）
    # 然后尝试直接检测设备类型
    ss_type = None
    if target_serial == "localhost:5559" and target_serial in ss4_localhost_mapping:
        ss_type = ss4_localhost_mapping[target_serial]["type"]  # 从字典中提取type
        print(f"[DISPLAYS] 从映射表识别 {target_serial} 为 {ss_type}")
    else:
        # 直接检测设备类型（适用于未初始化的SS4设备）
        ss_type = detect_ss_device(target_serial)
        print(f"[DISPLAYS] 通过getprop检测设备类型: {ss_type}")
    
    print(f"[DISPLAYS] Device type: {ss_type}")
    print(f"[DISPLAYS] Device serial: {target_serial}")
    print(f"[DISPLAYS] 开始动态探测设备的display配置...")
    
    # === Method 1: dumpsys display 解析（最可靠，含分辨率）===
    res = refresh_display_mapping(target_serial)
    if res:
        print(f"[DISPLAYS] ✅ dumpsys 解析到 {len(res)} 个 display: {[d['id'] for d in res]}")
        return res

    # === Method 2: wm size -d N 逐个探测（快速，有分辨率）===
    print(f"[DISPLAYS] dumpsys 方式失败，尝试 wm size 探测...")
    available_displays = []
    for display_id in range(6):
        try:
            r = subprocess.run(
                ["adb", "-s", target_serial, "shell", f"wm size -d {display_id}"],
                capture_output=True, text=True, timeout=3, check=False,
            )
            out = (r.stdout or "").strip()
            if r.returncode == 0 and re.search(r"\d+x\d+", out):
                m = re.search(r"(\d+)x(\d+)", out)
                res_str = f" {m.group(1)}x{m.group(2)}" if m else ""
                available_displays.append({"id": str(display_id), "description": f"Display {display_id}{res_str}"})
                print(f"[DISPLAYS] ✅ wm size: Display {display_id} = {out.strip()}")
        except Exception as e:
            print(f"[DISPLAYS] ⚠️ wm size Display {display_id}: {e}")
            continue

    if available_displays:
        print(f"[DISPLAYS] wm size 探测到 {len(available_displays)} 个 display")
        return available_displays

    # === Method 3: screencap 兜底（最慢，timeout 5s/屏）===
    print(f"[DISPLAYS] wm size 也失败，fallback 到 screencap 探测...")
    for display_id in range(6):
        try:
            result = subprocess.run(
                ["adb", "-s", target_serial, "shell", f"screencap -d {display_id} -p"],
                capture_output=True, timeout=5, check=False,
            )
            if result.returncode == 0 and len(result.stdout) > 100 and b"\x89PNG" in result.stdout:
                available_displays.append({"id": str(display_id), "description": f"Display {display_id}"})
                print(f"[DISPLAYS] ✅ screencap: Display {display_id} 可用")
        except Exception as e:
            print(f"[DISPLAYS] ⚠️ screencap Display {display_id}: {e}")
            continue

    if available_displays:
        print(f"[DISPLAYS] screencap 探测到 {len(available_displays)} 个 display")
        return available_displays

    print(f"[DISPLAYS] 所有方式都失败，返回最小配置 (Display 0)")
    return [{"id": "0", "description": "Display 0"}]

@app.post("/api/connect")
def connect_device(req: ConnectRequest):
    global current_serial
    try:
        if req.serial:
            current_serial = req.serial
            print(f"[CONNECT] 设置 current_serial 为: {current_serial}")
        else:
            devices = adb.device_list()
            if not devices:
                raise HTTPException(status_code=404, detail="No devices found")
            current_serial = devices[0].serial
            print(f"[CONNECT] 自动选择第一个设备: {current_serial}")
        
        d = adb.device(serial=current_serial)
        model = d.prop.get("ro.product.model", "Unknown")
        
        print(f"[CONNECT] ✅ 连接成功: {current_serial}, Model: {model}")
        
        # ── 自动 adb root（车机/已root设备需要 root 才能写 secure settings）──
        # best-effort：失败/不支持时只打印日志，不影响连接结果
        try:
            import time as _t_root
            _root_r = subprocess.run(
                ["adb", "-s", current_serial, "root"],
                capture_output=True, text=True, timeout=10
            )
            _root_out = (_root_r.stdout or "").strip()
            print(f"[CONNECT] adb root: rc={_root_r.returncode} -> {_root_out[:120]}")
            # 若 adbd 正在重启（返回 "restarting adbd as root"），等 2s 让其重连
            if _root_r.returncode == 0 and "restarting" in _root_out.lower():
                print("[CONNECT] adbd 正在重启，等待 2s...")
                _t_root.sleep(2)
        except Exception as _re:
            print(f"[CONNECT] adb root 尝试失败（忽略）: {_re}")
        
        return {
            "status": "connected", 
            "serial": current_serial,
            "info": {
                "productName": model,
                "model": model,
                "sdk": d.prop.get("ro.build.version.sdk", "Unknown")
            }
        }
    except Exception as e:
        print(f"[CONNECT] ❌ 连接失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

def _is_png_complete(data: bytes) -> bool:
    """检查 PNG 数据是否完整有效，验证截图完整性。
    大尺寸截图（如 SS4 Display 4: 3840x2160）通过 adb shell 协议传输时可能被截断，
    导致前端 canvas 下半部分显示为黑色（background-color: #000）。

    ⚠️ 旧实现只检查 b'IEND' in data[-50:]，太宽松：
       PNG IEND chunk 是严格的 12 字节 \x00\x00\x00\x00IEND\xae\x42\x60\x82，
       仅搜索 4 字节 "IEND" 可能被 IDAT 数据中的偶然字节序列误匹配，
       导致截断的 PNG 通过检查，前端只渲染前 N 行，其余显示为黑色（"黑屏一半"）。
    修复：使用 endswith() 检查完整 12 字节 IEND chunk，并验证 PNG 签名和数据量下限。
    """
    if not data or len(data) < 67:   # 最小有效 PNG（1×1 白色）约 67 字节
        return False
    # 完整的 IEND chunk：4字节长度=0 + 4字节类型"IEND" + 4字节CRC
    IEND_CHUNK = b'\x00\x00\x00\x00IEND\xae\x42\x60\x82'
    if not data.endswith(IEND_CHUNK):
        return False
    # 验证 PNG 签名存在（可能有前缀垃圾字节，用 find 而非 startswith）
    PNG_SIG = b'\x89PNG\r\n\x1a\n'
    if data.find(PNG_SIG) < 0:
        return False
    # 额外：根据 IHDR 宽高估算 PNG 最低数据量，防止"只有签名+空IDAT+IEND"的最小伪PNG通过
    # 每行至少 1 字节（过滤字节）+ 压缩数据，极端情况下 PNG 大小 >= H + 57（固定开销）
    try:
        import struct as _s
        idx = data.find(PNG_SIG)
        png_data = data[idx:]
        if len(png_data) >= 24:
            h = _s.unpack('>I', png_data[20:24])[0]
            if h > 0 and len(png_data) < h + 57:
                return False  # 数据量连每行 1 字节都不够，肯定截断了
    except Exception:
        pass
    return True


def _get_png_size(data: bytes):
    """从 PNG 数据头部解析图片尺寸，返回 (width, height) 或 (None, None)。
    用于验证截图是否对应正确的 Display（防止 screencap -d N 实际返回了其他 display 的截图）。
    PNG 格式：8字节签名 + IHDR chunk（4字节长度 + 4字节类型 + 4字节宽度 + 4字节高度 + ...）
    Width 在签名后第 16-19 字节，Height 在第 20-23 字节（big-endian uint32）。"""
    try:
        import struct
        if not data or len(data) < 24:
            return None, None
        # 找到 PNG 签名（可能前面有垃圾字节）
        png_sig = b'\x89PNG\r\n\x1a\n'
        idx = data.find(png_sig)
        if idx < 0:
            return None, None
        # IHDR 数据区起始 = 签名8字节 + chunk长度4字节 + chunk类型4字节 = +16
        start = idx + 16
        if len(data) < start + 8:
            return None, None
        w = struct.unpack('>I', data[start:start + 4])[0]
        h = struct.unpack('>I', data[start + 4:start + 8])[0]
        return w, h
    except Exception:
        return None, None


@app.get("/api/screenshot")
def get_screenshot(display: str = "0"):
    global current_serial, display_mapping
    if not current_serial:
         raise HTTPException(status_code=400, detail="Device not connected")
    
    try:
        print(f"[SCREENSHOT] 📸 请求截图 - Display ID: {display}, Device: {current_serial}")
        
        # Detect device type for special handling
        ss_type = detect_ss_device(current_serial)  # cached after first call
        print(f"[SCREENSHOT] 🚗 设备类型: {ss_type} (cached={current_serial in _ss_type_cache})")
        
        # Use physical ID for screencap if available
        phys_id = display_mapping.get(display, display)
        print(f"[SCREENSHOT] 🔄 物理ID映射: {display} -> {phys_id}")
        
        raw_png = None
        best_png = None   # 完整但尺寸不符的截图，所有正确尺寸尝试失败后用作兜底
        last_err = ""
        d = adb.device(serial=current_serial)

        # 从 display_info_cache 获取期望的截图尺寸，用于验证截图是否对应正确的 display
        # SS4 场景：screencap -d 4 有时实际返回 Display 0 的截图（3840x1740 而非 3840x2160），
        # 导致前端下方显示黑色。通过检查 PNG 尺寸可以检测并重试。
        _exp_w, _exp_h = None, None
        for _di in display_info_cache:
            if _di.get('id') == str(display):
                _res_m = re.search(r'(\d+)x(\d+)', _di.get('description', ''))
                if _res_m:
                    _exp_w, _exp_h = int(_res_m.group(1)), int(_res_m.group(2))
                    break
        if _exp_w and _exp_h:
            print(f"[SCREENSHOT] 📐 期望截图尺寸: {_exp_w}x{_exp_h}")

        # 优化的命令尝试顺序 - SS2MAX前后排设备都能正确截图
        cmd_variations = []
        
        # SS2/SS2MAX设备专用优化策略
        if ss_type == "SS2":
            print(f"[SCREENSHOT] 🚙 SS2/SS2MAX设备，使用优化截图策略")
            
            # Display 0 - 主屏优先使用最简命令
            if display == "0":
                print(f"[SCREENSHOT] 🎯 Display 0（主屏），使用简化命令")
                cmd_variations.append("screencap -p")
                cmd_variations.append(f"screencap -d {display} -p")
                cmd_variations.append(f"screencap -p -d {display}")
            else:
                # Display 1, 2 等副屏 - 优先使用logical display ID
                print(f"[SCREENSHOT] 🎯 Display {display}（副屏），优先logical ID")
                cmd_variations.append(f"screencap -d {display} -p")
                cmd_variations.append(f"screencap -p -d {display}")
                
                # 尝试physical ID（如果有映射）
                if phys_id != display:
                    print(f"[SCREENSHOT] 🔄 尝试物理ID: {phys_id}")
                    cmd_variations.append(f"screencap -d {phys_id} -p")
                    cmd_variations.append(f"screencap -p -d {phys_id}")
        else:
            # 其他设备（非SS2）的通用策略
            if display == "0":
                cmd_variations.append("screencap -p")
            
            # 优先logical ID
            cmd_variations.append(f"screencap -d {display} -p")
            cmd_variations.append(f"screencap -p -d {display}")
            
            # 然后physical ID
            if phys_id != display:
                cmd_variations.append(f"screencap -d {phys_id} -p")
                cmd_variations.append(f"screencap -p -d {phys_id}")

        # 使用adbutils执行命令
        # ⚠️ 注意：必须用 encoding=None 获取原始 bytes，否则 adbutils 2.x 默认 UTF-8 解码会损坏 PNG 高字节
        for cmd_str in cmd_variations:
            try:
                print(f"[SCREENSHOT] 🔧 尝试命令: {cmd_str}")
                res = d.shell(cmd_str, encoding=None)  # encoding=None → 返回 bytes，避免 UTF-8 损坏二进制
                if isinstance(res, str):
                    res = res.encode('latin1')  # 兼容旧版 adbutils 返回 str 的情况
                if res and len(res) > 100:
                    if _is_png_complete(res):
                        # 验证截图尺寸是否符合期望（防止 screencap -d N 实际返回了其他 display 的截图）
                        if _exp_w and _exp_h:
                            _aw, _ah = _get_png_size(res)
                            if _aw == _exp_w and _ah == _exp_h:
                                print(f"[SCREENSHOT] ✅ 成功！截图大小: {len(res)} bytes, 尺寸: {_aw}x{_ah}")
                                raw_png = res
                                break
                            elif _aw and _ah:
                                print(f"[SCREENSHOT] ⚠️ 截图尺寸不符: 期望 {_exp_w}x{_exp_h}, 实际 {_aw}x{_ah}, 可能抓到了错误 display，继续尝试...")
                                if best_png is None:
                                    best_png = res  # 记录第一个完整但尺寸不对的截图
                                last_err = f"size mismatch: got {_aw}x{_ah}, expected {_exp_w}x{_exp_h}"
                            else:
                                print(f"[SCREENSHOT] ✅ 成功（无法解析尺寸）！截图大小: {len(res)} bytes")
                                raw_png = res
                                break
                        else:
                            print(f"[SCREENSHOT] ✅ 成功！截图大小: {len(res)} bytes")
                            raw_png = res
                            break
                    else:
                        print(f"[SCREENSHOT] ⚠️ adbutils: PNG 数据不完整（缺少 IEND 尾块），继续尝试下一命令...")
                        last_err = "PNG data incomplete (missing IEND chunk)"
                else:
                    err_text = res.decode('utf-8', errors='replace').strip() if res else ''
                    print(f"[SCREENSHOT] ❌ 返回数据太小或为空: {len(res) if res else 0} bytes, 内容: {err_text[:100]}")
                    last_err = err_text
            except Exception as e:
                print(f"[SCREENSHOT] ❌ 命令失败: {e}")
                last_err = str(e)
                continue

        # Subprocess fallback（兜底方案）
        # 优先使用 exec-out，它专为二进制输出设计，绕过 shell 终端协议，避免 PNG 数据被截断/乱码
        if not raw_png:
            print(f"[SCREENSHOT] 🔄 使用subprocess fallback")
            subprocess_variations = []
            
            # ── exec-out 变体（最高优先级，直接二进制流，不走 shell）──────────────────
            # 对非 0 display 尤其重要：shell 协议可能截断二进制数据
            if display != "0":
                subprocess_variations.append(["adb", "-s", current_serial, "exec-out", "screencap", "-d", display, "-p"])
                if phys_id != display:
                    subprocess_variations.append(["adb", "-s", current_serial, "exec-out", "screencap", "-d", phys_id, "-p"])
                    # local: 前缀变体：SS4 screencap 对长物理 ID 可能需要 local: 前缀
                    if len(str(phys_id)) > 6:
                        subprocess_variations.append(["adb", "-s", current_serial, "exec-out", "screencap", "-d", f"local:{phys_id}", "-p"])
            else:
                subprocess_variations.append(["adb", "-s", current_serial, "exec-out", "screencap", "-p"])
            
            # ── 传统 shell 变体（兜底）──────────────────────────────────────────────
            if ss_type == "SS2":
                if display == "0":
                    subprocess_variations.append(["adb", "-s", current_serial, "shell", "screencap", "-p"])
                subprocess_variations.append(["adb", "-s", current_serial, "shell", "screencap", "-d", display, "-p"])
                subprocess_variations.append(["adb", "-s", current_serial, "shell", "screencap", "-p", "-d", display])
                if phys_id != display:
                    subprocess_variations.append(["adb", "-s", current_serial, "shell", "screencap", "-d", phys_id, "-p"])
            else:
                if display == "0":
                    subprocess_variations.append(["adb", "-s", current_serial, "shell", "screencap", "-p"])
                subprocess_variations.append(["adb", "-s", current_serial, "shell", "screencap", "-d", display, "-p"])
                subprocess_variations.append(["adb", "-s", current_serial, "shell", "screencap", "-p", "-d", display])
                if phys_id != display:
                    subprocess_variations.append(["adb", "-s", current_serial, "shell", "screencap", "-d", phys_id, "-p"])
                    subprocess_variations.append(["adb", "-s", current_serial, "shell", "screencap", "-p", "-d", phys_id])
            
            for cmd in subprocess_variations:
                print(f"[SCREENSHOT] 🔧 subprocess尝试: {' '.join(cmd)}")
                result = subprocess.run(cmd, capture_output=True, check=False, timeout=15)
                if result.stdout and len(result.stdout) > 100:
                    if _is_png_complete(result.stdout):
                        if _exp_w and _exp_h:
                            _aw, _ah = _get_png_size(result.stdout)
                            if _aw == _exp_w and _ah == _exp_h:
                                print(f"[SCREENSHOT] ✅ subprocess成功！大小: {len(result.stdout)} bytes, 尺寸: {_aw}x{_ah}")
                                raw_png = result.stdout
                                break
                            elif _aw and _ah:
                                print(f"[SCREENSHOT] ⚠️ subprocess截图尺寸不符: 期望 {_exp_w}x{_exp_h}, 实际 {_aw}x{_ah}，继续尝试...")
                                if best_png is None:
                                    best_png = result.stdout
                                last_err = f"size mismatch: got {_aw}x{_ah}, expected {_exp_w}x{_exp_h}"
                            else:
                                print(f"[SCREENSHOT] ✅ subprocess成功（无法解析尺寸）！大小: {len(result.stdout)} bytes")
                                raw_png = result.stdout
                                break
                        else:
                            print(f"[SCREENSHOT] ✅ subprocess成功！大小: {len(result.stdout)} bytes (returncode={result.returncode})")
                            raw_png = result.stdout
                            break
                    else:
                        print(f"[SCREENSHOT] ⚠️ subprocess: PNG 数据不完整（缺少 IEND 尾块），继续尝试下一命令...")
                        last_err = "PNG data incomplete (missing IEND chunk)"
                err_out = result.stderr.decode(errors='ignore').strip() if result.stderr else ''
                if err_out:
                    last_err = err_out
                    print(f"[SCREENSHOT] ❌ subprocess错误 (rc={result.returncode}): {last_err[:200]}")

        # ── wm screenshot 兜底（Android 13+ 走不同截图路径，某些场景能捕获 screencap 漏掉的图层）──
        if not raw_png:
            try:
                import time as _t
                _wm_tmp = "/sdcard/.ui_insp_sc_tmp.png"
                _wm_r = subprocess.run(
                    ["adb", "-s", current_serial, "shell", f"wm screenshot {_wm_tmp}"],
                    capture_output=True, check=False, timeout=8
                )
                _wm_stdout = (_wm_r.stdout or b"").decode("utf-8", errors="replace").strip()
                # 成功标志：returncode 0，或输出为空（wm screenshot 成功时通常无输出）
                if _wm_r.returncode == 0 or (not _wm_stdout) or "Written" in _wm_stdout:
                    _pull_r = subprocess.run(
                        ["adb", "-s", current_serial, "exec-out", f"cat {_wm_tmp}"],
                        capture_output=True, check=False, timeout=10
                    )
                    # 后台删除临时文件（不等待）
                    subprocess.Popen(
                        ["adb", "-s", current_serial, "shell", f"rm -f {_wm_tmp}"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                    )
                    if _pull_r.stdout and _is_png_complete(_pull_r.stdout):
                        print(f"[SCREENSHOT] ✅ wm screenshot 兜底成功！大小: {len(_pull_r.stdout)} bytes")
                        raw_png = _pull_r.stdout
                    else:
                        print(f"[SCREENSHOT] ⚠️ wm screenshot 数据不完整，跳过")
                else:
                    print(f"[SCREENSHOT] wm screenshot 不支持或失败 (rc={_wm_r.returncode}): {_wm_stdout[:80]}")
            except Exception as _wm_e:
                print(f"[SCREENSHOT] wm screenshot 尝试异常: {_wm_e}")

        # 所有命令均未返回期望尺寸的截图：用兜底截图（完整但尺寸不符）代替报错
        if not raw_png and best_png:
            _bw, _bh = _get_png_size(best_png)
            print(f"[SCREENSHOT] ⚠️ 所有命令均未返回期望尺寸 {_exp_w}x{_exp_h}，使用兜底截图: {_bw}x{_bh}")
            raw_png = best_png

        if not raw_png:
             raise Exception(f"Failed to get screenshot for display {display} (Physical: {phys_id}). Last error: {last_err}")
        
        # 验证PNG格式
        png_header = b"\x89PNG"
        start_idx = raw_png.find(png_header)
        if start_idx != -1:
            raw_png = raw_png[start_idx:]
            print(f"[SCREENSHOT] 🎨 PNG数据有效，起始位置: {start_idx}, 最终大小: {len(raw_png)} bytes")
        else:
            raise Exception("Invalid screenshot format: No PNG header found")

        return StreamingResponse(io.BytesIO(raw_png), media_type="image/png")
    except Exception as e:
        print(f"[SCREENSHOT] ❌ 截图失败 display {display}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

def check_accessibility_service(serial: str) -> bool:
    """检查辅助服务是否可用"""
    try:
        # 设置端口转发
        fw = subprocess.run(
            ["adb", "-s", serial, "forward", "tcp:8765", "tcp:8765"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        if fw.returncode != 0:
            # ⚠️ forward 失败（端口可能被另一台设备占用）→ 必须 return False！
            # 不能继续做 HTTP 探测——否则会打到其他设备的 8765 服务，造成"误判已运行"。
            print(f"[Accessibility] ⚠️ adb forward failed on {serial}: {fw.stderr.strip()} (返回 False，避免误用其他设备的服务)")
            return False

        if requests is None:
            print("[Accessibility] ⚠️ Python requests not installed, cannot probe /api/status")
            return False

        # 测试连接
        response = requests.get("http://localhost:8765/api/status", timeout=2)
        if response.status_code == 200:
            data = response.json()
            if data.get("service") == "running":
                print(f"[Accessibility] ✅ 辅助服务可用 (serial={serial})")
                return True
    except Exception as e:
        print(f"[Accessibility] ⚠️ 辅助服务不可用(serial={serial}): {e}")
    return False


def probe_accessibility_service(serial: str) -> Dict:
    """Probe accessibility service and return details for diagnosis.

    Returns:
      {
        ok: bool,
        forward_ok: bool,
        forward_stderr: str,
        http_ok: bool,
        http_error: str,
        status_json: dict|None,
      }
    """
    info: Dict = {
        "ok": False,
        "forward_ok": False,
        "forward_stderr": "",
        "http_ok": False,
        "http_error": "",
        "status_json": None,
    }

    try:
        fw = subprocess.run(
            ["adb", "-s", serial, "forward", "tcp:8765", "tcp:8765"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        info["forward_ok"] = fw.returncode == 0
        info["forward_stderr"] = (fw.stderr or "").strip()

        if not info["forward_ok"]:
            # ⚠️ forward 失败（端口被其他设备占用）→ 直接返回 False，避免 HTTP 打到其他设备
            info["http_error"] = f"adb forward failed: {info['forward_stderr']}"
            return info

        if requests is None:
            info["http_error"] = "python requests not installed"
            return info

        try:
            resp = requests.get("http://localhost:8765/api/status", timeout=2)
            if resp.status_code == 200:
                info["http_ok"] = True
                try:
                    info["status_json"] = resp.json()
                except Exception:
                    info["status_json"] = None
            else:
                info["http_error"] = f"HTTP {resp.status_code}"
        except Exception as e:
            info["http_error"] = str(e)

        info["ok"] = bool(info["http_ok"] and (info.get("status_json") or {}).get("service") == "running")
        return info
    except Exception as e:
        info["http_error"] = str(e)
        return info


def probe_accessibility_service_any(serial: str) -> Dict:
    """Probe possible serials and return the first successful result.

    Returns a dict with extra keys:
      - candidates: [..]
      - ok_serial: str
      - by_serial: {serial: probe_dict}
    """
    candidates = get_accessibility_candidate_serials(serial)
    by_serial: Dict[str, Dict] = {}
    ok_serial = ""
    ok = False
    for s in candidates:
        r = probe_accessibility_service(s)
        by_serial[s] = r
        if not ok and r.get("ok"):
            ok = True
            ok_serial = s
    return {
        "ok": ok,
        "ok_serial": ok_serial,
        "candidates": candidates,
        "by_serial": by_serial,
    }


def _adb_run(args: List[str], timeout: int = 10) -> subprocess.CompletedProcess:
    """Run adb subprocess (best-effort, capture output)."""
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)


def ensure_accessibility_service(
    serial: str,
    apk_path: Optional[str] = None,
    install_if_missing: bool = True,
    *,
    enable_service: bool = True,
    probe_running: bool = True,
) -> Dict:
    """Ensure CarUI accessibility APK is installed, and optionally enabled/probed.

    Default行为保持原样（安装/启用/校验），以兼容现有“一键启动辅助服务”。

    新增用法：连接设备时仅安装 APK（不改 secure settings，不做 8765 probe）：
      ensure_accessibility_service(serial, enable_service=False, probe_running=False)
    """
    result: Dict = {
        "serial": serial,
        "target_serial": resolve_accessibility_target_serial(serial),
        "install_serial": "",
        "apk_installed": None,
        "apk_install_attempted": False,
        "enabled": False,
        "running": False,
        "steps": [],
        "error": None,
    }

    # 对 SS4 场景：可能同时存在 original_serial / localhost:5559。
    # 为了让“仅安装 APK”也尽量成功，这里对候选 serial 做 best-effort 选择。
    candidates = get_accessibility_candidate_serials(serial)
    target_serial = result["target_serial"]
    install_serial = ""

    def step(msg: str):
        print(f"[Accessibility][Ensure] {msg}")
        result["steps"].append(msg)

    try:
        # 0) Validate adb device
        step(f"Using target_serial={target_serial}, candidates={candidates}")

        # 1) Resolve APK path (bundled first)
        if not apk_path:
            bundled = os.path.join(script_dir, "CarUIAccessibilityService-debug.apk")
            dev_path = os.path.abspath(
                os.path.join(
                    script_dir,
                    "..",
                    "accessibility_service",
                    "build",
                    "outputs",
                    "apk",
                    "debug",
                    "CarUIAccessibilityService-debug.apk",
                )
            )
            if os.path.exists(bundled):
                apk_path = bundled
            elif os.path.exists(dev_path):
                apk_path = dev_path

        # 2) Check/install APK (best-effort: try candidates in order)
        pkg = "com.carui.accessibility"
        installed = False
        last_pm_err = ""
        for s in candidates:
            r = _adb_run(["adb", "-s", s, "shell", "pm", "path", pkg], timeout=8)
            if r.returncode == 0 and ("package:" in (r.stdout or "")):
                installed = True
                install_serial = s
                step(f"APK already installed on {s}")
                break
            last_pm_err = (r.stderr or "").strip() or (r.stdout or "").strip()

        result["apk_installed"] = installed
        result["install_serial"] = install_serial
        step(f"APK installed? {installed}")

        if (not installed) and install_if_missing:
            if apk_path and os.path.exists(apk_path):
                result["apk_install_attempted"] = True
                step(f"Installing APK: {apk_path}")

                # prefer target_serial first, then other candidates
                install_try_serials = [target_serial] + [s for s in candidates if s != target_serial]
                install_ok = False
                last_install_err = ""
                for s in install_try_serials:
                    ir = _adb_run(["adb", "-s", s, "install", "--no-streaming", "-r", "-t", "-d", apk_path], timeout=90)
                    if ir.returncode == 0:
                        install_ok = True
                        install_serial = s
                        break
                    last_install_err = (ir.stderr or "").strip() or (ir.stdout or "").strip()

                if not install_ok:
                    step(f"APK install failed: {last_install_err}")
                else:
                    step(f"APK install success on {install_serial}")
                    result["apk_installed"] = True
                    result["install_serial"] = install_serial
            else:
                step("APK missing and no apk_path provided")
                if last_pm_err:
                    step(f"pm path last error: {last_pm_err}")

        # 2.5) adb root —— SS2/SS3 等车机必须 root 才能写 secure settings
        # 普通已-root 设备会返回 "adbd is already running as root"，此步骤无害
        step("Trying adb root (required for car ECUs to write secure settings)")
        try:
            import time as _time_root
            root_r = _adb_run(["adb", "-s", target_serial, "root"], timeout=8)
            root_out = (root_r.stdout or "").strip()
            step(f"adb root: rc={root_r.returncode} -> {root_out[:120]}")
            if root_r.returncode == 0 and "already" not in root_out.lower():
                # daemon 重启 —— 需要等待 adbd 重新上线
                # SS2/车机 adbd 重启可能需要 20~30s，固定 sleep(2) 不够
                # 改为主动轮询 adb get-state，最多等待 30 秒
                step("adbd restarting, polling for reconnect (up to 30s)...")
                _time_root.sleep(2)  # 给 adbd 一点时间先断开
                _reconnected = False
                for _wait_i in range(28):
                    try:
                        _dev_r = _adb_run(["adb", "-s", target_serial, "get-state"], timeout=3)
                        if _dev_r.returncode == 0 and "device" in (_dev_r.stdout or "").lower():
                            step(f"adbd reconnected after ~{_wait_i + 2}s")
                            _reconnected = True
                            break
                    except Exception:
                        pass
                    _time_root.sleep(1)
                if not _reconnected:
                    step("adbd reconnect polling timed out (30s), continuing anyway")
            else:
                _time_root.sleep(0.5)
        except Exception as _root_e:
            step(f"adb root exception: {_root_e} (continuing anyway)")

        # 3) Enable secure settings (optional)
        if enable_service:
            r = _adb_run(["adb", "-s", target_serial, "shell", "settings", "get", "secure", "enabled_accessibility_services"], timeout=6)
            current_services = (r.stdout or "").strip()
            component = "com.carui.accessibility/.CarUIAccessibilityService"
            if "com.carui.accessibility" in current_services:
                step("Service already in enabled_accessibility_services")
            else:
                new_services = (
                    f"{current_services}:{component}" if current_services and current_services != "null" else component
                )
                step(f"Enabling service via secure settings: {component}")
                _adb_run(["adb", "-s", target_serial, "shell", "settings", "put", "secure", "enabled_accessibility_services", new_services], timeout=6)

            _adb_run(["adb", "-s", target_serial, "shell", "settings", "put", "secure", "accessibility_enabled", "1"], timeout=6)

            enabled_services_now = _adb_run(["adb", "-s", target_serial, "shell", "settings", "get", "secure", "enabled_accessibility_services"], timeout=6).stdout.strip()
            result["enabled"] = "com.carui.accessibility" in (enabled_services_now or "")
            step(f"Enabled now? {result['enabled']}")
            if not result["enabled"]:
                step("WARNING: enable may require root/WRITE_SECURE_SETTINGS; please enable manually in Settings")

            # 3.1) Force-rebind: toggle accessibility_enabled 0→1
            # 仅写入 enabled_accessibility_services 在某些车机上不会立即触发服务绑定，
            # 需要切换 accessibility_enabled 来强制 AccessibilityManagerService 重新扫描。
            step("Force-rebind: toggling accessibility_enabled 0→1 to nudge framework")
            import time as _time_bind
            _adb_run(["adb", "-s", target_serial, "shell", "settings", "put", "secure", "accessibility_enabled", "0"], timeout=6)
            _time_bind.sleep(1)
            _adb_run(["adb", "-s", target_serial, "shell", "settings", "put", "secure", "accessibility_enabled", "1"], timeout=6)

            # 3.2) cmd accessibility enable（Android 10+ / 部分车机支持，最强触发方式）
            try:
                _cmd_r = _adb_run(
                    ["adb", "-s", target_serial, "shell", "cmd", "accessibility", "enable",
                     "com.carui.accessibility/.CarUIAccessibilityService"],
                    timeout=8,
                )
                _cmd_out = ((_cmd_r.stdout or "") + (_cmd_r.stderr or "")).strip()
                step(f"cmd accessibility enable: rc={_cmd_r.returncode} -> {_cmd_out[:120]}")
            except Exception as _cmd_e:
                step(f"cmd accessibility enable: skipped ({_cmd_e})")

        else:
            step("Skip enabling service (enable_service=false)")

        # 4) Probe running (optional)
        if probe_running:
            _adb_run(["adb", "-s", target_serial, "forward", "tcp:8765", "tcp:8765"], timeout=6)

            import time
            running = False
            for _ in range(1, 33):  # 32×0.5s = 16s
                if check_accessibility_service(target_serial):
                    running = True
                    break
                time.sleep(0.5)
            result["running"] = running
            step(f"Running? {running}")
            if not result["running"]:
                step("WARNING: service not responding on 8765; please open Accessibility settings and toggle service")
                # 诊断：进程是否存在 + 端口是否监听
                try:
                    _ps_out = (_adb_run(["adb", "-s", target_serial, "shell", "ps", "-A"], timeout=6).stdout or "")
                    _has_proc = "carui" in _ps_out.lower()
                    step(f"DIAG: carui process in ps? {_has_proc}")
                    # 0x223D = 8765
                    _net_out = (_adb_run(["adb", "-s", target_serial, "shell", "cat /proc/net/tcp6"], timeout=5).stdout or "")
                    _port_up = "223D" in _net_out.upper()
                    step(f"DIAG: port 8765 listening? {_port_up}")
                    if _port_up and not running:
                        step("DIAG: port up but HTTP not responding → re-forwarding")
                        _fw2 = _adb_run(["adb", "-s", target_serial, "forward", "tcp:8765", "tcp:8765"], timeout=6)
                        step(f"DIAG: re-forward rc={_fw2.returncode}")
                        # 额外再探一次
                        if check_accessibility_service(target_serial):
                            result["running"] = True
                            step("DIAG: service OK after re-forward!")
                except Exception as _diag_e:
                    step(f"DIAG error: {_diag_e}")
        else:
            step("Skip probing running status (probe_running=false)")

        return result
    except Exception as e:
        result["error"] = str(e)
        return result

def _get_display_bounds_from_dumpsys(serial: str, display: int):
    """通过 dumpsys display 获取 Display N 的全局逻辑坐标范围。
    返回 (x0, y0, x1, y1) 或 None。
    SS4多SoC场景下用于识别 Display N 在全局坐标中的位置，从而按坐标过滤辅助服务节点。
    """
    try:
        import re
        result = subprocess.run(
            ["adb", "-s", serial, "shell", "dumpsys", "display"],
            capture_output=True, text=True, timeout=5
        )
        output = result.stdout

        # 不同 Android 版本的 dumpsys display 输出格式不同，尝试多种定位方式
        # 先找到 mDisplayId=N 的位置，在其后 3000 字符内查找坐标矩形
        disp_idx = output.find(f"mDisplayId={display}")
        if disp_idx < 0:
            disp_idx = output.find(f"Display id {display}")
        if disp_idx < 0:
            print(f"[DisplayBounds] ⚠️ dumpsys display 中未找到 Display {display} 片段")
            return None

        # 截取 Display N 段落（最多 3000 字符，避免跨入下一 Display 段落）
        segment = output[disp_idx:disp_idx + 3000]

        # 尝试各种 Rect 格式匹配
        patterns = [
            # "logical Rect(x0, y0 - x1, y1)" 或 "logical frame Rect(...)"
            re.compile(r'logical\s+(?:frame\s+)?Rect\((\d+),\s*(\d+)\s*-\s*(\d+),\s*(\d+)\)'),
            # "displayRect=Rect(x0, y0 - x1, y1)"
            re.compile(r'displayRect\s*=\s*Rect\((\d+),\s*(\d+)\s*-\s*(\d+),\s*(\d+)\)'),
            # "bounds=Rect(x0, y0 - x1, y1)"
            re.compile(r'bounds\s*=\s*Rect\((\d+),\s*(\d+)\s*-\s*(\d+),\s*(\d+)\)'),
        ]
        for pat in patterns:
            m = pat.search(segment)
            if m:
                x0, y0, x1, y1 = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
                if x1 > x0 and y1 > y0:  # 合法矩形
                    print(f"[DisplayBounds] Display {display} 全局范围: ({x0},{y0})-({x1},{y1})")
                    return (x0, y0, x1, y1)

        print(f"[DisplayBounds] ⚠️ 无法从 dumpsys display 中解析出 Display {display} 的坐标矩形")
        return None
    except Exception as e:
        print(f"[DisplayBounds] ⚠️ dumpsys display 获取失败: {e}")
        return None


def get_hierarchy_from_accessibility(serial: str, display: int = 0) -> Optional[str]:
    """从辅助服务获取UI树并转换为XML格式"""
    try:
        import requests
        import xml.etree.ElementTree as ET
        
        print(f"[Accessibility] 📡 从辅助服务获取UI树...")
        
        # 确保端口转发（某些设备/系统在状态检测后仍可能失效，兜底再 forward 一次）
        subprocess.run(
            ["adb", "-s", serial, "forward", "tcp:8765", "tcp:8765"],
            capture_output=True,
            timeout=3,
            check=False,
        )

        # 请求UI树
        response = requests.get(f"http://localhost:8765/api/hierarchy?display={display}", timeout=5)
        if response.status_code != 200:
            print(f"[Accessibility] ❌ 请求失败: {response.status_code}")
            return None
        
        data = response.json()
        if not data.get("success"):
            print(f"[Accessibility] ❌ 获取失败: {data.get('error')}")
            return None
        
        nodes = data.get("nodes", [])
        display_fallback = data.get("displayFallback", False)
        print(f"[Accessibility] ✅ 获取到 {len(nodes)} 个根节点 (displayFallback={display_fallback})")

        # SS4多SoC兜底处理：APK无法找到 Display N 的专属窗口，返回了全部窗口节点。
        # 直接把这些节点当作 Display N 的数据会把 Display 0 的内容错误地呈现给用户。
        # 优先按 window.displayId 字段过滤，更准确；坐标过滤对于独立 DisplayGroup (origin=0,0) 无效。
        if display_fallback and display != 0:
            print(f"[Accessibility] ⚠️ displayFallback=True: APK在Display {display}无专属窗口，")
            print(f"[Accessibility]    返回了全部display的节点，按 window.displayId={display} 过滤...")
            # 优先按 displayId 精确过滤
            nodes_by_id = [n for n in nodes if (n.get("window") or {}).get("displayId") == display]
            if nodes_by_id:
                print(f"[Accessibility]    ✅ 按displayId过滤后保留 {len(nodes_by_id)} 个Display {display}的节点")
                nodes = nodes_by_id
                origin_x, origin_y = 0, 0
            else:
                # displayId 字段无匹配：尝试坐标过滤（仅对有偏移的 display 有效）
                print(f"[Accessibility]    ⚠️ 无 displayId={display} 的节点，尝试坐标范围过滤...")
                display_bounds = _get_display_bounds_from_dumpsys(serial, display)
                if display_bounds:
                    dx0, dy0, dx1, dy1 = display_bounds
                    # 仅当 display 有非零偏移时才做坐标过滤，否则无法区分 display 0 和独立 DisplayGroup
                    if dx0 > 50 or dy0 > 50:
                        print(f"[Accessibility]    Display {display} 全局范围: ({dx0},{dy0})-({dx1},{dy1})，按坐标过滤...")
                        def _wnd_overlaps(node, _dx0=dx0, _dy0=dy0, _dx1=dx1, _dy1=dy1):
                            wb = (node.get("window") or {}).get("bounds") or {}
                            if not wb:
                                return False
                            wl, wt = int(wb.get("left", 0)), int(wb.get("top", 0))
                            wr, wb2 = int(wb.get("right", 0)), int(wb.get("bottom", 0))
                            return not (wr <= _dx0 or wl >= _dx1 or wb2 <= _dy0 or wt >= _dy1)
                        nodes = [n for n in nodes if _wnd_overlaps(n)]
                        if nodes:
                            print(f"[Accessibility]    ✅ 坐标过滤后保留 {len(nodes)} 个节点")
                            origin_x, origin_y = dx0, dy0
                        else:
                            print(f"[Accessibility]    ⚠️ 坐标过滤后无节点，主SoC无法访问Display {display}，返回None")
                            return None
                    else:
                        print(f"[Accessibility]    ⚠️ Display {display} 坐标原点为(0,0)，无法按坐标区分，"
                              f"主SoC辅助服务无法访问Display {display}，返回None（将fallback到UIAutomator）")
                        return None
                else:
                    print(f"[Accessibility]    ⚠️ 无法获取Display {display}的坐标范围，"
                          f"主SoC辅助服务无法访问Display {display}，返回None（将fallback到UIAutomator）")
                    return None
        else:
            # --- 坐标归一化：将"全局坐标"转换为"当前 display 截图坐标系" ---
            # 在多屏/分屏场景下，AccessibilityNodeInfo#getBoundsInScreen 可能返回带 display 偏移的坐标，
            # 而 screencap -d <display> 的截图坐标原点是 (0,0)。
            # 这里用该 display 的窗口 bounds 的最小 left/top 作为 display 的原点偏移，并对疑似"绝对坐标"的节点做减偏移。
            origin_x = 0
            origin_y = 0
            try:
                xs = []
                ys = []
                for rn in nodes:
                    wb = ((rn.get("window") or {}).get("bounds") or {})
                    if "left" in wb and "top" in wb:
                        xs.append(int(wb.get("left", 0)))
                        ys.append(int(wb.get("top", 0)))
                if xs and ys:
                    origin_x = min(xs)
                    origin_y = min(ys)
            except Exception:
                origin_x = 0
                origin_y = 0

        def normalize_bounds(b: Dict) -> Dict:
            """按需将 bounds 从全局坐标转换为 display 内坐标。"""
            if not b:
                return b
            try:
                l = int(b.get("left", 0))
                t = int(b.get("top", 0))
                r = int(b.get("right", 0))
                bt = int(b.get("bottom", 0))

                # 如果 origin 很接近 0，说明已是 display 坐标系
                if origin_x < 50 and origin_y < 50:
                    return {"left": l, "top": t, "right": r, "bottom": bt}

                margin = 200
                # 只有当节点坐标看起来“落在 origin 偏移之后”，才做减偏移
                if l >= origin_x - margin and t >= origin_y - margin and r > origin_x and bt > origin_y:
                    nl = max(0, l - origin_x)
                    nt = max(0, t - origin_y)
                    nr = max(0, r - origin_x)
                    nb = max(0, bt - origin_y)
                    return {"left": nl, "top": nt, "right": nr, "bottom": nb}

                # 否则保持原值（一般是 already-relative）
                return {"left": l, "top": t, "right": r, "bottom": bt}
            except Exception:
                return b

        # 转换为XML格式
        hierarchy = ET.Element('hierarchy')
        hierarchy.set('rotation', '0')
        
        def convert_node_to_xml(json_node, parent_elem):
            """递归转换JSON节点到XML"""
            node = ET.SubElement(parent_elem, 'node')
            
            # 基本属性
            node.set('class', json_node.get('className', ''))
            node.set('package', json_node.get('packageName', ''))
            node.set('text', json_node.get('text', ''))
            node.set('content-desc', json_node.get('contentDescription', ''))
            node.set('resource-id', json_node.get('resourceId', ''))
            
            # 坐标
            bounds = normalize_bounds(json_node.get('bounds', {}))
            bounds_str = f"[{bounds.get('left',0)},{bounds.get('top',0)}][{bounds.get('right',0)},{bounds.get('bottom',0)}]"
            node.set('bounds', bounds_str)
            
            # 状态属性
            node.set('clickable', str(json_node.get('clickable', False)).lower())
            node.set('long-clickable', str(json_node.get('longClickable', False)).lower())
            node.set('enabled', str(json_node.get('enabled', True)).lower())
            # Accessibility 专有：是否对用户可见
            if 'visibleToUser' in json_node:
                node.set('visible-to-user', str(json_node.get('visibleToUser', False)).lower())
            node.set('focusable', str(json_node.get('focusable', False)).lower())
            node.set('focused', str(json_node.get('focused', False)).lower())
            node.set('selected', str(json_node.get('selected', False)).lower())
            node.set('checkable', str(json_node.get('checkable', False)).lower())
            node.set('checked', str(json_node.get('checked', False)).lower())
            node.set('scrollable', str(json_node.get('scrollable', False)).lower())
            
            # 递归处理子节点
            for child in json_node.get('children', []):
                convert_node_to_xml(child, node)
        
        # 转换所有根节点
        for root_node in nodes:
            convert_node_to_xml(root_node, hierarchy)
        
        # 生成XML字符串
        xml_str = ET.tostring(hierarchy, encoding='unicode')
        xml_content = '<?xml version="1.0" encoding="UTF-8"?>' + xml_str
        
        print(f"[Accessibility] ✅ 转换完成，XML长度: {len(xml_content)}")
        return xml_content
        
    except Exception as e:
        print(f"[Accessibility] ❌ 获取UI树失败: {e}")
        import traceback
        traceback.print_exc()
        return None


def inject_virtual_nodes_into_surfaceview(
    primary_xml: str,
    virtual_xml: str,
    virtual_display: int,
    virtual_w: Optional[int] = None,
    virtual_h: Optional[int] = None,
) -> str:
    """将虚拟 display 的节点树注入到主 display XML 的 SurfaceView 节点下，并做坐标映射。

    场景：
      - 主 display（通常 display=0）的 UI 树中有一个 SurfaceView 节点，
        其显示内容来自虚拟 display（如 displayId=10）。
      - AccessibilityService 无法穿透 SurfaceView 获取其内部节点，
        但可以单独查询虚拟 display 的节点树（需 API 33+ 或系统支持）。
      - 本函数将虚拟 display 的顶层节点注入为 SurfaceView 的子节点，
        同时将坐标从"虚拟 display 坐标系"映射到"SurfaceView 在主 display 中的位置"。

    坐标变换公式（线性缩放 + 平移）：
      scale_x = sv_w / virtual_w      (sv_w = SurfaceView 宽度，virtual_w = 虚拟 display 宽度)
      scale_y = sv_h / virtual_h
      new_x1  = sv_x1 + original_x1 * scale_x
      new_y1  = sv_y1 + original_y1 * scale_y

    参数：
      primary_xml      主 display 的 XML 层级字符串（含 <?xml ...?> 头部）
      virtual_xml      虚拟 display 的 XML 层级字符串
      virtual_display  虚拟 display id，仅用于日志
      virtual_w/h      虚拟 display 的逻辑分辨率；若为 None 则自动从缓存或节点推算

    返回：
      融合后的 XML 字符串；若注入失败则返回原始 primary_xml。
    """
    import xml.etree.ElementTree as ET
    import re as _re

    def _parse_b(s):
        if not s:
            return None
        m = _re.match(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', s)
        return {'x1': int(m.group(1)), 'y1': int(m.group(2)),
                'x2': int(m.group(3)), 'y2': int(m.group(4))} if m else None

    def _fmt_b(x1, y1, x2, y2):
        return f"[{x1},{y1}][{x2},{y2}]"

    def _strip_decl(xml_str):
        s = xml_str.strip()
        if s.startswith('<?xml'):
            return s[s.find('?>') + 2:].lstrip()
        return s

    def _transform_recursive(node, sv_b, vw, vh):
        """递归将虚拟 display 坐标映射到 SurfaceView 所在的主 display 坐标空间。"""
        b = _parse_b(node.get('bounds'))
        if b and sv_b and vw and vh:
            sw = sv_b['x2'] - sv_b['x1']
            sh = sv_b['y2'] - sv_b['y1']
            if sw > 0 and sh > 0:
                sx = sw / vw
                sy = sh / vh
                node.set('bounds', _fmt_b(
                    int(sv_b['x1'] + b['x1'] * sx),
                    int(sv_b['y1'] + b['y1'] * sy),
                    int(sv_b['x1'] + b['x2'] * sx),
                    int(sv_b['y1'] + b['y2'] * sy),
                ))
        for child in node:
            if child.tag == 'node':
                _transform_recursive(child, sv_b, vw, vh)

    try:
        primary_root = ET.fromstring(_strip_decl(primary_xml))
        virtual_root = ET.fromstring(_strip_decl(virtual_xml))
        virtual_nodes = list(virtual_root)

        if not virtual_nodes:
            print(f"[MergeVirtual] ⚠️ display {virtual_display} 无子节点，跳过注入")
            return primary_xml

        print(f"[MergeVirtual] display {virtual_display} 共 {len(virtual_nodes)} 个顶层节点")

        # ── 推算虚拟 display 分辨率（用于坐标缩放） ─────────────────────────────
        if not virtual_w or not virtual_h:
            # 优先从 display_info_cache 获取（已由 /api/displays 填充）
            for di in display_info_cache:
                if di.get('id') == str(virtual_display):
                    _rm = re.search(r'(\d+)x(\d+)', di.get('description', ''))
                    if _rm:
                        virtual_w = int(_rm.group(1))
                        virtual_h = int(_rm.group(2))
                        print(f"[MergeVirtual] 从缓存得到 display {virtual_display} 分辨率: {virtual_w}x{virtual_h}")
                    break

        if not virtual_w or not virtual_h:
            # 回退：从虚拟 display 顶层节点 bounds 的右下角推算
            max_r, max_b = 0, 0
            for vn in virtual_nodes:
                vb = _parse_b(vn.get('bounds', ''))
                if vb:
                    max_r = max(max_r, vb['x2'])
                    max_b = max(max_b, vb['y2'])
            if max_r > 0 and max_b > 0:
                virtual_w, virtual_h = max_r, max_b
                print(f"[MergeVirtual] 从节点 bounds 推算 display {virtual_display} 尺寸: {virtual_w}x{virtual_h}")

        if virtual_w and virtual_h:
            print(f"[MergeVirtual] 坐标缩放基准：虚拟 display {virtual_display} = {virtual_w}x{virtual_h}")
        else:
            print(f"[MergeVirtual] ⚠️ 无法获取 display {virtual_display} 分辨率，注入时不做坐标缩放（仅平移）")

        # ── 在主 display 树中找 SurfaceView 并注入 ──────────────────────────────
        injected = [0]

        def _find_and_inject(parent):
            for child in list(parent):
                if child.tag != 'node':
                    continue
                cls = child.get('class', '')
                if 'SurfaceView' in cls:
                    sv_b = _parse_b(child.get('bounds', ''))
                    print(f"[MergeVirtual] 找到 SurfaceView #{injected[0]+1}: "
                          f"class={cls}, bounds={child.get('bounds')}")
                    child.set('virtual-display-injected', str(virtual_display))
                    for vnode in virtual_nodes:
                        vnode_copy = ET.fromstring(ET.tostring(vnode))
                        vnode_copy.set('from-virtual-display', str(virtual_display))
                        if sv_b and virtual_w and virtual_h:
                            _transform_recursive(vnode_copy, sv_b, virtual_w, virtual_h)
                        child.append(vnode_copy)
                    injected[0] += 1
                    print(f"[MergeVirtual] ✅ 已注入 {len(virtual_nodes)} 个节点到 SurfaceView #{injected[0]}")
                    # 继续搜索（若存在多个 SurfaceView 均注入）
                else:
                    _find_and_inject(child)

        _find_and_inject(primary_root)

        if injected[0] == 0:
            print(f"[MergeVirtual] ⚠️ 未在 display0 树中找到 SurfaceView，无法注入 display {virtual_display} 节点")
        else:
            print(f"[MergeVirtual] ✅ 完成：display {virtual_display} 节点已融合到 {injected[0]} 个 SurfaceView 下")

        xml_str = ET.tostring(primary_root, encoding='unicode')
        return '<?xml version="1.0" encoding="UTF-8"?>' + xml_str

    except Exception as e:
        print(f"[MergeVirtual] ❌ 注入失败: {e}")
        import traceback
        traceback.print_exc()
        return primary_xml


def get_virtual_display_xml(serial: str, virtual_display: int, accessibility_serial: str) -> Optional[str]:
    """获取虚拟 display 的节点 XML，依次尝试 AccessibilityService → UIAutomator。

    参数：
      serial               当前设备序列号（用于 UIAutomator adb 命令）
      virtual_display      虚拟 display id（如 10）
      accessibility_serial 辅助服务所在设备的序列号（用于 HTTP 探测）

    返回：
      XML 字符串，或 None（两种方式均失败）
    """
    # 1) 优先通过辅助服务（更可靠，API 33+ 可精确访问虚拟 display 窗口）
    if check_accessibility_service(accessibility_serial):
        xml = get_hierarchy_from_accessibility(accessibility_serial, virtual_display)
        if xml and '<node' in xml:
            print(f"[VirtualDisplay] ✅ 辅助服务成功获取 display {virtual_display} 节点")
            return xml
        print(f"[VirtualDisplay] ⚠️ 辅助服务未返回 display {virtual_display} 节点，尝试 UIAutomator")

    # 2) 回退：UIAutomator dump --display N（Android 11+ 支持虚拟 display）
    try:
        import time
        d = adb.device(serial=serial)
        dump_path = f"/sdcard/uidump_virtual_{virtual_display}.xml"
        d.shell(f"rm -f {dump_path}")
        for attempt in range(2):
            err = d.shell(f"uiautomator dump --compressed --display {virtual_display} {dump_path}")
            print(f"[VirtualDisplay] UIAutomator dump display {virtual_display} (attempt {attempt+1}): {err}")
            xml_content = d.shell(f"cat {dump_path}")
            if xml_content and "<?xml" in xml_content:
                start = xml_content.find("<?xml")
                end = xml_content.rfind(">")
                if start != -1 and end != -1:
                    xml_trimmed = xml_content[start:end+1]
                    if '<node' in xml_trimmed:
                        print(f"[VirtualDisplay] ✅ UIAutomator 成功获取 display {virtual_display}")
                        return xml_trimmed
            time.sleep(0.3)
        print(f"[VirtualDisplay] ⚠️ UIAutomator 也未能获取 display {virtual_display} 节点")
    except Exception as e:
        print(f"[VirtualDisplay] ❌ UIAutomator 异常: {e}")

    return None


def _postprocess_add_visible_to_user(xml_str: str) -> str:
    """为 UIAutomator XML 中缺失 visible-to-user 的节点补上该属性（按 bounds 推断）。

    UIAutomator dump 本身不含 visible-to-user 字段，而辅助服务模式可以提供该字段。
    为保持两种模式属性展示一致，对 UIAutomator XML 做后处理：
      - bounds 有面积（x2>x1 且 y2>y1）→ visible-to-user=true
      - bounds 为零 / 不存在            → visible-to-user=false
    """
    try:
        import xml.etree.ElementTree as ET

        def _strip(s):
            s = s.strip()
            if s.startswith('<?xml'):
                return s[s.find('?>') + 2:].lstrip()
            return s

        root = ET.fromstring(_strip(xml_str))

        def _add_attr(node):
            if node.tag == 'node' and 'visible-to-user' not in node.attrib:
                b_str = node.get('bounds', '')
                visible = 'false'
                if b_str:
                    m = re.match(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', b_str)
                    if m:
                        x1, y1, x2, y2 = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
                        if x2 > x1 and y2 > y1:
                            visible = 'true'
                node.set('visible-to-user', visible)
            for child in node:
                _add_attr(child)

        _add_attr(root)
        return '<?xml version="1.0" encoding="UTF-8"?>' + ET.tostring(root, encoding='unicode')
    except Exception as e:
        print(f"[PostProcess] add_visible_to_user failed: {e}")
        return xml_str


def pick_accessibility_probe_serial(serial: str) -> str:
    """Pick the best serial for accessibility HTTP probe.

    For SS4, current_serial may be localhost:5559, while the accessibility APK may
    be running on the original physical serial (or vice versa).

    We probe candidate serials and return the one that can successfully reach
    http://localhost:8765/api/status after forwarding.
    """
    try:
        probe = probe_accessibility_service_any(serial)
        ok_serial = (probe or {}).get("ok_serial") or ""
        if ok_serial:
            return ok_serial
    except Exception:
        pass
    # fallback: prefer shell-capable serial (for forward/settings)
    try:
        return pick_accessibility_shell_serial(serial)
    except Exception:
        return serial

@app.get("/api/hierarchy")
def get_hierarchy(display: int = 0, force_accessibility: bool = False, merge_virtual_display: int = -1):
    """获取 UI 层级树。

    参数：
      display                目标 display id（0=主屏，其他值=副屏/虚拟屏）
      force_accessibility    True=优先使用辅助服务，False=优先使用 UIAutomator
      merge_virtual_display  ≥0 时，将指定虚拟 display（如 10）的节点融合到主树的
                             SurfaceView 节点下，并完成坐标映射。
                             典型用法：?display=0&force_accessibility=true&merge_virtual_display=10
    """
    global current_serial
    global hierarchy_xml_cache
    if not current_serial:
         raise HTTPException(status_code=400, detail="Device not connected")
    
    print(f"[Hierarchy] 📋 开始获取Display {display}的UI树...")
    print(f"[Hierarchy] 用户选择数据源: {'辅助服务' if force_accessibility else 'UIAutomator'}")
    if merge_virtual_display >= 0:
        print(f"[Hierarchy] 🔀 启用虚拟 display 融合模式：display {merge_virtual_display} → SurfaceView")
    
    # 根据用户选择使用对应的数据源
    if force_accessibility:
        # 用户选择使用辅助服务
        print(f"[Hierarchy] 🔧 使用辅助服务模式")
        # 更稳健：SS4 场景下对候选 serial 逐个 probe，选能通 /api/status 的那个
        target_serial = pick_accessibility_probe_serial(current_serial)
        if target_serial != current_serial:
            print(f"[Hierarchy] ♿ 辅助服务目标设备序列号修正: {current_serial} -> {target_serial}")

        if check_accessibility_service(target_serial):
            xml_from_accessibility = get_hierarchy_from_accessibility(target_serial, display)
            # 必须包含实际节点才算有效，空 hierarchy 不能直接返回（要 fallback 到 UIAutomator）
            if xml_from_accessibility and '<node' in xml_from_accessibility:
                # ── 虚拟 display 节点融合（辅助服务路径） ────────────────────────
                if merge_virtual_display >= 0:
                    print(f"[Hierarchy] 🔀 [辅助服务] 开始融合 display {merge_virtual_display} 节点...")
                    virtual_xml = get_virtual_display_xml(current_serial, merge_virtual_display, target_serial)
                    if virtual_xml and '<node' in virtual_xml:
                        xml_from_accessibility = inject_virtual_nodes_into_surfaceview(
                            xml_from_accessibility, virtual_xml, merge_virtual_display
                        )
                        print(f"[Hierarchy] ✅ display {merge_virtual_display} 节点已融合到辅助服务树")
                    else:
                        print(f"[Hierarchy] ⚠️ display {merge_virtual_display} 无可用节点，跳过融合")
                # ─────────────────────────────────────────────────────────────
                print(f"[Hierarchy] ✅ 使用辅助服务数据源")
                return {"xml": xml_from_accessibility, "source": "accessibility",
                        "merged_virtual_display": merge_virtual_display if merge_virtual_display >= 0 else None}
            elif xml_from_accessibility:
                print(f"[Hierarchy] ⚠️ 辅助服务返回空节点树（display={display}），fallback到UIAutomator")
            else:
                print(f"[Hierarchy] ⚠️ 辅助服务获取失败，fallback到UIAutomator")
        else:
            print(f"[Hierarchy] ⚠️ 辅助服务不可用，fallback到UIAutomator")
    
    # 步骤1：优先使用UIAutomator
    print(f"[Hierarchy] 🔍 使用UIAutomator获取...")
    uiautomator_xml = None
    try:
        import xml.etree.ElementTree as ET
        d = adb.device(serial=current_serial)
        dump_path = f"/sdcard/uidump_all.xml"
        
        # Clear previous dump
        d.shell(f"rm -f {dump_path}")
        
        # ──────────────────────────────────────────────────────────────────────
        # 获取 hierarchy 策略：
        #   Display 0  → 优先 --windows（含多窗口叠加），失败再 --display 0
        #   非主屏      → 优先 --display N（坐标系与截图完全一致），失败再 --windows+过滤
        # 这样可避免 SS4 多屏场景下 --windows 不含 Display 4 导致树为空的问题。
        # ──────────────────────────────────────────────────────────────────────
        import time
        xml_content = ""
        dump_err = ""

        if display == 0:
            # ── Display 0: --windows 优先 ──────────────────────────────────────
            print(f"[Hierarchy] 🔍 Display 0: 使用 --windows 获取多窗口层级...")
            for attempt in range(3):
                try:
                    cmd = f"uiautomator dump --compressed --windows {dump_path}"
                    dump_err = d.shell(cmd)
                    print(f"[Hierarchy] --windows 输出(attempt {attempt+1}/3): {dump_err}")
                    xml_content = d.shell(f"cat {dump_path}")
                    if xml_content and "<?xml" in xml_content:
                        break
                except Exception as _e:
                    dump_err = str(_e)
                time.sleep(0.3)

            if not xml_content or "<?xml" not in xml_content:
                print(f"[Hierarchy] ⚠️ --windows 失败，fallback 到 --display 0...")
                d.shell(f"rm -f {dump_path}")
                for attempt in range(3):
                    err = d.shell(f"uiautomator dump --compressed --display 0 {dump_path}")
                    print(f"[Hierarchy] --display 0 fallback 输出(attempt {attempt+1}/3): {err}")
                    xml_content = d.shell(f"cat {dump_path}")
                    if xml_content and "<?xml" in xml_content:
                        break
                    time.sleep(0.3)
        else:
            # ── 非主屏: --display N 优先（坐标系与 screencap 一致，无需转换） ──
            print(f"[Hierarchy] 🎯 非主屏 Display {display}: 直接使用 --display 方式（坐标与截图一致）...")
            for attempt in range(3):
                cmd = f"uiautomator dump --compressed --display {display} {dump_path}"
                dump_err = d.shell(cmd)
                print(f"[Hierarchy] --display {display} 输出(attempt {attempt+1}/3): {dump_err}")
                xml_content = d.shell(f"cat {dump_path}")
                if xml_content and "<?xml" in xml_content:
                    break
                time.sleep(0.3)

            if not xml_content or "<?xml" not in xml_content:
                # fallback: --windows 并依赖后续过滤
                print(f"[Hierarchy] ⚠️ --display {display} 失败，fallback 到 --windows+过滤...")
                d.shell(f"rm -f {dump_path}")
                for attempt in range(3):
                    try:
                        cmd = f"uiautomator dump --compressed --windows {dump_path}"
                        dump_err = d.shell(cmd)
                        print(f"[Hierarchy] --windows fallback 输出(attempt {attempt+1}/3): {dump_err}")
                        xml_content = d.shell(f"cat {dump_path}")
                        if xml_content and "<?xml" in xml_content:
                            break
                    except Exception as _e:
                        dump_err = str(_e)
                    time.sleep(0.3)

        if not xml_content or "<?xml" not in xml_content:
            raise Exception(f"Failed to dump hierarchy for display {display}")

        # 清理XML内容
        start = xml_content.find("<?xml")
        end = xml_content.rfind(">")
        if start != -1 and end != -1:
            xml_content = xml_content[start:end+1]
        
        print(f"[Hierarchy] 成功获取UI层级,XML长度: {len(xml_content)}")
        
        uiautomator_xml = xml_content
        
        # 处理多窗口多display XML格式：合并所有相关display的窗口
        try:
            root = ET.fromstring(xml_content)
            
            print(f"[Hierarchy] 📊 XML根标签: {root.tag}")
            
            # 辅助函数：解析bounds字符串
            def parse_bounds(bounds_str):
                if not bounds_str or bounds_str == '':
                    return None
                try:
                    import re
                    match = re.match(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', bounds_str)
                    if match:
                        return {
                            'x1': int(match.group(1)),
                            'y1': int(match.group(2)),
                            'x2': int(match.group(3)),
                            'y2': int(match.group(4))
                        }
                except:
                    pass
                return None
            
            # 辅助函数：格式化bounds
            def format_bounds(x1, y1, x2, y2):
                return f"[{x1},{y1}][{x2},{y2}]"
            
            def bounds_area(b):
                if not b:
                    return 0
                return max(0, b['x2'] - b['x1']) * max(0, b['y2'] - b['y1'])

            def is_zero_bounds(b):
                if not b:
                    return True
                return b['x1'] == b['x2'] or b['y1'] == b['y2']

            def union_bounds(a, b):
                if not a:
                    return b
                if not b:
                    return a
                return {
                    'x1': min(a['x1'], b['x1']),
                    'y1': min(a['y1'], b['y1']),
                    'x2': max(a['x2'], b['x2']),
                    'y2': max(a['y2'], b['y2']),
                }

            # 递归应用 affine transform（scale + offset）
            def transform_node_bounds(node, scale_x: float, scale_y: float, offset_x: float, offset_y: float):
                bounds_str = node.get('bounds')
                if bounds_str:
                    b = parse_bounds(bounds_str)
                    if b:
                        nx1 = int(round(b['x1'] * scale_x + offset_x))
                        ny1 = int(round(b['y1'] * scale_y + offset_y))
                        nx2 = int(round(b['x2'] * scale_x + offset_x))
                        ny2 = int(round(b['y2'] * scale_y + offset_y))
                        node.set('bounds', format_bounds(nx1, ny1, nx2, ny2))

                for child in node:
                    if child.tag == 'node':
                        transform_node_bounds(child, scale_x, scale_y, offset_x, offset_y)

            # 对某些车型/窗口，uiautomator 会输出大量 [0,0][0,0] 的叶子节点，导致无法命中。
            # 这里对“有意义”的节点（text/resource-id/clickable=true）在 bounds 为 0 时，继承最近的非 0 祖先 bounds。
            def fix_zero_bounds_for_actionable_nodes(node, inherited_bounds=None):
                b = parse_bounds(node.get('bounds', ''))
                node_has_action = bool(node.get('text')) or bool(node.get('resource-id')) or node.get('clickable') == 'true'

                # 如果当前节点 bounds 为 0，且它是可操作/可识别的节点，则继承祖先 bounds
                if node_has_action and is_zero_bounds(b) and inherited_bounds:
                    node.set('bounds', format_bounds(inherited_bounds['x1'], inherited_bounds['y1'], inherited_bounds['x2'], inherited_bounds['y2']))
                    b = inherited_bounds

                # 更新继承 bounds：只使用非 0 的 bounds 作为后续子节点的参考
                next_inherited = inherited_bounds
                if b and not is_zero_bounds(b):
                    next_inherited = b

                for child in node:
                    if child.tag == 'node':
                        fix_zero_bounds_for_actionable_nodes(child, next_inherited)
            
            # 检查是否是多窗口格式 (<displays>)
            if root.tag == 'displays':
                print(f"[Hierarchy] ✅ 检测到多窗口多display格式，开始合并所有相关display...")
                
                # 创建一个新的hierarchy根节点
                merged_hierarchy = ET.Element('hierarchy')
                merged_hierarchy.set('rotation', '0')
                
                window_count = 0
                node_count = 0
                
                # 遍历所有display
                for display_elem in root.findall('.//display'):
                    display_id = display_elem.get('id', 'unknown')
                    
                    # **修复：只处理当前请求的display，避免坐标混乱**
                    # 之前合并多个display导致坐标转换错误
                    if display_id == str(display):
                        print(f"[Hierarchy] 📱 处理Display {display_id} (仅当前请求的display)...")
                        
                        for window_elem in display_elem.findall('window'):
                            window_count += 1
                            window_title = window_elem.get('title', '')
                            window_bounds = window_elem.get('bounds', '')
                            window_type = window_elem.get('type', '')
                            
                            print(f"[Hierarchy]   窗口{window_count}: title='{window_title}', bounds={window_bounds}")
                            
                            # 解析窗口的bounds，获取偏移量
                            window_bounds_parsed = parse_bounds(window_bounds)
                            dst_bounds = window_bounds_parsed
                            
                            # 获取window下的hierarchy节点
                            hierarchy_elem = window_elem.find('hierarchy')
                            if hierarchy_elem is not None:
                                # 打印hierarchy下有多少个node
                                top_level_nodes = hierarchy_elem.findall('node')
                                print(f"[Hierarchy]     hierarchy下有 {len(top_level_nodes)} 个顶层node")
                                
                                # 递归统计所有子节点数量
                                def count_all_nodes(parent):
                                    count = 0
                                    for child in parent:
                                        if child.tag == 'node':
                                            count += 1
                                            count += count_all_nodes(child)
                                    return count
                                
                                total_nodes = count_all_nodes(hierarchy_elem)
                                print(f"[Hierarchy]     包含总共 {total_nodes} 个节点（含子节点）")
                                
                                # 将hierarchy下的所有node添加到merged_hierarchy
                                for node in top_level_nodes:
                                    # 深拷贝节点（会包含所有子节点）
                                    node_copy = ET.fromstring(ET.tostring(node))

                                    # --- 关键修正：对 SS2/SS2MAX 等设备，window 内 hierarchy 的坐标系可能是“逻辑分辨率”
                                    # 例如 src 为 [0,0][1906,1440]，但 window bounds 为 [20,1440][2860,1620]。
                                    # 此时需要 scale + offset 的 affine transform，而不是简单 offset。
                                    src_union = None
                                    try:
                                        # 用当前顶层节点自身 bounds 作为 src 坐标系（比 window 更可靠）
                                        src_union = parse_bounds(node_copy.get('bounds', ''))
                                        if not src_union or is_zero_bounds(src_union):
                                            # fallback：合并所有顶层节点的非 0 bounds
                                            for tn in top_level_nodes:
                                                tb = parse_bounds(tn.get('bounds', ''))
                                                if tb and not is_zero_bounds(tb):
                                                    src_union = union_bounds(src_union, tb)
                                    except Exception:
                                        src_union = None

                                    scale_x = 1.0
                                    scale_y = 1.0
                                    offset_x = 0.0
                                    offset_y = 0.0

                                    # **关键修复V2：更智能的坐标转换判断**
                                    # 核心策略：
                                    # 1. 优先检查单个节点的bounds，而不是合并后的src_union
                                    # 2. 如果节点bounds在window范围内(±margin)，说明已是绝对坐标
                                    # 3. 如果节点bounds远小于window起点，说明是相对坐标，需要转换
                                    
                                    # 获取设备类型，对SS2等设备做特殊处理
                                    ss_type = detect_ss_device(current_serial) if current_serial else None
                                    
                                    # 全屏窗口判断（window起点在原点附近）
                                    is_fullscreen_window = dst_bounds and dst_bounds['x1'] < 100 and dst_bounds['y1'] < 100
                                    
                                    if is_fullscreen_window:
                                        # 全屏窗口，hierarchy坐标已经是绝对坐标，不需要转换
                                        print(f"[Hierarchy]       ✅ 全屏窗口(window起点[{dst_bounds['x1']},{dst_bounds['y1']}]接近原点)，跳过转换")
                                    elif dst_bounds and src_union and not is_zero_bounds(src_union):
                                        src_start_x = src_union['x1']
                                        src_start_y = src_union['y1']
                                        dst_start_x = dst_bounds['x1']
                                        dst_start_y = dst_bounds['y1']
                                        
                                        print(f"[Hierarchy]       📐 坐标分析:")
                                        print(f"[Hierarchy]          Window: [{dst_bounds['x1']},{dst_bounds['y1']}]->[{dst_bounds['x2']},{dst_bounds['y2']}]")
                                        print(f"[Hierarchy]          Node:   [{src_start_x},{src_start_y}]->[{src_union['x2']},{src_union['y2']}]")
                                        print(f"[Hierarchy]          Device: {ss_type or 'Unknown'}")
                                        
                                        # 策略1: 检查节点是否在window范围内（考虑±200的margin）
                                        margin = 200
                                        node_in_window_range_x = (dst_bounds['x1'] - margin <= src_start_x <= dst_bounds['x2'] + margin)
                                        node_in_window_range_y = (dst_bounds['y1'] - margin <= src_start_y <= dst_bounds['y2'] + margin)
                                        
                                        print(f"[Hierarchy]          策略1: 节点在window范围内? X={node_in_window_range_x}, Y={node_in_window_range_y}")
                                        
                                        # 策略2: 检查节点是否明显是相对坐标（起点接近0）
                                        node_near_origin = (src_start_x < 50 and src_start_y < 50)
                                        print(f"[Hierarchy]          策略2: 节点接近原点? {node_near_origin}")
                                        
                                        # 策略3: 检查节点与window的相对位置关系
                                        # 如果节点坐标远小于window起点，说明是相对坐标
                                        node_much_smaller = (src_start_x < dst_start_x - 100) and (src_start_y < dst_start_y - 100)
                                        print(f"[Hierarchy]          策略3: 节点坐标远小于window? {node_much_smaller}")
                                        
                                        # 决策：如果节点在window范围内，或者节点坐标不是明显的相对坐标，则不转换
                                        should_transform = False
                                        reason = ""
                                        
                                        if node_in_window_range_x and node_in_window_range_y:
                                            should_transform = False
                                            reason = "节点坐标在window范围内，已是绝对坐标"
                                        elif node_near_origin and not (node_in_window_range_x and node_in_window_range_y):
                                            should_transform = True
                                            reason = "节点坐标接近原点，是相对坐标"
                                        elif node_much_smaller:
                                            # 对于SS2设备，即使节点坐标小，也可能已经是绝对坐标
                                            # 需要更仔细的判断
                                            if ss_type == "SS2":
                                                # SS2特殊处理：如果节点x坐标>1000，大概率是绝对坐标
                                                if src_start_x > 1000:
                                                    should_transform = False
                                                    reason = "SS2设备，节点X>1000，判断为绝对坐标"
                                                else:
                                                    should_transform = True
                                                    reason = "SS2设备，节点坐标远小于window，判断为相对坐标"
                                            else:
                                                should_transform = True
                                                reason = "节点坐标远小于window起点，是相对坐标"
                                        else:
                                            should_transform = False
                                            reason = "无明确特征，保持不转换（安全策略）"
                                        
                                        print(f"[Hierarchy]          🎯 决策: {'需要转换' if should_transform else '不转换'} - {reason}")
                                        
                                        if should_transform:
                                            # 相对坐标（local坐标系），需要缩放但不加全局偏移
                                            # Fix C: 节点已在local坐标系，offset不应加 dst_bounds['x1']
                                            src_w = max(1, src_union['x2'] - src_union['x1'])
                                            src_h = max(1, src_union['y2'] - src_union['y1'])
                                            dst_w = max(1, dst_bounds['x2'] - dst_bounds['x1'])
                                            dst_h = max(1, dst_bounds['y2'] - dst_bounds['y1'])
                                            scale_x = dst_w / src_w
                                            scale_y = dst_h / src_h
                                            # 目标是将节点对齐到 local(0,0)，不加全局window起点偏移
                                            offset_x = 0.0 - src_union['x1'] * scale_x
                                            offset_y = 0.0 - src_union['y1'] * scale_y
                                            print(f"[Hierarchy]          ✅ 应用转换(本地输出): scale=({scale_x:.4f},{scale_y:.4f}), offset=({offset_x:.1f},{offset_y:.1f})")
                                        else:
                                            # 节点是全局绝对坐标，不缩放
                                            scale_x = 1.0
                                            scale_y = 1.0
                                            # Fix B: 若 window 本身有全局偏移（x1>100），需减去 window 原点
                                            # 将全局坐标平移为 display 本地坐标
                                            if dst_bounds and (dst_bounds['x1'] > 100 or dst_bounds['y1'] > 100):
                                                offset_x = -float(dst_bounds['x1'])
                                                offset_y = -float(dst_bounds['y1'])
                                                print(f"[Hierarchy]          🔄 全局→本地平移: offset=({offset_x:.0f},{offset_y:.0f})")
                                            else:
                                                offset_x = 0.0
                                                offset_y = 0.0
                                                print(f"[Hierarchy]          ✅ 保持原坐标")

                                    transform_node_bounds(node_copy, scale_x, scale_y, offset_x, offset_y)
                                    fix_zero_bounds_for_actionable_nodes(node_copy, None)
                                    
                                    merged_hierarchy.append(node_copy)
                                    node_count += 1
                                    
                                    # 打印节点信息和调试特定节点
                                    node_bounds = node_copy.get('bounds', 'unknown')
                                    node_class = node_copy.get('class', 'unknown')
                                    node_text = node_copy.get('text', '')
                                    child_count = count_all_nodes(node_copy)
                                    print(f"[Hierarchy]     添加节点{node_count}: class={node_class}, bounds={node_bounds}, 子节点数={child_count}")
                                    
                                    # 调试：打印所有包含"关闭"文本的节点
                                    if '关闭' in node_text or node_copy.get('content-desc', '') == '关闭':
                                        print(f"[Hierarchy]     ⚠️ 发现'关闭'节点:")
                                        print(f"[Hierarchy]        Window: {window_title}")
                                        print(f"[Hierarchy]        转换前bounds: {node.get('bounds')}")
                                        print(f"[Hierarchy]        转换后bounds: {node_bounds}")
                                        print(f"[Hierarchy]        Scale: ({scale_x:.4f}, {scale_y:.4f})")
                                        print(f"[Hierarchy]        Offset: ({offset_x:.2f}, {offset_y:.2f})")
                                        print(f"[Hierarchy]        Text: '{node_text}'")
                                        print(f"[Hierarchy]        Resource-ID: {node_copy.get('resource-id', '')}")
                                        print(f"[Hierarchy]        Clickable: {node_copy.get('clickable', 'false')}")
                
                print(f"[Hierarchy] ✅ 合并完成：{window_count} 个窗口，{node_count} 个顶层节点")
                
                xml_content = ET.tostring(merged_hierarchy, encoding='unicode')
                xml_content = '<?xml version="1.0" encoding="UTF-8"?>' + xml_content
                print(f"[Hierarchy] 合并后XML长度: {len(xml_content)}")
                
                # 打印合并后的根节点信息用于调试
                try:
                    debug_root = ET.fromstring(xml_content)
                    if len(debug_root) > 0:
                        first_node = debug_root[0]
                        print(f"[Hierarchy] 第一个顶层节点: class={first_node.get('class')}, bounds={first_node.get('bounds')}")
                except:
                    pass
                    
            elif root.tag == 'hierarchy':
                print(f"[Hierarchy] ℹ️ 单hierarchy格式")
                # 打印根节点信息
                first_nodes = root.findall('node')
                if first_nodes:
                    print(f"[Hierarchy] 顶层节点数: {len(first_nodes)}")
                    for i, node in enumerate(first_nodes[:3]):  # 打印前3个
                        print(f"[Hierarchy]   节点{i}: class={node.get('class')}, bounds={node.get('bounds')}")

                # ── 非主屏坐标变换 ─────────────────────────────────────────────
                # SS4 的 uiautomator dump --display N 实际返回 Display 0 的全局坐标系
                # （例如 Display 0=6296x1740，而 Display 4=3840x2160）
                # 需要把 bounds 从 Display 0 的全局坐标映射到 Display N 的本地坐标（0,0 起点）
                if display != 0 and first_nodes:
                    try:
                        # Fix A: 先获取目标 display 分辨率，再计算是否需要变换
                        # 若 display_info_cache 为空（如用户只调了 /connect 未调 /displays），
                        # 自动触发一次 refresh 填充缓存，确保坐标变换有据可依
                        if not display_info_cache and current_serial:
                            print(f"[Hierarchy] ⚠️ display_info_cache 为空，自动刷新...")
                            refresh_display_mapping(current_serial)

                        target_w, target_h = None, None
                        for di in display_info_cache:
                            if di.get('id') == str(display):
                                res_m = re.search(r'(\d+)x(\d+)', di.get('description', ''))
                                if res_m:
                                    target_w = int(res_m.group(1))
                                    target_h = int(res_m.group(2))
                                    break

                        # 收集所有顶层节点的非零 bounds，计算全局坐标包围盒
                        top_bounds = [parse_bounds(n.get('bounds', '')) for n in first_nodes]
                        top_bounds = [b for b in top_bounds if b and not is_zero_bounds(b)]
                        if top_bounds:
                            src_x_min = min(b['x1'] for b in top_bounds)
                            src_y_min = min(b['y1'] for b in top_bounds)
                            src_x_max = max(b['x2'] for b in top_bounds)
                            src_y_max = max(b['y2'] for b in top_bounds)
                            span_w = src_x_max - src_x_min
                            span_h = src_y_max - src_y_min

                            # 条件1: 起点偏离原点（全局坐标系有偏移，需要平移）
                            offset_remap = src_x_min > 50 or src_y_min > 50

                            # 条件2: 跨度"超出"期望分辨率（>20%），说明 bounds 来自更大的坐标系
                            # 例如 Display 4 但 bounds 来自 Display 0（6296×1740）→ span_w=6296>3840×1.2 → 缩放
                            # 条件2b: 坐标系"偏小"（UIAutomator 使用了不同的逻辑分辨率）
                            # 例如 Display 4（3840×2160），UIAutomator 返回 [20,0][2691,1740]（y_max=Display0高度）
                            # 此时 h_ratio=1740/2160=0.806 < 1.0，不触发旧条件，但节点会"到不了底"
                            # 判断条件：根节点贴近原点（y_min≤10, x_min≤100）且两轴均偏小（<0.9）
                            scale_remap = False
                            if target_w and target_h and span_w > 100 and span_h > 100:
                                w_ratio = span_w / target_w
                                h_ratio = span_h / target_h
                                if w_ratio > 1.2 or h_ratio > 1.2:
                                    # 坐标系比目标大 → 缩小
                                    scale_remap = True
                                    print(f"[Hierarchy] ⚠️ 坐标系超出期望: 节点范围 {span_w}x{span_h}"
                                          f" > 期望 {target_w}x{target_h}"
                                          f" (ratio={w_ratio:.2f}x{h_ratio:.2f})")
                                elif ((w_ratio < 0.9 or h_ratio < 0.9)
                                      and w_ratio <= 1.1 and h_ratio <= 1.1
                                      and src_y_min <= 10 and src_x_min <= 100):
                                    # 坐标系比目标小 → 放大（UIAutomator 使用了不同的逻辑分辨率）
                                    # 额外条件：根节点贴近原点，说明这是"全屏"坐标系而非局部小窗口
                                    # 使用 or 而非 and：任一轴偏小即触发
                                    # 例如 SS4 Display4：span_w=3840=target_w(w_ratio=1.0), span_h=1740<2160(h_ratio=0.806)
                                    # → 仅 h_ratio<0.9 满足，w_ratio<=1.1 满足，触发放大
                                    scale_remap = True
                                    print(f"[Hierarchy] ⚠️ 坐标空间偏小: 节点范围 {span_w}x{span_h}"
                                          f" < 期望 {target_w}x{target_h}"
                                          f" (ratio={w_ratio:.2f}x{h_ratio:.2f}),"
                                          f" 根节点起点[{src_x_min},{src_y_min}]贴近原点,"
                                          f" 触发放大变换")

                            needs_remap = offset_remap or scale_remap
                            print(f"[Hierarchy] 📐 Display {display} bounds 包围盒:"
                                  f" [{src_x_min},{src_y_min}][{src_x_max},{src_y_max}],"
                                  f" offset_remap={offset_remap},"
                                  f" scale_remap={scale_remap},"
                                  f" 需要变换={needs_remap}")

                            if needs_remap:
                                if scale_remap and target_w and target_h:
                                    # 跨度超出目标分辨率：按目标分辨率缩放（来自不同/更大坐标系）
                                    src_w = max(1, span_w)
                                    src_h = max(1, span_h)
                                    sc_x = target_w / src_w
                                    sc_y = target_h / src_h
                                    off_x = -src_x_min * sc_x
                                    off_y = -src_y_min * sc_y
                                    print(f"[Hierarchy] 🔄 缩放+平移变换: [{src_x_min},{src_y_min}][{src_x_max},{src_y_max}] → [0,0][{target_w},{target_h}]")
                                    print(f"[Hierarchy] 🔢 scale=({sc_x:.4f},{sc_y:.4f}), offset=({off_x:.1f},{off_y:.1f})")
                                elif offset_remap:
                                    # 起点偏移但跨度正常：只做平移，不缩放
                                    # （坐标系与目标一致，只是起点在全局坐标中有偏移）
                                    sc_x = 1.0
                                    sc_y = 1.0
                                    off_x = -float(src_x_min)
                                    off_y = -float(src_y_min)
                                    print(f"[Hierarchy] 🔄 纯平移变换: offset=({off_x:.0f},{off_y:.0f})")
                                # 对所有顶层节点（及其子树）应用变换
                                for n in root:
                                    if n.tag == 'node':
                                        transform_node_bounds(n, sc_x, sc_y, off_x, off_y)
                                xml_content = ET.tostring(root, encoding='unicode')
                                xml_content = '<?xml version="1.0" encoding="UTF-8"?>' + xml_content
                                print(f"[Hierarchy] ✅ 坐标变换完成，XML长度: {len(xml_content)}")
                    except Exception as _remap_err:
                        print(f"[Hierarchy] ⚠️ 非主屏坐标变换异常: {_remap_err}")
            else:
                print(f"[Hierarchy] ⚠️ 未知根标签: {root.tag}")
                
        except Exception as parse_error:
            print(f"[Hierarchy] ❌ XML解析/合并出错: {parse_error}")
            import traceback
            traceback.print_exc()
            # 如果解析失败，返回原始XML
            pass
        
        # 成功获取到XML
        uiautomator_xml = xml_content
        print(f"[Hierarchy] ✅ UIAutomator数据获取成功")
        # 补充 visible-to-user 属性（UIAutomator dump 不提供此字段，按 bounds 有无面积推断）
        uiautomator_xml = _postprocess_add_visible_to_user(uiautomator_xml)
        # cache
        hierarchy_xml_cache[display] = uiautomator_xml
        return {"xml": uiautomator_xml, "source": "uiautomator"}
        
    except Exception as e:
        print(f"[Hierarchy] ❌ UIAutomator获取失败: {e}")
        import traceback
        traceback.print_exc()
        
        # UIAutomator失败：尽量返回缓存，避免前端完全不可用
        cached = hierarchy_xml_cache.get(display)
        if cached:
            print(f"[Hierarchy] 🧰 返回缓存的hierarchy(避免前端中断)，display={display}")
            return {
                "xml": cached,
                "source": "cache",
                "reason": "uiautomator_failed",
                "error": str(e),
            }

        # 无缓存则返回一个空的 hierarchy，仍然 200，前端可提示但不至于崩
        empty_xml = "<?xml version=\"1.0\" encoding=\"UTF-8\"?><hierarchy rotation=\"0\"/>"
        return {
            "xml": empty_xml,
            "source": "empty",
            "reason": "uiautomator_failed",
            "error": str(e),
        }

class ClickRequest(BaseModel):
    x: int
    y: int
    display: int = 0

@app.post("/api/click")
def click_screen(req: ClickRequest):
    global current_serial
    if not current_serial:
         raise HTTPException(status_code=400, detail="Device not connected")
    try:
        d = adb.device(serial=current_serial)
        # Add -d for input if supported (Android 10+)
        if req.display > 0:
            d.shell(f"input -d {req.display} tap {req.x} {req.y}")
        else:
            d.shell(f"input tap {req.x} {req.y}")
        return {"status": "clicked", "x": req.x, "y": req.y, "display": req.display}
    except Exception as e:
         raise HTTPException(status_code=500, detail=str(e))

class SwipeRequest(BaseModel):
    start_x: int
    start_y: int
    end_x: int
    end_y: int
    duration: float = 0.5
    display: int = 0

@app.post("/api/swipe")
def swipe_screen(req: SwipeRequest):
    global current_serial
    if not current_serial:
         raise HTTPException(status_code=400, detail="Device not connected")
    try:
        d = adb.device(serial=current_serial)
        duration_ms = int(req.duration * 1000)
        if req.display > 0:
            d.shell(f"input -d {req.display} swipe {req.start_x} {req.start_y} {req.end_x} {req.end_y} {duration_ms}")
        else:
            d.shell(f"input swipe {req.start_x} {req.start_y} {req.end_x} {req.end_y} {duration_ms}")
        return {"status": "swiped", "start": [req.start_x, req.start_y], "end": [req.end_x, req.end_y], "display": req.display}
    except Exception as e:
         raise HTTPException(status_code=500, detail=str(e))

class BackRequest(BaseModel):
    display: int = 0

@app.post("/api/back")
def back_button(req: BackRequest):
    global current_serial
    if not current_serial:
         raise HTTPException(status_code=400, detail="Device not connected")
    try:
        d = adb.device(serial=current_serial)
        if req.display > 0:
            # keyevent 4 is BACK
            d.shell(f"input -d {req.display} keyevent 4")
        else:
            d.shell(f"input keyevent 4")
        return {"status": "back", "display": req.display}
    except Exception as e:
         raise HTTPException(status_code=500, detail=str(e))



# ── 通用按键事件 ───────────────────────────────────────────────────────────────
_KEY_MAP: Dict[str, int] = {
    "BACK":         4,
    "HOME":         3,
    "RECENTS":      187,
    "APP_SWITCH":   187,
    "VOLUME_UP":    24,
    "VOLUME_DOWN":  25,
    "POWER":        26,
    "MENU":         82,
    "ENTER":        66,
    "DPAD_UP":      19,
    "DPAD_DOWN":    20,
    "DPAD_LEFT":    21,
    "DPAD_RIGHT":   22,
    "DPAD_CENTER":  23,
}

class KeyEventRequest(BaseModel):
    key: str
    display: int = 0

@app.post("/api/keyevent")
def send_keyevent(req: KeyEventRequest):
    global current_serial
    if not current_serial:
        raise HTTPException(status_code=400, detail="Device not connected")
    code = _KEY_MAP.get(req.key.upper())
    if code is None:
        raise HTTPException(status_code=400, detail=f"Unknown key: {req.key}. Valid keys: {list(_KEY_MAP.keys())}")
    try:
        d = adb.device(serial=current_serial)
        if req.display > 0:
            d.shell(f"input -d {req.display} keyevent {code}")
        else:
            d.shell(f"input keyevent {code}")
        return {"ok": True, "key": req.key, "code": code, "display": req.display}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/accessibility/enable")
def enable_accessibility_service():
    """启用辅助服务"""
    global current_serial, ss4_localhost_mapping
    if not current_serial:
        raise HTTPException(status_code=400, detail="Device not connected")

    # 统一使用 resolve_accessibility_target_serial，避免 SS4 上 serial 选择不一致
    target_serial = get_accessibility_target_serial_from_current()
    if target_serial != current_serial:
        print(f"[Accessibility] 🔧 目标设备序列号修正: {current_serial} -> {target_serial}")
    
    try:
        print(f"[Accessibility] 🔧 启用辅助服务...")
        print(f"[Accessibility] 📱 目标设备: {target_serial}")
        
        # 获取当前启用的所有辅助服务
        result = subprocess.run(
            ["adb", "-s", target_serial, "shell", "settings", "get", "secure", "enabled_accessibility_services"],
            capture_output=True, text=True, timeout=3
        )
        
        current_services = result.stdout.strip()
        print(f"[Accessibility] 当前服务: {current_services}")
        
        # 如果已经包含我们的服务，不需要重复添加
        if "com.carui.accessibility" in current_services:
            print(f"[Accessibility] ℹ️ 辅助服务已启用")
            return {
                "status": "success",
                "message": "辅助服务已启用",
                "already_enabled": True
            }
        
        # 添加我们的服务到服务列表
        if current_services and current_services != "null":
            new_services = f"{current_services}:com.carui.accessibility/.CarUIAccessibilityService"
        else:
            new_services = "com.carui.accessibility/.CarUIAccessibilityService"
        
        # 更新设置（使用target_serial）
        subprocess.run(
            ["adb", "-s", target_serial, "shell", "settings", "put", "secure", 
             "enabled_accessibility_services", new_services],
            capture_output=True, text=True, timeout=3
        )
        
        # 确保辅助服务功能已启用
        subprocess.run(
            ["adb", "-s", target_serial, "shell", "settings", "put", "secure",
             "accessibility_enabled", "1"],
            capture_output=True, text=True, timeout=3
        )
        
        # 设置端口转发（使用target_serial）
        subprocess.run(
            ["adb", "-s", target_serial, "forward", "tcp:8765", "tcp:8765"],
            capture_output=True, text=True, timeout=3
        )
        
        print(f"[Accessibility] ✅ 已启用辅助服务")
        print(f"[Accessibility] 新服务列表: {new_services}")
        
        return {
            "status": "success",
            "message": "辅助服务已启用",
            "previous_services": current_services,
            "current_services": new_services
        }
    
    except Exception as e:
        print(f"[Accessibility] ❌ 启用失败: {e}")
        raise HTTPException(status_code=500, detail=f"启用辅助服务失败: {str(e)}")


class EnsureAccessibilityRequest(BaseModel):
    serial: Optional[str] = None
    apk_path: Optional[str] = None
    install_if_missing: bool = True
    enable_service: bool = True
    probe_running: bool = True


@app.post("/api/accessibility/ensure")
def api_ensure_accessibility(req: EnsureAccessibilityRequest):
    """One-click ensure accessibility service is installed/enabled/running.

    If req.serial is empty, uses current_serial.
    """
    global current_serial
    serial = req.serial or current_serial
    if not serial:
        raise HTTPException(status_code=400, detail="Device not connected")
    return ensure_accessibility_service(
        serial,
        apk_path=req.apk_path,
        install_if_missing=req.install_if_missing,
        enable_service=req.enable_service,
        probe_running=req.probe_running,
    )

class InstallApkRequest(BaseModel):
    serial: Optional[str] = None
    no_streaming: bool = False


@app.post("/api/accessibility/install-apk")
def api_install_apk(req: InstallApkRequest):
    """手动安装辅助服务 APK，支持 --no-streaming 模式（适用于某些车机设备）。"""
    global current_serial
    serial = req.serial or current_serial
    if not serial:
        raise HTTPException(status_code=400, detail="未选择设备")

    # 查找 APK 路径（优先 bundled，其次 dev build）
    bundled = os.path.join(script_dir, "CarUIAccessibilityService-debug.apk")
    dev_path = os.path.abspath(
        os.path.join(
            script_dir,
            "..",
            "accessibility_service",
            "build",
            "outputs",
            "apk",
            "debug",
            "CarUIAccessibilityService-debug.apk",
        )
    )
    if os.path.exists(bundled):
        apk_path = bundled
    elif os.path.exists(dev_path):
        apk_path = dev_path
    else:
        raise HTTPException(status_code=404, detail="APK 文件不存在，请先编译辅助服务模块")

    if req.no_streaming:
        cmd = ["adb", "-s", serial, "install", "--no-streaming", "-r", "-t", "-d", apk_path]
    else:
        cmd = ["adb", "-s", serial, "install", "-r", "-t", apk_path]

    result = _adb_run(cmd, timeout=90)
    output = ((result.stdout or "") + (result.stderr or "")).strip()
    success = result.returncode == 0

    # 签名不匹配时自动先卸载再安装（INSTALL_FAILED_UPDATE_INCOMPATIBLE）
    if not success and "INSTALL_FAILED_UPDATE_INCOMPATIBLE" in output:
        print(f"[InstallAPK] ⚠️ 签名不匹配，自动卸载旧版本再重装...")
        uninstall_r = _adb_run(["adb", "-s", serial, "uninstall", "com.carui.accessibility"], timeout=15)
        uninstall_out = ((uninstall_r.stdout or "") + (uninstall_r.stderr or "")).strip()
        print(f"[InstallAPK] 卸载结果: {uninstall_out}")
        # 重新安装
        result2 = _adb_run(cmd, timeout=90)
        output2 = ((result2.stdout or "") + (result2.stderr or "")).strip()
        success = result2.returncode == 0
        output = f"[自动卸载旧版] {uninstall_out}\n[重新安装] {output2}"

    return {
        "success": success,
        "output": output,
        "serial": serial,
        "apk_path": apk_path,
        "no_streaming": req.no_streaming,
    }


@app.post("/api/accessibility/disable")
def disable_accessibility_service():
    """禁用辅助服务，恢复原有服务（如语音服务）"""
    global current_serial, ss4_localhost_mapping
    if not current_serial:
        raise HTTPException(status_code=400, detail="Device not connected")

    # 统一使用 resolve_accessibility_target_serial，避免 SS4 上 serial 选择不一致
    target_serial = get_accessibility_target_serial_from_current()
    if target_serial != current_serial:
        print(f"[Accessibility] 🛑 目标设备序列号修正: {current_serial} -> {target_serial}")
    
    try:
        print(f"[Accessibility] 🛑 禁用辅助服务...")
        print(f"[Accessibility] 📱 目标设备: {target_serial}")
        
        # 获取当前启用的所有辅助服务
        result = subprocess.run(
            ["adb", "-s", target_serial, "shell", "settings", "get", "secure", "enabled_accessibility_services"],
            capture_output=True, text=True, timeout=3
        )
        
        current_services = result.stdout.strip()
        print(f"[Accessibility] 当前服务: {current_services}")
        
        # 移除我们的辅助服务
        if "com.carui.accessibility" in current_services:
            # 将服务列表分割，移除我们的服务，然后重新组合
            services_list = current_services.split(':')
            services_list = [s for s in services_list if 'com.carui.accessibility' not in s]
            
            new_services = ':'.join(services_list)
            
            # 更新设置（使用target_serial）
            subprocess.run(
                ["adb", "-s", target_serial, "shell", "settings", "put", "secure", 
                 "enabled_accessibility_services", new_services],
                capture_output=True, text=True, timeout=3
            )
            
            print(f"[Accessibility] ✅ 已禁用辅助服务")
            print(f"[Accessibility] 新服务列表: {new_services}")
            
            return {
                "status": "success",
                "message": "辅助服务已禁用",
                "previous_services": current_services,
                "current_services": new_services
            }
        else:
            print(f"[Accessibility] ℹ️ 辅助服务未启用")
            return {
                "status": "success",
                "message": "辅助服务未启用，无需操作"
            }
    
    except Exception as e:
        print(f"[Accessibility] ❌ 禁用失败: {e}")
        raise HTTPException(status_code=500, detail=f"禁用辅助服务失败: {str(e)}")


@app.post("/api/accessibility/restart-service")
def api_restart_accessibility_service():
    """通过切换 accessibility_enabled 开关（0→1）强制重新激活辅助服务，等待端口 8765 响应。"""
    global current_serial
    if not current_serial:
        raise HTTPException(status_code=400, detail="Device not connected")
    target_serial = pick_accessibility_shell_serial(current_serial)
    steps: List[str] = []
    try:
        import time
        _adb_run(["adb", "-s", target_serial, "forward", "tcp:8765", "tcp:8765"], timeout=5)
        steps.append("📡 端口转发: tcp:8765 → tcp:8765")
        r0 = _adb_run(["adb", "-s", target_serial, "shell", "settings", "put", "secure",
                        "accessibility_enabled", "0"], timeout=5)
        steps.append(f"🔘 关闭 accessibility_enabled: rc={r0.returncode}")
        time.sleep(1)
        r1 = _adb_run(["adb", "-s", target_serial, "shell", "settings", "put", "secure",
                        "accessibility_enabled", "1"], timeout=5)
        steps.append(f"✅ 开启 accessibility_enabled: rc={r1.returncode}")
        try:
            rc_cmd = _adb_run(
                ["adb", "-s", target_serial, "shell", "cmd", "accessibility", "enable",
                 "com.carui.accessibility/.CarUIAccessibilityService"],
                timeout=8
            )
            steps.append(f"♿ cmd accessibility enable: rc={rc_cmd.returncode}")
        except Exception as _e:
            steps.append(f"♿ cmd accessibility enable: 跳过 ({_e})")
        running = False
        for i in range(30):
            if check_accessibility_service(target_serial):
                running = True
                steps.append(f"🎉 服务在约 {(i + 1) * 0.5:.1f}s 后响应")
                break
            time.sleep(0.5)
        if not running:
            steps.append("⏰ 等待超时（15s），服务仍未响应")
        return {"success": running, "running": running, "steps": steps, "target_serial": target_serial}
    except Exception as e:
        steps.append(f"❌ 异常: {e}")
        return {"success": False, "running": False, "steps": steps, "error": str(e),
                "target_serial": target_serial}


@app.get("/api/accessibility/status")
def get_accessibility_status():
    """获取辅助服务状态"""
    global current_serial, ss4_localhost_mapping
    if not current_serial:
        raise HTTPException(status_code=400, detail="Device not connected")

    # enabled 检测依赖 settings 命令，优先挑能跑 settings 的 serial
    shell_serial = pick_accessibility_shell_serial(current_serial)
    # probe 需要探测 HTTP 服务，SS4 场景下可能需要在 original_serial / localhost 两者之间尝试
    probe_any = probe_accessibility_service_any(current_serial)
    probe_ok_serial = probe_any.get("ok_serial") or ""
    # 对外仍保留 target_serial 字段：表示本次探测认为更可能有效的 serial
    target_serial = probe_ok_serial or shell_serial or current_serial
    if target_serial != current_serial:
        print(f"[Accessibility] 📊 目标设备序列号修正: {current_serial} -> {target_serial}")
    
    try:
        print(f"[Accessibility] 📊 查询辅助服务状态...")
        print(f"[Accessibility] 📱 目标设备: {target_serial}")
        
        enabled_services = ""
        is_enabled = False
        enabled_check_error = ""

        # 检查是否启用（注意：部分车机/SS4 环境 settings 命令可能不可用）
        try:
            result = subprocess.run(
                [
                    "adb",
                    "-s",
                    shell_serial,
                    "shell",
                    "settings",
                    "get",
                    "secure",
                    "enabled_accessibility_services",
                ],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            enabled_services = (result.stdout or "").strip()
            combined = (result.stdout or "") + (result.stderr or "")
            if result.returncode != 0 or "not found" in combined.lower():
                enabled_check_error = (result.stderr or "").strip() or combined.strip() or "settings command failed"
            is_enabled = "com.carui.accessibility" in enabled_services
        except Exception as e:
            enabled_check_error = str(e)

        is_running = bool(probe_any.get("ok"))
        
        return {
            "serial": current_serial,
            "target_serial": target_serial,
            "shell_serial": shell_serial,
            "enabled": is_enabled,
            "running": is_running,
            "all_services": enabled_services
            ,
            "enabled_check_error": enabled_check_error,
            "probe": probe_any,
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/restart-server")
def restart_server():
    """重启Python服务器进程"""
    global current_serial
    try:
        import signal
        import time
        
        # 获取当前进程的PID
        current_pid = os.getpid()
        print(f"[RESTART] 🔄 准备重启服务器，当前PID: {current_pid}")

        # 写入“重启请求标记文件”，让 IDE 插件侧可以立即感知并主动重启进程
        # （避免监控线程 5s*3 次失败后才重启，导致用户体感很慢）
        try:
            flag_path = os.path.join(script_dir, "restart_requested.flag")
            with open(flag_path, "w", encoding="utf-8") as f:
                f.write(f"pid={current_pid}\n")
                f.write(f"ts={int(time.time())}\n")
            print(f"[RESTART] 🏁 写入重启标记文件: {flag_path}")
        except Exception as e:
            print(f"[RESTART] ⚠️ 写入重启标记文件失败: {e}")
        
        # 在重启前先禁用辅助服务，恢复设备原有服务
        # 注意：SS4 场景下 current_serial 可能是 localhost:5559，但辅助服务运行在原始物理 serial 上。
        if current_serial:
            try:
                print(f"[RESTART] 🛑 重启前禁用辅助服务...")
                target_serial = resolve_accessibility_target_serial(current_serial)
                if target_serial != current_serial:
                    print(f"[RESTART] ♿ SS设备修正辅助服务目标序列号: {current_serial} -> {target_serial}")

                result = subprocess.run(
                    ["adb", "-s", target_serial, "shell", "settings", "get", "secure", "enabled_accessibility_services"],
                    capture_output=True, text=True, timeout=3
                )
                
                current_services = result.stdout.strip()
                
                if "com.carui.accessibility" in current_services:
                    # 移除我们的辅助服务
                    services_list = current_services.split(':')
                    services_list = [s for s in services_list if 'com.carui.accessibility' not in s]
                    new_services = ':'.join(services_list)
                    
                    subprocess.run(
                        ["adb", "-s", target_serial, "shell", "settings", "put", "secure", 
                         "enabled_accessibility_services", new_services],
                        capture_output=True, text=True, timeout=3
                    )
                    
                    print(f"[RESTART] ✅ 已禁用辅助服务，恢复原有服务")
                else:
                    print(f"[RESTART] ℹ️ 辅助服务未启用，无需禁用")
            except Exception as e:
                print(f"[RESTART] ⚠️ 禁用辅助服务失败: {e}，继续重启")
        
        # 返回成功响应后，延迟终止进程（让响应能够发送出去）
        # NOTE: 某些环境下 uvicorn/依赖线程对 SIGTERM 退出不够“干练”，这里做硬退出兜底。
        def delayed_restart():
            time.sleep(0.25)  # 等待响应发送（尽量短）
            print(f"[RESTART] 💀 终止当前进程 (SIGTERM)...")
            try:
                os.kill(current_pid, signal.SIGTERM)
            except Exception as _e:
                print(f"[RESTART] ⚠️ SIGTERM failed: {_e}")

            # 再给一点点时间做优雅退出，否则直接 SIGKILL
            time.sleep(0.4)
            try:
                print(f"[RESTART] ☠️ 强制终止当前进程 (SIGKILL)...")
                os.kill(current_pid, signal.SIGKILL)
            except Exception as _e:
                print(f"[RESTART] ⚠️ SIGKILL failed: {_e}")

            # 最终兜底：硬退出
            try:
                os._exit(0)
            except Exception:
                pass
        
        # 在后台线程中执行重启
        import threading
        threading.Thread(target=delayed_restart, daemon=True).start()
        
        return {
            "status": "success",
            "message": "服务器将在0.5秒后重启（已自动禁用辅助服务）",
            "pid": current_pid
        }
    except Exception as e:
        print(f"[RESTART] ❌ 重启失败: {e}")
        raise HTTPException(status_code=500, detail=f"重启失败: {str(e)}")


@app.post("/api/hard-exit")
def hard_exit():
    """瞬间杀死 Python 服务进程（不给 uvicorn/线程留情面）。

    插件侧 hardStop 已经很暴力了；这个接口主要用于：
    - 前端想“立刻断电”，直接让当前 server 自杀
    - 某些情况下 process 句柄丢失，仍可通过 HTTP 让其自杀
    """
    try:
        import signal
        import threading
        import time

        pid = os.getpid()
        print(f"[HARD_EXIT] ☠️ hard exit requested, pid={pid}")

        def killer():
            time.sleep(0.15)
            try:
                os.kill(pid, signal.SIGKILL)
            except Exception:
                try:
                    os._exit(0)
                except Exception:
                    pass

        threading.Thread(target=killer, daemon=True).start()
        return {"status": "ok", "pid": pid}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/")
def read_root():
    return JSONResponse(content={"message": "Car UI Tool API is running. Go to /static/index.html"})

def find_available_port(start_port=18888, max_attempts=10):
    """Find an available port starting from start_port"""
    import socket
    
    for port in range(start_port, start_port + max_attempts):
        try:
            # Try to bind to the port
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(('127.0.0.1', port))
            sock.close()
            print(f"✅ Port {port} is available")
            return port
        except OSError:
            print(f"❌ Port {port} is already in use, trying next...")
            continue
    
    # If no port found, raise error
    raise RuntimeError(f"No available port found in range {start_port}-{start_port + max_attempts - 1}")


def try_bind_port(port: int) -> bool:
    """Best-effort check if a port is available by binding to it."""
    import socket
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(('127.0.0.1', port))
        sock.close()
        return True
    except Exception:
        return False


def read_preferred_port(port_file: str) -> Optional[int]:
    """Prefer reusing last port so that JCEF page origin stays stable across restart/hard-exit."""
    try:
        if os.path.exists(port_file):
            txt = open(port_file, 'r', encoding='utf-8').read().strip()
            if txt:
                p = int(txt)
                if 1024 < p < 65535:
                    return p
    except Exception:
        return None
    return None

if __name__ == "__main__":
    # Find available port
    try:
        # Try to reuse last port first (stable origin for embedded JCEF)
        port_file = os.path.join(os.path.dirname(__file__), "server_port.txt")
        preferred = read_preferred_port(port_file)
        if preferred and try_bind_port(preferred):
            port = preferred
            print(f"🔁 Reusing previous port: {port}")
        else:
            port = find_available_port(start_port=18888, max_attempts=10)
        print(f"🚀 Starting server on port {port}")
        
        # Write port to file for plugin to read
        with open(port_file, 'w') as f:
            f.write(str(port))
        print(f"📝 Port number saved to {port_file}")
        
        # Start server
        uvicorn.run(app, host="0.0.0.0", port=port)
    except Exception as e:
        print(f"❌ Failed to start server: {e}")
        sys.exit(1)
