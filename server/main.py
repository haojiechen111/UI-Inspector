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
    try:
        devices = []
        for d in adb.device_list():
            model = d.prop.get("ro.product.model", "Unknown")
            ss_type = detect_ss_device(d.serial)
            
            device_info = {
                "serial": d.serial,
                "model": model,
                "ss_type": ss_type,  # Will be "SS4", "SS3", etc. or None
                "needs_init": ss_type == "SS4"  # Only SS4 needs init, not SS2/SS3
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
    global current_serial, display_info_cache
    target_serial = serial or current_serial
    
    if not target_serial:
        return []
    
    # Detect device type for custom screen names
    ss_type = detect_ss_device(target_serial)
    print(f"[DISPLAYS] Device type: {ss_type}")
    
    res = refresh_display_mapping(target_serial)
    if res:
        # Customize screen names based on device type
        for display in res:
            display_id = display["id"]
            if ss_type == "SS4":
                # SS4: 中控屏，后排空调屏，hud屏，后排屏
                if display_id == "0":
                    display["description"] = "中控屏"
                elif display_id == "2":
                    display["description"] = "后排空调屏"
                elif display_id == "4":
                    display["description"] = "hud屏"
                elif display_id == "5":
                    display["description"] = "后排屏"
            elif ss_type in ["SS2", "SS3"] or ss_type is None:
                # SS2/SS3/其他: 中控屏，副驾屏，后排屏
                if display_id == "0":
                    display["description"] = "中控屏"
                elif display_id == "2":
                    display["description"] = "副驾屏"
                elif display_id == "4":
                    display["description"] = "后排屏"
        return res
    
    # Static fallback based on device type
    if ss_type == "SS4":
        return [
            {"id": "0", "description": "中控屏"},
            {"id": "2", "description": "后排空调屏"},
            {"id": "4", "description": "hud屏"},
            {"id": "5", "description": "后排屏"}
        ]
    else:
        # SS2/SS3/其他默认配置
        return [
            {"id": "0", "description": "中控屏"},
            {"id": "2", "description": "副驾屏"},
            {"id": "4", "description": "后排屏"}
        ]

@app.post("/api/connect")
def connect_device(req: ConnectRequest):
    global current_serial
    try:
        if req.serial:
            current_serial = req.serial
        else:
            devices = adb.device_list()
            if not devices:
                raise HTTPException(status_code=404, detail="No devices found")
            current_serial = devices[0].serial
        
        d = adb.device(serial=current_serial)
        model = d.prop.get("ro.product.model", "Unknown")
        
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
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/screenshot")
def get_screenshot(display: str = "0"):
    global current_serial, display_mapping
    if not current_serial:
         raise HTTPException(status_code=400, detail="Device not connected")
    
    try:
        print(f"[SCREENSHOT] 📸 请求截图 - Display ID: {display}, Device: {current_serial}")
        
        # Use physical ID for screencap if available
        phys_id = display_mapping.get(display, display)
        print(f"[SCREENSHOT] 🔄 物理ID映射: {display} -> {phys_id}")
        
        variations = []
        # Fallback 1: screencap -p (most compatible for display 0)
        if display == "0":
            variations.append(["adb", "-s", current_serial, "shell", "screencap", "-p"])
            
        # Fallback 2: physical ID
        variations.append(["adb", "-s", current_serial, "shell", "screencap", "-d", phys_id, "-p"])
        variations.append(["adb", "-s", current_serial, "shell", "screencap", "-p", "-d", phys_id])
        
        # Fallback 3: logical ID
        if phys_id != display:
            variations.append(["adb", "-s", current_serial, "shell", "screencap", "-d", display, "-p"])
            variations.append(["adb", "-s", current_serial, "shell", "screencap", "-p", "-d", display])

        raw_png = None
        last_err = ""
        d = adb.device(serial=current_serial)
        
        # Variation commands - 优化顺序，优先使用logical ID
        cmd_variations = []
        
        # 对于非0 display，优先使用logical ID（因为某些设备physical ID映射可能不准确）
        if display != "0":
            print(f"[SCREENSHOT] 🎯 非主屏，优先尝试logical ID")
            cmd_variations.append(f"screencap -d {display} -p")
            cmd_variations.append(f"screencap -p -d {display}")
        
        # 然后尝试display 0的简化命令
        if display == "0":
            cmd_variations.append("screencap -p")
        
        # 最后尝试physical ID
        if phys_id != display:
            cmd_variations.append(f"screencap -d {phys_id} -p")
            cmd_variations.append(f"screencap -p -d {phys_id}")

        for cmd_str in cmd_variations:
            try:
                # Using d.shell is faster than subprocess.run because it reuses the connection
                res = d.shell(cmd_str, decode=False)
                if res and len(res) > 100:
                    raw_png = res
                    break
            except Exception as e:
                last_err = str(e)
                continue

        # Last resort fallback to subprocess for tough environments
        if not raw_png:
             # Try DP_X style for some specific car systems if and only if display > 0
             # But here we just try a standard subprocess call as final fallback
             for cmd in variations:
                 result = subprocess.run(cmd, capture_output=True, check=False)
                 if result.returncode == 0 and result.stdout and len(result.stdout) > 100:
                     raw_png = result.stdout
                     break
                 if result.stderr:
                     last_err = result.stderr.decode(errors='ignore')

        if not raw_png:
             raise Exception(f"Failed to get screenshot for display {display} (Physical: {phys_id}). Last error: {last_err}")
        
        png_header = b"\x89PNG"
        start_idx = raw_png.find(png_header)
        if start_idx != -1:
            raw_png = raw_png[start_idx:]
        else:
            raise Exception("Invalid screenshot format: No PNG header found")

        return StreamingResponse(io.BytesIO(raw_png), media_type="image/png")
    except Exception as e:
        print(f"Screenshot error for display {display}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/hierarchy")
def get_hierarchy(display: int = 0):
    global current_serial
    if not current_serial:
         raise HTTPException(status_code=400, detail="Device not connected")
    
    try:
        import xml.etree.ElementTree as ET
        d = adb.device(serial=current_serial)
        dump_path = f"/sdcard/uidump_{display}.xml"
        
        # Clear previous dump
        d.shell(f"rm -f {dump_path}")
        
        # 方法1: 使用--windows参数获取所有窗口和层级(包括系统UI)
        print(f"[Hierarchy] 尝试获取所有层级(包括系统UI)...")
        cmd = f"uiautomator dump --windows {dump_path}"
        err = d.shell(cmd)
        print(f"[Hierarchy] uiautomator dump --windows 输出: {err}")
        
        # 读取dump的内容
        xml_content = d.shell(f"cat {dump_path}")
        
        if not xml_content or "<?xml" not in xml_content:
            print(f"[Hierarchy] 默认dump失败,尝试指定display...")
            # Fallback: 尝试指定display
            d.shell(f"rm -f {dump_path}")
            cmd = f"uiautomator dump --display {display} {dump_path}"
            err = d.shell(cmd)
            xml_content = d.shell(f"cat {dump_path}")
            
        if not xml_content or "<?xml" not in xml_content:
            raise Exception(f"Failed to dump hierarchy for display {display}")

        # 清理XML内容
        start = xml_content.find("<?xml")
        end = xml_content.rfind(">")
        if start != -1 and end != -1:
            xml_content = xml_content[start:end+1]
        
        print(f"[Hierarchy] 成功获取UI层级,XML长度: {len(xml_content)}")
        
        # 处理多窗口XML格式：将所有窗口合并到单个hierarchy中
        try:
            root = ET.fromstring(xml_content)
            
            # 检查是否是多窗口格式 (<displays>)
            if root.tag == 'displays':
                print(f"[Hierarchy] 检测到多窗口格式，开始合并...")
                # 创建一个新的hierarchy根节点
                merged_hierarchy = ET.Element('hierarchy')
                merged_hierarchy.set('rotation', '0')
                
                window_count = 0
                # 遍历所有display下的所有window
                for display_elem in root.findall('.//display'):
                    display_id = display_elem.get('id', 'unknown')
                    for window_elem in display_elem.findall('window'):
                        window_count += 1
                        window_title = window_elem.get('title', '')
                        window_bounds = window_elem.get('bounds', '')
                        window_type = window_elem.get('type', '')
                        
                        # 获取window下的hierarchy节点
                        hierarchy_elem = window_elem.find('hierarchy')
                        if hierarchy_elem is not None:
                            # 将hierarchy下的所有node添加到merged_hierarchy
                            for node in hierarchy_elem.findall('node'):
                                # 为每个顶层node添加window信息作为注释属性
                                node_copy = ET.fromstring(ET.tostring(node))
                                merged_hierarchy.append(node_copy)
                
                print(f"[Hierarchy] 合并了 {window_count} 个窗口的节点")
                xml_content = ET.tostring(merged_hierarchy, encoding='unicode')
                xml_content = '<?xml version="1.0" encoding="UTF-8"?>' + xml_content
                print(f"[Hierarchy] 合并后XML长度: {len(xml_content)}")
            else:
                print(f"[Hierarchy] 单窗口格式，无需合并")
        except Exception as parse_error:
            print(f"[Hierarchy] XML解析/合并出错: {parse_error}")
            # 如果解析失败，返回原始XML
            pass
        
        return {"xml": xml_content}
    except Exception as e:
        print(f"Hierarchy error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

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
