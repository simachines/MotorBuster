import dearpygui.dearpygui as dpg

with open('dpg_api.txt', 'w') as f:
    try:
        f.write(f"DPG Version: {dpg.get_dearpygui_version()}\n")
        f.write(f"Drag: {[x for x in dir(dpg) if 'drag' in x.lower()]}\n")
        f.write(f"Drop: {[x for x in dir(dpg) if 'drop' in x.lower()]}\n")
    except Exception as e:
        f.write(f"Error: {e}\n")
