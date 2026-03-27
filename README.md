# Fedit 2.0 - Haptic DAW Sequencer

**MotorBuster** is a specialized Digital Audio Workstation (DAW) designed for **Force Feedback** sequencing. It allows you to sequence haptic effects (vibrations, forces, textures) and play them back in real-time on any DirectInput compatible device (Gamepads, Steering Wheels, Flight Sticks).
<img width="100" height="100" alt="Generated Image March 27, 2026 - 4_53AM (2)" src="https://github.com/user-attachments/assets/a2b23d80-2724-454a-b7a6-475f140ba010" />

<img width="1895" height="1188" alt="image" src="https://github.com/user-attachments/assets/43e7f935-1c6e-44d4-b459-97b9709e49a3" />

## 🚀 Features

*   **Haptic Sequencing**: arrange haptic clips on a timeline just like audio.
*   **Real-Time Playback**: Feel the effects instantly as the playhead moves.
*   **Rich Effect Library**:
    *   **Sine**: Smooth periodic vibrations.
    *   **Constant**: Steady force/pressure.
    *   **Ramp**: Linear force gradients.
    *   **Sawtooth**: Sharp, rhythmic textures.
*   **Full Editing**:
    *   **Drag & Drop** from palette.
    *   **Resize** clips by dragging edges.
    *   **Move** clips across tracks and time.
*   **Project Management**: Save and Load your haptic compositions (`.fedit` JSON files).
*   **Broad Hardware Support**: Built on SDL2, supporting most game controllers.

## 🛠️ Installation & Build

Fedit is built with Python and Dear PyGui.

### Prerequisites
*   Python 3.12+
*   `pip install -r requirements.txt` (see dependencies below)

### Dependencies
```bash
pip install dearpygui pysdl2
```
*Note: You also need the SDL2 DLLs. The build script handles this for the standalone version.*

### Building Standalone (Windows)
To create a portable `.exe`:

1.  Clone the repository.
2.  Run the build script:
    ```bash
    python build_native.py
    ```
3.  The executable will be in `dist/Fedit.exe`.

## 🎮 Usage

1.  **Connect Controller**: Plug in your Gamepad or Haptic Device.
2.  **Launch**: Run `Fedit.exe`.
3.  **Connect**: Click **Scan** and then **Connect** in the top bar to initialize your device.
4.  **Compose**:
    *   Drag effects from the left palette to the timeline.
    *   Drag clip edges to change duration.
    *   Click a clip to edit Intensity/Frequency in the Inspector.
5.  **Play**: Hit the **Play** button to feel your sequence!

## 📄 License
MIT License. Free to use and modify.
