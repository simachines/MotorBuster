from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
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
else:
    base_dir = Path(__file__).parent

# Hint PySDL3 where to find bundled binaries (works for dev and PyInstaller)
os.environ.setdefault("SDL_BINARY_PATH", str(base_dir))

from .ffb_engine import DeviceInfo, engine

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

# Add Cache Control Middleware
@app.middleware("http")
async def add_cache_control_header(request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

# WebSocket endpoint
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

            elif cmd == "play_clip":
                clip = payload.get("clip") or payload
                effect_id = engine.play_descriptor(clip)
                if effect_id != -1:
                    await websocket.send_json({"type": "log", "payload": f"Playing effect {clip.get('type', 'unknown')} (id={effect_id})"})
                else:
                    await websocket.send_json({"type": "log", "payload": f"Failed to play effect: {clip.get('type', 'unknown')}"})

            elif cmd == "stop_all":
                engine.stop_effect()
                await websocket.send_json({"type": "log", "payload": "Stopped all effects"})

    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"Error: {e}")

# Static file serving (no root mount to avoid websocket interception)
if static_dir.exists():
    assets_dir = static_dir / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    @app.get("/", include_in_schema=False)
    async def serve_index():
        return FileResponse(static_dir / "index.html")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str):
        # Fallback to SPA index for any unknown route (except websockets)
        return FileResponse(static_dir / "index.html")
else:
    print(f"Warning: Static files not found at {static_dir}")

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


if __name__ == "__main__":
    start()
