import dearpygui.dearpygui as dpg

dpg.create_context()

def drop_cb(sender, app_data):
    print(f"DROP DETECTED on {sender} data={app_data}")
    dpg.set_value("status", f"Drop on {sender}: {app_data}")

with dpg.window(label="Test Drag", width=600, height=400):
    dpg.add_text("Drag this:", tag="status")
    with dpg.group():
        dpg.add_button(label="Source", width=100)
        with dpg.drag_payload(drag_data="PAYLOAD", payload_type="TEST"):
            dpg.add_text("Dragging...")

    dpg.add_spacer(height=20)
    dpg.add_text("Drop here (Child+Drawlist):")
    
    with dpg.child_window(tag="target_win", width=400, height=200):
        with dpg.drawlist(width=1000, height=1000, tag="target_draw"):
            dpg.draw_circle((50,50), 20, color=(255,0,0), fill=(255,0,0))
            dpg.draw_text((10,10), "Drawlist Content")

    # Bind
    dpg.set_item_drop_callback("target_win", drop_cb)
    # dpg.set_item_drop_callback("target_draw", drop_cb) # We know this crashes

dpg.create_viewport(title='Test Drag', width=600, height=400)
dpg.setup_dearpygui()
dpg.show_viewport()
dpg.start_dearpygui()
dpg.destroy_context()
