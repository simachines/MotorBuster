import math
import sys
import os

OUTPUT_FILE = os.path.abspath("debug_waves_out.txt")

def wave_amplitude(type, t, freq, magnitude, start_phase):
    mag = magnitude
    freq = max(1e-6, freq)
    phase = 2 * math.pi * freq * t
    start_phase_rad = math.radians(start_phase)
    
    # Original logic
    total_phase = (phase + start_phase_rad) % (2 * math.pi)
    norm_phase = total_phase / (2 * math.pi)
    
    if type == "Square":
        return mag * (1.0 if math.sin(total_phase) >= 0 else -1.0)
    elif type == "Triangle":
        if norm_phase < 0.25: return mag * (4.0 * norm_phase)
        elif norm_phase < 0.75: return mag * (2.0 - 4.0 * norm_phase)
        else: return mag * (4.0 * norm_phase - 4.0)
    elif type in ("Sawtooth", "SawtoothUp", "Ramp"):
         return mag * (-1.0 + 2.0 * norm_phase)
    elif type == "SawtoothDown":
         return mag * (1.0 - 2.0 * norm_phase)
    return mag * math.sin(total_phase)

def run():
    lines = []
    lines.append("TYPE\tPHASE\tSTART_VAL")
    
    for type in ["Sine", "Square", "Triangle", "SawtoothUp", "SawtoothDown"]:
        for p in [0, 90]:
            val = wave_amplitude(type, 0.0, 1.0, 1.0, p)
            lines.append(f"{type}\t{p}\t{val:.4f}")
            
    content = "\n".join(lines)
    print(content)
    
    try:
        with open(OUTPUT_FILE, "w") as f:
            f.write(content)
        print(f"Written to {OUTPUT_FILE}")
    except Exception as e:
        print(f"File write error: {e}")

if __name__ == "__main__":
    run()
