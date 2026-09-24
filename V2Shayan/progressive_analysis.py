"""
Test all compression pipeline combos on tomsahur-modified.png with tile=10.
Pipelines tested:
  1. delta -> zstd              (current)
  2. rle -> zstd
  3. delta -> rle -> zstd
  4. Progressive Phase2: residuals -> delta -> zstd   (current progressive)
  5. Progressive Phase2: residuals -> rle -> zstd
  6. Progressive Phase2: residuals -> delta -> rle -> zstd
"""
import os, sys
from PIL import Image
import zstandard as zstd

img_path = os.path.join(os.path.dirname(__file__), "tomsahur-modified.png")
img = Image.open(img_path).convert("L")
orig_w, orig_h = img.size
TILE = 10

# ── helpers ──
def delta_encode(data):
    out = bytearray(len(data))
    out[0] = data[0]
    for i in range(1, len(data)):
        out[i] = (data[i] - data[i-1]) % 256
    return bytes(out)

def rle_encode(data):
    """Simple RLE: [count, value] pairs. count = 1-255."""
    out = bytearray()
    i = 0
    while i < len(data):
        val = data[i]
        run = 1
        while i + run < len(data) and data[i + run] == val and run < 255:
            run += 1
        out.append(run)
        out.append(val)
        i += run
    return bytes(out)

def rle_decode(data):
    out = bytearray()
    for i in range(0, len(data), 2):
        run = data[i]
        val = data[i+1]
        out.extend([val] * run)
    return bytes(out)

cctx = zstd.ZstdCompressor(level=19)

# ── Prepare data ──
crop_w = (orig_w // TILE) * TILE
crop_h = (orig_h // TILE) * TILE
tiled_img = img.crop((0, 0, crop_w, crop_h)).resize(
    (crop_w // TILE, crop_h // TILE), Image.LANCZOS
)
tw, th = tiled_img.size
tiled_pixels = bytes(tiled_img.getdata())

full_img = img.crop((0, 0, crop_w, crop_h))
full_pixels = bytes(full_img.getdata())
full_w, full_h = crop_w, crop_h

# Residuals (Phase 2 of progressive)
residuals = bytearray(len(full_pixels))
for y in range(full_h):
    for x in range(full_w):
        idx = y * full_w + x
        tx_idx = x // TILE
        ty_idx = y // TILE
        avg_val = tiled_pixels[ty_idx * tw + tx_idx]
        residuals[idx] = (full_pixels[idx] - avg_val + 128) % 256
residuals = bytes(residuals)

print(f"Image: {orig_w}x{orig_h}, tile={TILE}")
print(f"Phase 1 (AVG): {tw}x{th} = {len(tiled_pixels):,} bytes raw")
print(f"Phase 2 (residuals): {full_w}x{full_h} = {len(residuals):,} bytes raw")
print(f"Full pixels (no progressive): {len(full_pixels):,} bytes raw")

SPEED = 620.0

def test(name, data, pipeline_desc):
    zst = cctx.compress(data)
    t = len(zst) / SPEED
    print(f"  {name:45s} | pipe: {pipeline_desc:30s} | {len(data):>10,} pre-zstd | {len(zst):>8,} final | {t:6.1f}s")
    return len(zst)

print(f"\n{'='*120}")
print("FULL IMAGE (no progressive, single pass)")
print(f"{'='*120}")
raw = full_pixels
test("raw -> zstd", cctx.compress(raw) and raw, "raw -> zstd")

d = delta_encode(raw)
s1 = test("delta -> zstd (CURRENT)", d, "delta -> zstd")

r = rle_encode(raw)
test("rle -> zstd", r, "rle -> zstd")

dr = rle_encode(d)
test("delta -> rle -> zstd", dr, "delta -> rle -> zstd")

# zstd only (no preprocessing)
z_raw = cctx.compress(raw)
print(f"  {'raw -> zstd (no preprocess)':45s} | pipe: {'raw -> zstd':30s} | {len(raw):>10,} pre-zstd | {len(z_raw):>8,} final | {len(z_raw)/SPEED:6.1f}s")

print(f"\n{'='*120}")
print("PHASE 1 (AVG preview) - same for all methods")
print(f"{'='*120}")
p1_d = delta_encode(tiled_pixels)
p1_zst = test("Phase1: delta -> zstd", p1_d, "delta -> zstd")

p1_r = rle_encode(tiled_pixels)
test("Phase1: rle -> zstd", p1_r, "rle -> zstd")

p1_dr = rle_encode(p1_d)
test("Phase1: delta -> rle -> zstd", p1_dr, "delta -> rle -> zstd")

print(f"\n{'='*120}")
print("PHASE 2 (residuals from AVG)")
print(f"{'='*120}")

# Distribution analysis of residuals
from collections import Counter
hist = Counter(residuals)
top5 = hist.most_common(5)
print(f"  Residual distribution - top 5 values: {top5}")
center_band = sum(1 for v in residuals if 118 <= v <= 138)
print(f"  Values within +/-10 of center (128): {center_band:,} / {len(residuals):,} = {center_band/len(residuals)*100:.1f}%")

p2_d = delta_encode(residuals)
s2_d = test("Phase2: residuals -> delta -> zstd", p2_d, "delta -> zstd")

p2_r = rle_encode(residuals)
s2_r = test("Phase2: residuals -> rle -> zstd", p2_r, "rle -> zstd")

p2_dr = rle_encode(p2_d)
s2_dr = test("Phase2: residuals -> delta -> rle -> zstd", p2_dr, "delta -> rle -> zstd")

# Also test: residuals quantized to fewer levels then RLE
quant4 = bytes((v >> 2) << 2 for v in residuals)  # quantize to 64 levels
q4_r = rle_encode(quant4)
test("Phase2: quantize(4) -> rle -> zstd (LOSSY)", q4_r, "quant4 -> rle -> zstd")

quant8 = bytes((v >> 3) << 3 for v in residuals)  # quantize to 32 levels
q8_r = rle_encode(quant8)
test("Phase2: quantize(8) -> rle -> zstd (LOSSY)", q8_r, "quant8 -> rle -> zstd")

print(f"\n{'='*120}")
print("TOTAL PROGRESSIVE COMPARISON @ {:.0f} B/s".format(SPEED))
print(f"{'='*120}")

combos = [
    ("Current single-pass (delta+zstd)", s1, 0),
    ("Progressive: delta+zstd / delta+zstd", p1_zst + s2_d, p1_zst),
    ("Progressive: delta+zstd / rle+zstd", p1_zst + s2_r, p1_zst),
    ("Progressive: delta+zstd / delta+rle+zstd", p1_zst + s2_dr, p1_zst),
]

for name, total, preview in combos:
    t_total = total / SPEED
    t_preview = preview / SPEED
    vs = ((total - s1) / s1) * 100
    print(f"  {name:50s} | {total:>8,} B | preview: {t_preview:5.1f}s | total: {t_total:6.1f}s ({t_total/60:.1f}m) | vs current: {vs:+.1f}%")
