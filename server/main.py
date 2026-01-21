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

app = FastAPI()

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Robust Path Resolution using sys.path[0]
import os
import sys

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


def resolve_accessibility_target_serial(serial: str) -> str:
    """辅助服务相关操作需要在“物理设备”上执行。

    对于 SS4 这类会被转换成 localhost:5559 的设备：
    - current_serial 用于截图/输入事件
    - 辅助服务 APK 仍运行在原始物理 serial 上
    """
    global ss4_localhost_mapping
    if serial == "localhost:5559" and serial in ss4_localhost_mapping:
        return ss4_localhost_mapping[serial].get("original_serial", serial)
    return serial


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

def refresh_display_mapping(serial: str):
    global display_mapping, display_info_cache
    try:
        # 1. Get Physical IDs from SurfaceFlinger
        sf_output = subprocess.run(["adb", "-s", serial, "shell", "dumpsys SurfaceFlinger --display-id"], 
                                   capture_output=True, text=True, timeout=5).stdout
        
        # 2. Get Logical ID mapping from dumpsys display
        display_output = subprocess.run(["adb", "-s", serial, "shell", "dumpsys display"], 
                                       capture_output=True, text=True, timeout=5).stdout
        
        new_mapping = {}
        info_list = []
        
        # Parse SurfaceFlinger for physical IDs and names
        sf_matches = re.finditer(r"Display ([\d]{10,20}) .*?displayName=\"([^\"]+)\"", sf_output)
        phys_to_name = {m.group(1): m.group(2) for m in sf_matches}
        
        # Parse dumpsys display for Logical to Physical mapping
        devices_blocks = display_output.split("Display Device ")
        for block in devices_blocks[1:]:
            id_match = re.search(r"mDisplayId=([\d]+)", block)
            unique_match = re.search(r"mUniqueId=local:([\d]{10,20})", block)
            if id_match and unique_match:
                logical = id_match.group(1)
                physical = unique_match.group(1)
                new_mapping[logical] = physical
                
                name = phys_to_name.get(physical, f"Display {logical}")
                # Try to find resolution
                res_match = re.search(r"([\d]+) x ([\d]+),", block)
                res_str = ""
                if res_match:
                    res_str = f" ({res_match.group(1)}x{res_match.group(2)})"
                
                desc = name
                if logical == "0": desc = f"Main Driver ({name})"
                elif logical == "2": desc = f"Passenger ({name})"
                elif logical == "4": desc = f"Rear Left ({name})"
                elif logical == "5": desc = f"Rear Right ({name})"
                
                info_list.append({
                    "id": logical, 
                    "physical_id": physical,
                    "description": f"{desc}{res_str}"
                })

        if not info_list:
            # 如果无法获取display信息，返回None让调用方使用静态fallback
            return None

        display_mapping = new_mapping
        display_info_cache = info_list
        return info_list
    except Exception as e:
        print(f"Error refreshing display mapping: {e}")
        return None

def detect_ss_device(serial: str) -> Optional[str]:
    """Detect if device is SS series (SS4, SS3, etc.) by checking display.id property"""
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
        if 'SS4' in output_upper:
            print(f"[SS_DETECT] ✅✅✅ Detected SS4 device (string match): {serial}")
            return "SS4"
        elif 'SS3' in output_upper:
            print(f"[SS_DETECT] ✅✅✅ Detected SS3 device (string match): {serial}")
            return "SS3"
        elif 'SS2' in output_upper:
            print(f"[SS_DETECT] ✅✅✅ Detected SS2 device (string match): {serial}")
            return "SS2"
        elif 'SS5' in output_upper:
            print(f"[SS_DETECT] ✅✅✅ Detected SS5 device (string match): {serial}")
            return "SS5"
        else:
            print(f"[SS_DETECT] ❌ No SS device pattern found")
            print(f"[SS_DETECT] 💡 If this should be an SS device, check the display.id format")
        
        return None
    except Exception as e:
        print(f"[SS_DETECT] ⚠️ Exception occurred: {e}")
        import traceback
        traceback.print_exc()
        return None

