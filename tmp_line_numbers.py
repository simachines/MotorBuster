from pathlib import Path
text = Path('native_app.py').read_text().splitlines()
patterns = [
    ('center_line', 'height / 2], [total_w, height / 2'),
    ('wheel_playhead_helper', 'def _should_show_wheel_playhead'),
]
for name, pat in patterns:
    for idx, line in enumerate(text, 1):
        if pat in line:
            print(f"{name} {idx}")
            break
