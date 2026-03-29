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

print(f"Building MotorBuster Native 2.0 from {BASE_DIR}")
print(f"Dependencies at {DEPENDENCIES_DIR}")

icon_path = os.path.join(BASE_DIR, "assets", "icon.ico")

# Build Arguments
args = [
    os.path.join(BASE_DIR, 'native_app.py'), # Your main script
    '--name=MotorBuster',              # Executable name
    f'--icon={icon_path}', # Exe Icon
    f'--add-data={os.path.join(BASE_DIR, "assets")};assets',  # Bundle assets dir
    '--onedir',                  # Directory with exe and dependencies
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
    f'--distpath={DIST_DIR}',    # Force dist location
    f'--workpath={BUILD_DIR}',   # Force build temp location
    f'--specpath={BASE_DIR}',    # Force spec file location
]

# Bundle SDL3 DLLs from server package
sdl3_dll = os.path.join(BASE_DIR, "server", "SDL3.dll")
if os.path.exists(sdl3_dll):
    print(f"Bundling SDL3.dll from: {sdl3_dll}")
    args.append(f'--add-binary={sdl3_dll};.')
else:
    print("WARNING: Could not find SDL3.dll in server/ directory!")

# Bundle other SDL3 libs if present (Image, Mixer, etc)
for fname in os.listdir(os.path.join(BASE_DIR, "server")):
    if fname.startswith("SDL3") and fname.endswith(".dll") and fname != "SDL3.dll":
        fpath = os.path.join(BASE_DIR, "server", fname)
        print(f"Bundling {fname}...")
        args.append(f'--add-binary={fpath};.')

# Force include the 'server' package dir
server_dir = os.path.join(BASE_DIR, "server")
if os.path.exists(server_dir):
     print(f"Bundling server package from: {server_dir}")
     args.append(f'--add-data={server_dir};server')

print(f"Running PyInstaller with: {args}")

PyInstaller.__main__.run(args)

# Post-Build Check
primary_exe_path = os.path.join(DIST_DIR, "MotorBuster", "MotorBuster.exe")

if os.path.exists(primary_exe_path):
    print(f"Build Complete: {primary_exe_path}")
else:
    print("Build Failed.")