@app.get("/api/devices")
def get_devices():
    global ss4_localhost_mapping
    try:
        devices = []
        for d in adb.device_list():
            model = d.prop.get("ro.product.model", "Unknown")
            
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
    
    # 尝试动态获取设备实际支持的display列表
    res = refresh_display_mapping(target_serial)
    if res:
        # 只显示Display ID，不添加额外描述
        print(f"[DISPLAYS] 从dumpsys获取到 {len(res)} 个display")
        for display in res:
            display_id = display["id"]
            display["description"] = f"Display {display_id}"
        return res
    
    # 如果无法获取，尝试通过screencap探测实际可用的display
    print(f"[DISPLAYS] dumpsys方式失败，尝试探测可用display...")
    available_displays = []
    
    try:
        d = adb.device(serial=target_serial)
        # 探测display 0-5，看哪些可用
        for display_id in range(6):
            try:
                # 尝试快速截图测试display是否存在
                result = subprocess.run(
                    ["adb", "-s", target_serial, "shell", f"screencap -d {display_id} -p"],
                    capture_output=True, 
                    timeout=2,
                    check=False
                )
                # 如果返回数据大于100字节且包含PNG头，说明display存在
                if result.returncode == 0 and len(result.stdout) > 100 and b"\x89PNG" in result.stdout:
                    available_displays.append({
                        "id": str(display_id),
                        "description": f"Display {display_id}"
                    })
                    print(f"[DISPLAYS] ✅ Display {display_id} 可用")
                else:
                    print(f"[DISPLAYS] ❌ Display {display_id} 不可用或无响应")
            except Exception as e:
                print(f"[DISPLAYS] ⚠️ Display {display_id} 探测失败: {e}")
                continue
    except Exception as e:
        print(f"[DISPLAYS] ⚠️ 探测过程出错: {e}")
    
    # 如果探测到了display，返回探测结果
    if available_displays:
        print(f"[DISPLAYS] 探测成功，找到 {len(available_displays)} 个可用display")
        return available_displays
    
    # 最后的fallback：至少返回display 0
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

@app.get("/api/screenshot")
def get_screenshot(display: str = "0"):
    global current_serial, display_mapping
    if not current_serial:
         raise HTTPException(status_code=400, detail="Device not connected")
    
    try:
        print(f"[SCREENSHOT] 📸 请求截图 - Display ID: {display}, Device: {current_serial}")
        
        # Detect device type for special handling
        ss_type = detect_ss_device(current_serial)
        print(f"[SCREENSHOT] 🚗 设备类型: {ss_type}")
        
        # Use physical ID for screencap if available
        phys_id = display_mapping.get(display, display)
        print(f"[SCREENSHOT] 🔄 物理ID映射: {display} -> {phys_id}")
        
        raw_png = None
        last_err = ""
        d = adb.device(serial=current_serial)
        
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

        # 使用adbutils执行命令（更快更稳定）
        for cmd_str in cmd_variations:
            try:
                print(f"[SCREENSHOT] 🔧 尝试命令: {cmd_str}")
                # 注意：不同版本的adbutils对shell()的返回值处理不同
                # 新版本返回bytes，旧版本可能返回str
                res = d.shell(cmd_str)
                # 如果返回的是字符串，转换为bytes
                if isinstance(res, str):
                    res = res.encode('latin1')
                if res and len(res) > 100:
                    print(f"[SCREENSHOT] ✅ 成功！截图大小: {len(res)} bytes")
                    raw_png = res
                    break
                else:
                    print(f"[SCREENSHOT] ❌ 返回数据太小或为空: {len(res) if res else 0} bytes")
            except Exception as e:
                print(f"[SCREENSHOT] ❌ 命令失败: {e}")
                last_err = str(e)
                continue

        # Subprocess fallback（兜底方案）
        if not raw_png:
            print(f"[SCREENSHOT] 🔄 使用subprocess fallback")
            subprocess_variations = []
            
            # 构建subprocess命令列表
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
                result = subprocess.run(cmd, capture_output=True, check=False, timeout=10)
                if result.returncode == 0 and result.stdout and len(result.stdout) > 100:
                    print(f"[SCREENSHOT] ✅ subprocess成功！大小: {len(result.stdout)} bytes")
                    raw_png = result.stdout
                    break
                if result.stderr:
                    last_err = result.stderr.decode(errors='ignore')
                    print(f"[SCREENSHOT] ❌ subprocess错误: {last_err}")

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
        subprocess.run(["adb", "-s", serial, "forward", "tcp:8765", "tcp:8765"], 
                      capture_output=True, timeout=3, check=False)
        
        # 测试连接
        import requests
        response = requests.get("http://localhost:8765/api/status", timeout=2)
        if response.status_code == 200:
            data = response.json()
            if data.get("service") == "running":
                print(f"[Accessibility] ✅ 辅助服务可用")
                return True
    except Exception as e:
        print(f"[Accessibility] ⚠️ 辅助服务不可用: {e}")
    return False

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
        print(f"[Accessibility] ✅ 获取到 {len(nodes)} 个根节点")
        
        # --- 坐标归一化：将“全局坐标”转换为“当前 display 截图坐标系” ---
        # 在多屏/分屏场景下，AccessibilityNodeInfo#getBoundsInScreen 可能返回带 display 偏移的坐标，
        # 而 screencap -d <display> 的截图坐标原点是 (0,0)。
        # 这里用该 display 的窗口 bounds 的最小 left/top 作为 display 的原点偏移，并对疑似“绝对坐标”的节点做减偏移。
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

