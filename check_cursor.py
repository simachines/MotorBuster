import dearpygui.dearpygui as dpg

attrs = dir(dpg)
cursor_attrs = [a for a in attrs if 'cursor' in a.lower() or 'mouse' in a.lower()]
print("Cursor/Mouse attributes:", cursor_attrs)
