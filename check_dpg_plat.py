import dearpygui.dearpygui as dpg

attrs = dir(dpg)
plat_attrs = [a for a in attrs if 'platform' in a.lower() or 'window' in a.lower()]
print("Platform/Window attributes:", plat_attrs)