@app.get("/api/hierarchy")
def get_hierarchy(display: int = 0, force_accessibility: bool = False):
    global current_serial
    global hierarchy_xml_cache
    if not current_serial:
         raise HTTPException(status_code=400, detail="Device not connected")
    
    print(f"[Hierarchy] 📋 开始获取Display {display}的UI树...")
    print(f"[Hierarchy] 用户选择数据源: {'辅助服务' if force_accessibility else 'UIAutomator'}")
    
    # 根据用户选择使用对应的数据源
    if force_accessibility:
        # 用户选择使用辅助服务
        print(f"[Hierarchy] 🔧 使用辅助服务模式")
        target_serial = resolve_accessibility_target_serial(current_serial)
        if target_serial != current_serial:
            print(f"[Hierarchy] ♿ 辅助服务目标设备序列号修正: {current_serial} -> {target_serial}")

        if check_accessibility_service(target_serial):
            xml_from_accessibility = get_hierarchy_from_accessibility(target_serial, display)
            if xml_from_accessibility:
                print(f"[Hierarchy] ✅ 使用辅助服务数据源")
                return {"xml": xml_from_accessibility, "source": "accessibility"}
            else:
                print(f"[Hierarchy] ⚠️ 辅助服务获取失败，fallback到UIAutomator")
        else:
            print(f"[Hierarchy] ⚠️ 辅助服务不可用，fallback到UIAutomator")
    
    # 步骤1：优先使用UIAutomator
    # 步骤1：使用UIAutomator获取hierarchy
    print(f"[Hierarchy] 🔍 使用UIAutomator获取...")
    uiautomator_xml = None
    original_xml_for_check = None  # 用于完整性检查的原始XML（转换前）
    try:
        import xml.etree.ElementTree as ET
        d = adb.device(serial=current_serial)
        dump_path = f"/sdcard/uidump_all.xml"
        
        # Clear previous dump
        d.shell(f"rm -f {dump_path}")
        
        # 获取所有display的完整hierarchy（使用--windows获取多窗口多display数据）
        # 某些车机上 uiautomator dump 会偶发报：ERROR: could not get idle state.
        # 这里做重试，并优先使用 --compressed 降低数据量。
        print(f"[Hierarchy] 🔍 获取所有display的完整层级数据...")
        dump_err = ""
        for attempt in range(3):
            try:
                cmd = f"uiautomator dump --compressed --windows {dump_path}"
                dump_err = d.shell(cmd)
                print(f"[Hierarchy] uiautomator dump输出(attempt {attempt+1}/3): {dump_err}")
                xml_content = d.shell(f"cat {dump_path}")
                if xml_content and "<?xml" in xml_content:
                    break
            except Exception as _e:
                dump_err = str(_e)
            import time
            time.sleep(0.3)

        # 读取dump的内容（若上面已经读取并成功，会走到这里继续使用）
        if 'xml_content' not in locals():
            xml_content = d.shell(f"cat {dump_path}")
        
        if not xml_content or "<?xml" not in xml_content:
            print(f"[Hierarchy] --windows方式失败,尝试指定display...")
            # Fallback: 尝试指定display
            d.shell(f"rm -f {dump_path}")
            # 也做一次重试
            for attempt in range(3):
                cmd = f"uiautomator dump --compressed --display {display} {dump_path}"
                err = d.shell(cmd)
                print(f"[Hierarchy] uiautomator dump(display)输出(attempt {attempt+1}/3): {err}")
                xml_content = d.shell(f"cat {dump_path}")
                if xml_content and "<?xml" in xml_content:
                    break
                import time
                time.sleep(0.3)
            
        if not xml_content or "<?xml" not in xml_content:
            raise Exception(f"Failed to dump hierarchy for display {display}")

        # 清理XML内容
        start = xml_content.find("<?xml")
        end = xml_content.rfind(">")
        if start != -1 and end != -1:
            xml_content = xml_content[start:end+1]
        
        print(f"[Hierarchy] 成功获取UI层级,XML长度: {len(xml_content)}")
        
        # 保存原始XML用于完整性检查（在坐标转换之前）
        original_xml_for_check = xml_content
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
                                            # 相对坐标，需要转换
                                            src_w = max(1, src_union['x2'] - src_union['x1'])
                                            src_h = max(1, src_union['y2'] - src_union['y1'])
                                            dst_w = max(1, dst_bounds['x2'] - dst_bounds['x1'])
                                            dst_h = max(1, dst_bounds['y2'] - dst_bounds['y1'])
                                            scale_x = dst_w / src_w
                                            scale_y = dst_h / src_h
                                            offset_x = dst_bounds['x1'] - src_union['x1'] * scale_x
                                            offset_y = dst_bounds['y1'] - src_union['y1'] * scale_y
                                            print(f"[Hierarchy]          ✅ 应用转换: scale=({scale_x:.4f},{scale_y:.4f}), offset=({offset_x:.1f},{offset_y:.1f})")
                                        else:
                                            # 已经是绝对坐标，不转换
                                            scale_x = 1.0
                                            scale_y = 1.0
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
                
                # 调试：在合并后的hierarchy中查找"关闭"节点
                def find_nodes_with_text(element, text_pattern):
                    results = []
                    if element.tag == 'node':
                        node_text = element.get('text', '')
                        content_desc = element.get('content-desc', '')
                        if text_pattern in node_text or text_pattern in content_desc:
                            results.append({
                                'text': node_text,
                                'content-desc': content_desc,
                                'bounds': element.get('bounds'),
                                'class': element.get('class'),
                                'resource-id': element.get('resource-id'),
                                'clickable': element.get('clickable')
                            })
                    for child in element:
                        results.extend(find_nodes_with_text(child, text_pattern))
                    return results
                
                close_nodes = find_nodes_with_text(merged_hierarchy, '关闭')
                if close_nodes:
                    print(f"[Hierarchy] 📍 合并后找到 {len(close_nodes)} 个'关闭'节点:")
                    for idx, node in enumerate(close_nodes):
                        print(f"[Hierarchy]    [{idx}] {node['class']}")
                        print(f"[Hierarchy]        Bounds: {node['bounds']}")
                        print(f"[Hierarchy]        Text: '{node['text']}'")
                        print(f"[Hierarchy]        Content-desc: '{node['content-desc']}'")
                        print(f"[Hierarchy]        Resource-ID: {node['resource-id']}")
                        print(f"[Hierarchy]        Clickable: {node['clickable']}")
                
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



