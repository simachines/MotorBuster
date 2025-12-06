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
    '--name=Fedit',              # Executable name
    '--onefile',                 # Single ONEFILE executable
    '--clean',                   # Clean cache
    '--windowed',                # No terminal window
    f'--paths={DEPENDENCIES_DIR}', # Look for deps here
    '--collect-all=dearpygui',   # Collect DPG assets
    '--collect-all=pysdl2_dll',  # Collect SDL2 dll wrapper if any
    # Explicitly add SDL2.dll to root from our local cache
    f'--add-binary={os.path.join(DEPENDENCIES_DIR, "sdl2dll", "dll", "SDL2.dll")};.',
    
    # Exclude unnecessary web/server stuff to save size
    '--exclude-module=uvicorn',
    '--exclude-module=fastapi',
    '--exclude-module=starlette',
    '--exclude-module=tkinter',
]

print(f"Running PyInstaller with: {args}")

PyInstaller.__main__.run(args)

print("Build Complete.")
