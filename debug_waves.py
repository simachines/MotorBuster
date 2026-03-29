import math
import sys
import os

OUTPUT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "debug_output_direct.txt")

def wave_amplitude(type, t, freq, magnitude, start_phase, sweep_enabled=False):
    mag = magnitude
    if type == "Constant":
        return mag
    
    # Periodic types
    freq = max(1e-6, freq)
    
    # Phase calculation
    phase = 2 * math.pi * freq * t
        
    start_phase_rad = math.radians(start_phase)
    # The current logic trace from native_app_py
    total_phase = (phase + start_phase_rad) % (2 * math.pi)
    norm_phase = total_phase / (2 * math.pi) # 0.0 to 1.0
    
    if type == "Square":
        return mag * (1.0 if math.sin(total_phase) >= 0 else -1.0)
        
    elif type == "Triangle":
        if norm_phase < 0.25:
            return mag * (4.0 * norm_phase)
        elif norm_phase < 0.75:
            return mag * (2.0 - 4.0 * norm_phase)
        else:
            return mag * (4.0 * norm_phase - 4.0)
            
    elif type in ("Sawtooth", "SawtoothUp", "Ramp"):
         return mag * (-1.0 + 2.0 * norm_phase)
         
    elif type == "SawtoothDown":
         return mag * (1.0 - 2.0 * norm_phase)

    # Default: Sine
    return mag * math.sin(total_phase)

def plot_wave(f, type, phase_deg):
    f.write(f"--- {type} (Phase {phase_deg}) ---\n")
    duration = 1.0
    freq = 1.0
    steps = 20
    chars = []
    
    for i in range(steps + 1):
        t = (i / steps) * duration
        # We simulate 1 period (freq=1.0)
        val = wave_amplitude(type, t, freq, 1.0, phase_deg)
        chars.append(f"{val:.2f}")
    
    f.write(" ".join(chars) + "\n")

try:
    print(f"Writing to {OUTPUT_FILE}")
    with open(OUTPUT_FILE, "w") as f:
        f.write("Current Implementation Check:\n")
        plot_wave(f, "Sine", 0)
        plot_wave(f, "Sine", 90)
        plot_wave(f, "Triangle", 0)
        plot_wave(f, "Triangle", 90)
        plot_wave(f, "Square", 0)
        plot_wave(f, "Square", 90)
        plot_wave(f, "SawtoothUp", 0)
        plot_wave(f, "SawtoothUp", 90)
        plot_wave(f, "SawtoothDown", 0)
        plot_wave(f, "SawtoothDown", 90)
    print("Done writing.")
except Exception as e:
    print(f"Error: {e}")
