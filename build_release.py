import PyInstaller.__main__
import os
import shutil
import sys

# Make sure we are in the root of the project
base_path = os.path.dirname(os.path.abspath(__file__))
server_path = os.path.join(base_path, "server")
client_dist = os.path.join(base_path, "client", "dist")
dep_path = os.path.join(base_path, ".dependencies")

print(f"Building MotorBuster 2.0 from {base_path}")
print(f"Dependencies at {dep_path}")

# 1. Clean previous build
if os.path.exists("dist"):
    shutil.rmtree("dist")
if os.path.exists("build"):
    shutil.rmtree("build")

# 2. Run PyInstaller
sep = ";" if os.name == 'nt' else ":"

args = [
    os.path.join(server_path, "main.py"),
    '--name=MotorBuster',
    '--onedir',
    '--clean',
    f'--paths={dep_path}',  # Help PyInstaller find packages
    f'--add-data={client_dist}{sep}client/dist',
    '--hidden-import=uvicorn.loops.auto',
    '--hidden-import=uvicorn.protocols.http.auto',
    '--hidden-import=websockets.legacy',
    '--hidden-import=websockets.legacy.server',
    '--hidden-import=websockets.legacy.client',
    # Explicitly add pysdl2-dll path if needed, but strict collecting might work
    '--collect-all=pysdl2_dll', 
]

print(f"Running PyInstaller with: {args}")

PyInstaller.__main__.run(args)

print("Build Complete. Executable should be in dist/MotorBuster/MotorBuster.exe")
