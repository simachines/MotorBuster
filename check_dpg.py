import dearpygui.dearpygui as dpg

attrs = dir(dpg)
cursor_attrs = [a for a in attrs if 'cursor' in a.lower()]
print("Cursor attributes:", cursor_attrs)

mouse_attrs = [a for a in attrs if 'mouse' in a.lower()]
print("Mouse attributes:", mouse_attrs)
