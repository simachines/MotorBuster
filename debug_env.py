import sys
print("Starting Debug Script")
try:
    import PyInstaller
    print("PyInstaller imported successfully")
except Exception as e:
    print(f"Failed to import PyInstaller: {e}")

try:
    import dearpygui.dearpygui as dpg
    print("DearPyGui imported successfully")
except Exception as e:
    print(f"Failed to import DearPyGui: {e}")

try:
    import sdl2
    print("SDL2 imported successfully")
except Exception as e:
    print(f"Failed to import SDL2: {e}")

try:
    import server.ffb_engine
    print("server.ffb_engine imported successfully")
except Exception as e:
    print(f"Failed to import server.ffb_engine: {e}")