@app.post("/api/accessibility/enable")
def enable_accessibility_service():
    """启用辅助服务"""
    global current_serial, ss4_localhost_mapping
    if not current_serial:
        raise HTTPException(status_code=400, detail="Device not connected")
    
    # 对于SS4设备（localhost:5559），使用原始物理设备序列号操作辅助服务
    target_serial = current_serial
    if current_serial == "localhost:5559" and current_serial in ss4_localhost_mapping:
        target_serial = ss4_localhost_mapping[current_serial]["original_serial"]
        print(f"[Accessibility] 🔧 SS4设备，使用原始序列号操作: {target_serial} (而非 {current_serial})")
    
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

@app.post("/api/accessibility/disable")
def disable_accessibility_service():
    """禁用辅助服务，恢复原有服务（如语音服务）"""
    global current_serial, ss4_localhost_mapping
    if not current_serial:
        raise HTTPException(status_code=400, detail="Device not connected")
    
    # 对于SS4设备（localhost:5559），使用原始物理设备序列号操作辅助服务
    target_serial = current_serial
    if current_serial == "localhost:5559" and current_serial in ss4_localhost_mapping:
        target_serial = ss4_localhost_mapping[current_serial]["original_serial"]
        print(f"[Accessibility] 🛑 SS4设备，使用原始序列号操作: {target_serial} (而非 {current_serial})")
    
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

