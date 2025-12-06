import dearpygui.dearpygui as dpg

with open('dpg_dump.txt', 'w') as f:
    f.write("DROP: " + str([m for m in dir(dpg) if "drop" in m.lower()]) + "\n")
    f.write("DRAG: " + str([m for m in dir(dpg) if "drag" in m.lower()]) + "\n")
