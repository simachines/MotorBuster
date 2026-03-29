from PIL import Image, ImageDraw
import math
import os

def create_vibration_icon():
    size = (256, 256)
    # Transparent background
    img = Image.new('RGBA', size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Colors: Yellow, Orange, Purple, Green
    colors = [
        (255, 255, 0),   # Yellow
        (255, 165, 0),   # Orange
        (128, 0, 128),   # Purple
        (0, 128, 0)      # Green
    ]

    center_y = size[1] // 2
    amplitude = 90
    frequency = 0.05
    width = size[0]
    
    # We'll draw line segments to simulate a gradient curve
    prev_x = 0
    prev_y = center_y
    
    stroke_width = 18

    for x in range(width):
        # Normalized position 0..1
        t = x / width
        
        # Window function (Hanning-like) to taper ends
        # sin(pi * t)^2
        window = math.sin(math.pi * t) ** 2
        
        # Wave function
        # sin(x * freq)
        # Add some phase shift to center a peak if we want, or just let it flow
        angle = (x - width/2) * frequency
        y = center_y + math.sin(angle) * amplitude * window

        # Color Interpolation
        # Map t (0..1) to color indices (0..3)
        # 3 segments
        segment_len = 1.0 / (len(colors) - 1)
        segment_idx = int(t / segment_len)
        segment_idx = min(segment_idx, len(colors) - 2)
        
        segment_t = (t - (segment_idx * segment_len)) / segment_len
        
        c1 = colors[segment_idx]
        c2 = colors[segment_idx + 1]
        
        r = int(c1[0] + (c2[0] - c1[0]) * segment_t)
        g = int(c1[1] + (c2[1] - c1[1]) * segment_t)
        b = int(c1[2] + (c2[2] - c1[2]) * segment_t)
        color = (r, g, b, 255)

        # Draw segment
        if x > 0:
            draw.line([prev_x, prev_y, x, y], fill=color, width=stroke_width)
            # Fill gaps for smooth thick line (simple circle at joining point)
            draw.ellipse([x-stroke_width/2, y-stroke_width/2, x+stroke_width/2, y+stroke_width/2], fill=color)

        prev_x = x
        prev_y = y

    # Ensure assets directory exists
    os.makedirs("assets", exist_ok=True)
    
    # Save as ICO and PNG
    img.save("assets/icon.ico", format='ICO', sizes=[(256, 256)])
    img.save("assets/icon.png", format='PNG')
    print("Vibration Icon generated in assets/")

if __name__ == "__main__":
    create_vibration_icon()
