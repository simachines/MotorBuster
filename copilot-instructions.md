DO NOT SKIP INSTRUCTIONS

CLOSE AND THEN BUILD and LUNCH the software automatically so I can test EACH TIME you make a change.
Dont ask me if im good with it, instead just do it and open the software so I can test and then I will give you feedback what to change.
Install any dependencies needed for building and running the software without asking me.

Build Instructions
To build a standalone .exe version of Fedit 2.0:

bash
python build_native.py
This script uses PyInstaller to bundle the application, dearpygui, and the necessary SDL2.dll into a single executable.
Locate Output: The finished Fedit2.exe will be located in the dist/ folder.
Note: If you are running from source for development, you can simply run python native_app.py.