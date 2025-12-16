import PyInstaller.__main__
import os
import shutil
import sys

# Define base paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DIST_DIR = os.path.join(BASE_DIR, "dist")
BUILD_DIR = os.path.join(BASE_DIR, "build")
DEPENDENCIES_DIR = os.path.join(BASE_DIR, ".dependencies")

# Clean previous builds
if os.path.exists(DIST_DIR):
    shutil.rmtree(DIST_DIR)
if os.path.exists(BUILD_DIR):
    shutil.rmtree(BUILD_DIR)

print(f"Building Fedit Native 2.0 from {BASE_DIR}")
print(f"Dependencies at {DEPENDENCIES_DIR}")

# Build Arguments
args = [
    'native_app.py',             # Your main script
    '--name=Fedit2',              # Executable name
    '--onefile',                 # Single ONEFILE executable
    '--clean',                   # Clean cache
    '--windowed',                # No terminal window
    '--collect-all=dearpygui',   # Collect DPG assets
    '--collect-all=dearpygui',   # Collect DPG assets
    '--collect-all=pysdl2_dll',  # Collect SDL2 dll wrapper if any
    '--collect-submodules=server', # Ensure server package is collected
    '--paths=.',                 # Add root to search path
    
    # Exclude unnecessary web/server stuff to save size
    '--exclude-module=uvicorn',
    '--exclude-module=fastapi',
    '--exclude-module=starlette',
    '--exclude-module=tkinter',
    '--hidden-import=server.ffb_engine', # Explicitly force import
]

# Robust Dependency Handling
sdl2_path = None

# 1. Check local .dependencies cache (used in our agent env)
local_sdl2 = os.path.join(DEPENDENCIES_DIR, "sdl2dll", "dll", "SDL2.dll")
if os.path.exists(DEPENDENCIES_DIR):
    print(f"Using local dependencies at {DEPENDENCIES_DIR}")
    args.append(f'--paths={DEPENDENCIES_DIR}')
    if os.path.exists(local_sdl2):
        sdl2_path = local_sdl2

# 2. If not found locally, check site-packages (standard pip install)
if not sdl2_path:
    try:
        import os
        # Try to find within installed pysdl2-dll package
        # Usually in Lib/site-packages/sdl2dll/dll/SDL2.dll or similar
        # We can try to guess based on standard install locations or import
        import sdl2dll # type: ignore
        package_dir = os.path.dirname(os.path.abspath(sdl2dll.__file__))
        proposed = os.path.join(package_dir, "dll", "SDL2.dll")
        if os.path.exists(proposed):
             sdl2_path = proposed
             print(f"Found SDL2 via package import: {sdl2_path}")
    except ImportError:
        pass

# 3. Last resort fallback (user might be running this script from a venv)
if not sdl2_path:
    # Look in the venv site-packages manually if needed, or just warn
    pass

if sdl2_path and os.path.exists(sdl2_path):
    print(f"Bundling SDL2.dll from: {sdl2_path}")
    args.append(f'--add-binary={sdl2_path};.')
else:
    print("WARNING: Could not find SDL2.dll to bundle! Application may fail.")

# Force include the 'server' package dir
server_dir = os.path.join(BASE_DIR, "server")
if os.path.exists(server_dir):
     print(f"Bundling server package from: {server_dir}")
     args.append(f'--add-data={server_dir};server')

print(f"Running PyInstaller with: {args}")

PyInstaller.__main__.run(args)

# Post-Build Check
if os.path.exists("dist/Fedit.exe"):
    print("Build Complete: dist/Fedit.exe")
    # If using OneFile, we don't need to copy SDL2.dll next to it usually, 
    # BUT PySDL2 sometimes demands it in CWD. Let's be safe and copy it if we found it.
    if sdl2_path:
         shutil.copy(sdl2_path, os.path.join(DIST_DIR, "SDL2.dll"))
         print("Copied SDL2.dll to dist/ for safety.")
else:
    print("Build Failed.")