@app.get("/api/accessibility/status")
def get_accessibility_status():
    """获取辅助服务状态"""
    global current_serial, ss4_localhost_mapping
    if not current_serial:
        raise HTTPException(status_code=400, detail="Device not connected")
    
    # 对于SS4设备（localhost:5559），使用原始物理设备序列号操作辅助服务
    target_serial = current_serial
    if current_serial == "localhost:5559" and current_serial in ss4_localhost_mapping:
        target_serial = ss4_localhost_mapping[current_serial]["original_serial"]
        print(f"[Accessibility] 📊 SS4设备，使用原始序列号查询状态: {target_serial} (而非 {current_serial})")
    
    try:
        print(f"[Accessibility] 📊 查询辅助服务状态...")
        print(f"[Accessibility] 📱 目标设备: {target_serial}")
        
        # 检查是否启用
        result = subprocess.run(
            ["adb", "-s", target_serial, "shell", "settings", "get", "secure", "enabled_accessibility_services"],
            capture_output=True, text=True, timeout=3
        )
        
        enabled_services = result.stdout.strip()
        is_enabled = "com.carui.accessibility" in enabled_services
        
        # 检查是否运行中（使用target_serial）
        is_running = check_accessibility_service(target_serial)
        
        return {
            "enabled": is_enabled,
            "running": is_running,
            "all_services": enabled_services
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/restart-server")
def restart_server():
    """重启Python服务器进程"""
    global current_serial
    try:
        import signal
        
        # 获取当前进程的PID
        current_pid = os.getpid()
        print(f"[RESTART] 🔄 准备重启服务器，当前PID: {current_pid}")
        
        # 在重启前先禁用辅助服务，恢复设备原有服务
        if current_serial:
            try:
                print(f"[RESTART] 🛑 重启前禁用辅助服务...")
                result = subprocess.run(
                    ["adb", "-s", current_serial, "shell", "settings", "get", "secure", "enabled_accessibility_services"],
                    capture_output=True, text=True, timeout=3
                )
                
                current_services = result.stdout.strip()
                
                if "com.carui.accessibility" in current_services:
                    # 移除我们的辅助服务
                    services_list = current_services.split(':')
                    services_list = [s for s in services_list if 'com.carui.accessibility' not in s]
                    new_services = ':'.join(services_list)
                    
                    subprocess.run(
                        ["adb", "-s", current_serial, "shell", "settings", "put", "secure", 
                         "enabled_accessibility_services", new_services],
                        capture_output=True, text=True, timeout=3
                    )
                    
                    print(f"[RESTART] ✅ 已禁用辅助服务，恢复原有服务")
                else:
                    print(f"[RESTART] ℹ️ 辅助服务未启用，无需禁用")
            except Exception as e:
                print(f"[RESTART] ⚠️ 禁用辅助服务失败: {e}，继续重启")
        
        # 返回成功响应后，延迟终止进程（让响应能够发送出去）
        def delayed_restart():
            import time
            time.sleep(0.5)  # 等待响应发送
            print(f"[RESTART] 💀 终止当前进程...")
            os.kill(current_pid, signal.SIGTERM)
        
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

if __name__ == "__main__":
    # Find available port
    try:
        port = find_available_port(start_port=18888, max_attempts=10)
        print(f"🚀 Starting server on port {port}")
        
        # Write port to file for plugin to read
        port_file = os.path.join(os.path.dirname(__file__), "server_port.txt")
        with open(port_file, 'w') as f:
            f.write(str(port))
        print(f"📝 Port number saved to {port_file}")
        
        # Start server
        uvicorn.run(app, host="0.0.0.0", port=port)
    except Exception as e:
        print(f"❌ Failed to start server: {e}")
        sys.exit(1)
