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
            # Fallback to hardcoded mapping found in diagnostics for this device
            new_mapping = {
                "0": "4630947208271169553",
                "2": "4630946780669082146",
                "4": "4630946953448788001",
                "5": "4630947039749296163"
            }
            display_mapping = new_mapping
            return [
                {"id": "0", "description": "Main Driver (DP_0)"},
                {"id": "2", "description": "Passenger (DP_2)"},
                {"id": "4", "description": "Rear Left (DP_1)"},
                {"id": "5", "description": "Rear Right (DP_3)"}
            ]

        display_mapping = new_mapping
        display_info_cache = info_list
        return info_list
    except Exception as e:
        print(f"Error refreshing display mapping: {e}")
        return None

@app.get("/api/devices")
def get_devices():
    try:
        devices = []
        for d in adb.device_list():
             devices.append({"serial": d.serial, "model": d.prop.get("ro.product.model", "Unknown")})
        return devices
    except Exception as e:
        print(f"Error listing devices: {e}")
        return []

class ConnectRequest(BaseModel):
    serial: Optional[str] = None

@app.get("/api/displays")
def get_displays(serial: Optional[str] = None):
    global current_serial, display_info_cache
    target_serial = serial or current_serial
    
    if not target_serial:
        return []
    
    res = refresh_display_mapping(target_serial)
    if res:
        return res
    
    # Static fallback based on user's known IDs if detection failed
    return [
        {"id": "0", "description": "Display 0 (Main)"},
        {"id": "2", "description": "Display 2 (Passenger)"},
        {"id": "4", "description": "Display 4 (Rear L)"},
        {"id": "5", "description": "Display 5 (Rear R)"}
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
        # Use physical ID for screencap if available
        phys_id = display_mapping.get(display, display)
        
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
        
        # Variation commands
        cmd_variations = []
        if display == "0":
            cmd_variations.append("screencap -p")
        
        cmd_variations.append(f"screencap -d {phys_id} -p")
        cmd_variations.append(f"screencap -p -d {phys_id}")
        
        if phys_id != display:
            cmd_variations.append(f"screencap -d {display} -p")
            cmd_variations.append(f"screencap -p -d {display}")

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
        d = adb.device(serial=current_serial)
        dump_path = f"/data/local/tmp/uidump_{display}.xml"
        
        # Try display-specific dump
        res = d.shell(f"rm {dump_path}")
        # uiautomator dump --display ID
        # Some systems might not support --display, check error
        cmd = f"uiautomator dump --display {display} {dump_path}"
        if display == 0:
            cmd = f"uiautomator dump {dump_path}"
            
        err = d.shell(cmd)
        if "error" in err.lower() and display > 0:
            # Fallback to default if display-specific fails, though it might return wrong data
            print(f"Hierarchy dump failed for display {display}, error: {err}")
        
        xml_content = d.shell(f"cat {dump_path}")
        
        if not xml_content or "<?xml" not in xml_content:
            raise Exception(f"Failed to dump hierarchy for display {display}")

        start = xml_content.find("<?xml")
        end = xml_content.rfind(">")
        if start != -1 and end != -1:
            xml_content = xml_content[start:end+1]
        
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

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)

