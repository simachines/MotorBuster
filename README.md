#  Motor Buster 
<img width="100" height="100" alt="Generated Image March 27, 2026 - 4_53AM (2)" src="https://github.com/user-attachments/assets/a2b23d80-2724-454a-b7a6-475f140ba010" />

**MotorBuster** is a specialized FFB and motor performance testing toolkit. It allows you to sequence haptic effects (vibrations, forces, textures). It is work in-progress.


<img width="1288" height="803" alt="image" src="https://github.com/user-attachments/assets/ca0a31b3-6388-4463-ba3c-c32771d4cabf" />


## 🚀 Features

*   **Haptic Sequencing**: arrange haptic clips on a timeline.
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
*   **Project Management**: Save and Load your haptic compositions (`.motorbuster` JSON files).
*   **Broad Hardware Support**: Built on SDL3, supporting most game controllers.

## 🛠️ Installation & Build

MotorBuster is built with Python and Dear PyGui.

### Prerequisites
*   Python 3.12+
*   `python -m pip install -r requirements.txt`

### Dependencies
```bash
python -m pip install -r requirements.txt
```
*Note: `requirements.txt` includes build/runtime dependencies, including `PyInstaller`.*

### Building Standalone (Windows)
To create a portable `.exe`:

1.  Clone the repository.
2.  Run the build script:
    ```bash
    python build_native.py
    ```
3.  The executable will be in `dist/MotorBuster/MotorBuster.exe`.

## 🎮 Usage

1.  **Connect Controller**: Plug in your Gamepad or Haptic Device.
2.  **Launch**: Run `MotorBuster.exe`.
3.  **Connect**: Click **Scan** and then **Connect** in the top bar to initialize your device.
4.  **Compose**:
    *   Drag effects from the left palette to the timeline.
    *   Drag clip edges to change duration.
    *   Click a clip to edit Intensity/Frequency in the Inspector.
5.  **Play**: Hit the **Play** button to feel your sequence!

## 📄 License
MIT License. Free to use and modify.
