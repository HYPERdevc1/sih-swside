"""Verify progressive encode/decode round-trip is lossless (no serial needed)."""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))

from PIL import Image
import zstandard as zstd

# Import the encoder from what.py
from what import delta_encode, prepare_progressive
from rec import delta_decode

IMG = os.path.join(os.path.dirname(__file__), "tomsahur-modified.png")
TILE = 10

# ── Encode ──
format_flag, payload, _ = prepare_progressive(IMG, tile_size=TILE)
assert format_flag == b"P", f"Expected format P, got {format_flag}"

# ── Parse the progressive payload (same as rec.py does) ──
tile_size = payload[0]
p1_w = int.from_bytes(payload[1:3], 'big')
p1_h = int.from_bytes(payload[3:5], 'big')
full_w = int.from_bytes(payload[5:7], 'big')
full_h = int.from_bytes(payload[7:9], 'big')
p1_len = int.from_bytes(payload[9:13], 'big')
p2_len = int.from_bytes(payload[13:17], 'big')

print(f"\nPayload parsed: tile={tile_size}, preview={p1_w}x{p1_h}, full={full_w}x{full_h}")
print(f"Phase 1: {p1_len:,} bytes, Phase 2: {p2_len:,} bytes")

# ── Decode Phase 1 ──
dctx = zstd.ZstdDecompressor()
p1_zst = payload[17 : 17 + p1_len]
p1_pixels = delta_decode(dctx.decompress(p1_zst))

preview = Image.new("L", (p1_w, p1_h))
preview.putdata(p1_pixels)
print(f"Phase 1 preview: {p1_w}x{p1_h} OK")

# ── Decode Phase 2 ──
p2_zst = payload[17 + p1_len : 17 + p1_len + p2_len]
p2_residuals = delta_decode(dctx.decompress(p2_zst))

full_pixels = [0] * (full_w * full_h)
for y in range(full_h):
    for x in range(full_w):
        idx = y * full_w + x
        tx_idx = x // tile_size
        ty_idx = y // tile_size
        avg_val = p1_pixels[ty_idx * p1_w + tx_idx]
        full_pixels[idx] = (p2_residuals[idx] - 128 + avg_val) % 256

# ── Compare against original ──
orig = Image.open(IMG).convert("L")
crop_w = (orig.size[0] // TILE) * TILE
crop_h = (orig.size[1] // TILE) * TILE
orig = orig.crop((0, 0, crop_w, crop_h))
orig_pixels = list(orig.getdata())

assert len(full_pixels) == len(orig_pixels), (
    f"Size mismatch: {len(full_pixels)} vs {len(orig_pixels)}"
)

mismatches = sum(1 for a, b in zip(full_pixels, orig_pixels) if a != b)
total = len(orig_pixels)

if mismatches == 0:
    print(f"\nLOSSLESS VERIFICATION: PASS  ({total:,} pixels, 0 mismatches)")
else:
    print(f"\nLOSSLESS VERIFICATION: FAIL  ({mismatches:,} / {total:,} mismatches)")
    sys.exit(1)

print("Progressive encode/decode round-trip is pixel-perfect.")
