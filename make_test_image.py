"""Generate a simple test B&W gradient image for the encoder/decoder pipeline."""
from PIL import Image
import os

w, h = 64, 64
img = Image.new("L", (w, h))
for y in range(h):
    for x in range(w):
        # diagonal gradient with some patterns
        val = int(((x + y) / (w + h - 2)) * 255)
        # add a dark circle in the center
        cx, cy = w // 2, h // 2
        dist = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
        if dist < 12:
            val = max(0, val - 150)
        img.putpixel((x, y), val)

out = os.path.join(os.path.dirname(__file__), "test_image.png")
img.save(out)
print(f"Created test image: {out}  ({w}x{h})")
