from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import json
import asyncio
import webbrowser
import os
import sys
from pathlib import Path

# Fix DLL path for PyInstaller
if getattr(sys, 'frozen', False):
    base_dir = Path(sys.executable).parent
    os.environ["PYSDL2_DLL_PATH"] = str(base_dir)
else:
    base_dir = Path(__file__).parent

from ffb_engine import engine

app = FastAPI()

# Allow CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Determine static path
if getattr(sys, 'frozen', False):
    bundle_dir = Path(sys._MEIPASS) if hasattr(sys, '_MEIPASS') else base_dir
    # In strict onedir, we moved it to client/dist at root of exe (sys.executable parent)
    # So it is at base_dir / "client" / "dist"
    static_dir = base_dir / "client" / "dist"
else:
    static_dir = base_dir.parent / "client" / "dist"

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    
    try:
        try:
             engine.init_sdl()
        except:
             pass 

        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            cmd = msg.get("cmd")
            payload = msg.get("payload", {})

            if cmd == "scan_devices":
                devices = engine.list_devices()
                await websocket.send_json({"type": "devices", "payload": [vars(d) for d in devices]})

            elif cmd == "connect":
                idx = payload.get("index")
                success = engine.connect_device(idx)
                if success:
                    await websocket.send_json({"type": "log", "payload": f"Connected to device {idx}"})
                else:
                    await websocket.send_json({"type": "log", "payload": f"Failed to connect to {idx}"})

            elif cmd == "play_test":
                effect_type = payload.get("type")
                if effect_type == "square":
                    engine.play_constant(level=10000, length=2000)
                    await websocket.send_json({"type": "log", "payload": "Playing Square Wave"})
                elif effect_type == "sweep":
                    engine.play_constant(level=5000, length=2000)
                    await websocket.send_json({"type": "log", "payload": "Playing Sweep"})

            elif cmd == "stop_all":
                engine.stop_effect()
                await websocket.send_json({"type": "log", "payload": "Stopped all effects"})

    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"Error: {e}")

# Add Cache Control Middleware
@app.middleware("http")
async def add_cache_control_header(request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

# Mount Static Files
if static_dir.exists():
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
else:
    print(f"Warning: Static files not found at {static_dir}")

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    print("Client Connected (V2 PRO)")
    
    try:
        try:
             engine.init_sdl()
        except:
             pass 

        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            cmd = msg.get("cmd")
            payload = msg.get("payload", {})

            if cmd == "scan_devices":
                devices = engine.list_devices()
                # Always add a simulated device for testing UI
                if not devices:
                    devices.append(DeviceInfo(index=999, name="Simulated Wheel (Test Mode)"))
                
                await websocket.send_json({"type": "devices", "payload": [vars(d) for d in devices]})

            elif cmd == "connect":
                idx = payload.get("index")
                if idx == 999:
                    # Connect to simulated device
                    await websocket.send_json({"type": "log", "payload": f"Connected to Simulated Device"})
                else:
                    success = engine.connect_device(idx)
                    if success:
                        await websocket.send_json({"type": "log", "payload": f"Connected to device {idx}"})
                    else:
                        await websocket.send_json({"type": "log", "payload": f"Failed to connect to {idx}"})

            elif cmd == "play_test":
                effect_type = payload.get("type")
                if effect_type == "square":
                    engine.play_constant(level=10000, length=2000)
                    await websocket.send_json({"type": "log", "payload": "Playing Square Wave"})
                elif effect_type == "sweep":
                    engine.play_constant(level=5000, length=2000)
                    await websocket.send_json({"type": "log", "payload": "Playing Sweep"})

            elif cmd == "stop_all":
                engine.stop_effect()
                await websocket.send_json({"type": "log", "payload": "Stopped all effects"})

    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"Error: {e}")

def start():
    print("-----------------------------------------")
    print("   LAUNCHING FEDIT V2.0 (PRO UI)   ")
    print("-----------------------------------------")
    # Only open browser if we are serving the app
    if static_dir.exists():
        # Add random query param to bust browser cache
        import time
        webbrowser.open(f"http://localhost:8000?t={int(time.time())}")
    
    uvicorn.run(app, host="0.0.0.0", port=8000)
