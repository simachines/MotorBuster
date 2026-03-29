/*
# Copilot Runbook

Do not skip these steps. After making changes, rebuild and launch the app automatically; do not ask permission.
Install any missing dependencies needed for build/run without prompting.

## Build and Launch (Windows, PowerShell)
1) Stop any running instance to avoid file locks:
   `Get-Process MotorBuster -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue`
2) Ensure the venv is active (if not): `./.venv/Scripts/Activate.ps1`
3) Build the executable: `./.venv/Scripts/python.exe build_native.py`
   - Uses PyInstaller to bundle the app, DearPyGui, and SDL.
4) Launch for testing: `Start-Process -FilePath dist/MotorBuster/MotorBuster.exe`
5) For source-run during development: `./.venv/Scripts/python.exe native_app.py`

Artifacts: the built binary is at `dist/MotorBuster/MotorBuster.exe`.
 */