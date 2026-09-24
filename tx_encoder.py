"""
TX Encoder — Image to Pixel Data Serializer
=============================================
Takes a black-and-white (or grayscale) image, reads every pixel,
and writes a structured .bwdata file containing:
  • image metadata (width, height, total_pixels)
  • a flat array of grayscale shade values (0 = pure black … 255 = pure white)

Pixels are stored row-by-row, left-to-right (raster order) so an ESP
can stream them sequentially.

Usage:
    python tx_encoder.py <image_path> [--output <output_path>] [--tile <size>]

Options:
    --output, -o   Path for the .bwdata output file (default: <image_name>.bwdata)
    --tile,  -t    Optional tile size (NxN pixels averaged into one value).
                   Use 1 for full-resolution pixel-level encoding (default: 1).
    --preview      Show a matplotlib preview of the encoded data before saving.
"""

import argparse
import json
import os
import sys
import time

try:
    import zstandard as zstd
    HAS_ZSTD = True
except ImportError:
    HAS_ZSTD = False

try:
    from PIL import Image
except ImportError:
    print("ERROR: Pillow is required.  Install it with:  pip install Pillow")
    sys.exit(1)


# ── helpers ──────────────────────────────────────────────────────────────────

def classify_shade(value: int) -> str:
    """Return a human-readable shade label for a grayscale value."""
    if value == 0:
        return "pure_black"
    elif value <= 50:
        return "very_dark"
    elif value <= 100:
        return "dark_gray"
    elif value <= 155:
        return "mid_gray"
    elif value <= 200:
        return "light_gray"
    elif value <= 254:
        return "very_light"
    else:
        return "pure_white"


def encode_image(image_path: str, tile_size: int = 1):
    """
    Load an image, convert to grayscale, and return a data dict with
    metadata + pixel/tile array.
    """
    img = Image.open(image_path).convert("L")  # force grayscale
    orig_w, orig_h = img.size

    # ── tiling (averaging NxN blocks) ────────────────────────────────────
    if tile_size > 1:
        # crop to exact multiple of tile_size
        crop_w = (orig_w // tile_size) * tile_size
        crop_h = (orig_h // tile_size) * tile_size
        img = img.crop((0, 0, crop_w, crop_h))
        img = img.resize((crop_w // tile_size, crop_h // tile_size), Image.LANCZOS)

    w, h = img.size
    pixels = list(img.getdata())  # flat list, row-major

    # build per-pixel records
    pixel_array = []
    for idx, val in enumerate(pixels):
        pixel_array.append({
            "i": idx,
            "v": val,            # 0-255 shade value
            "s": classify_shade(val),  # human label
        })

    data = {
        "header": {
            "source_file": os.path.basename(image_path),
            "original_width": orig_w,
            "original_height": orig_h,
            "encoded_width": w,
            "encoded_height": h,
            "tile_size": tile_size,
            "total_pixels": w * h,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        },
        "pixels": pixel_array,
    }
    return data


def save_bwdata(data: dict, output_path: str):
    """Write the encoded data to a .bwdata JSON file."""
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    size_kb = os.path.getsize(output_path) / 1024
    return size_kb


def save_compact_bwdata(data: dict, output_path: str):
    """
    Also write a compact binary-ish version (.bwdata.bin) that only stores
    raw shade values — much smaller, ideal for ESP streaming.

    Format:
        bytes 0-1 : width  (uint16, big-endian)
        bytes 2-3 : height (uint16, big-endian)
        bytes 4-N : pixel values (1 byte each, row-major)
    """
    w = data["header"]["encoded_width"]
    h = data["header"]["encoded_height"]
    bin_path = output_path + ".bin"
    with open(bin_path, "wb") as f:
        f.write(w.to_bytes(2, "big"))
        f.write(h.to_bytes(2, "big"))
        f.write(bytes(p["v"] for p in data["pixels"]))
    size_kb = os.path.getsize(bin_path) / 1024
    return bin_path, size_kb


def delta_encode(pixel_bytes: bytes) -> bytes:
    """
    Delta-encode a byte sequence: store first pixel as-is, then
    store the difference (mod 256) between consecutive pixels.
    This clusters values near 0, making them much more compressible.
    """
    out = bytearray(len(pixel_bytes))
    out[0] = pixel_bytes[0]
    for i in range(1, len(pixel_bytes)):
        out[i] = (pixel_bytes[i] - pixel_bytes[i - 1]) % 256
    return bytes(out)


def save_compressed_bwdata(data: dict, output_path: str):
    """
    Write a delta+zstd compressed version (.bwdata.zst) optimised for
    low-bandwidth laser (OISL) transmission.

    Format:
        bytes 0-1 : width  (uint16, big-endian)
        bytes 2-3 : height (uint16, big-endian)
        bytes 4-N : zstd-compressed, delta-encoded pixel values

    The byte array from bytes 4-N is what goes straight into the
    Arduino/laser transmitter.
    """
    if not HAS_ZSTD:
        return None, 0, 0

    w = data["header"]["encoded_width"]
    h = data["header"]["encoded_height"]
    raw_pixels = bytes(p["v"] for p in data["pixels"])
    delta_pixels = delta_encode(raw_pixels)

    cctx = zstd.ZstdCompressor(level=19)
    compressed = cctx.compress(delta_pixels)

    zst_path = output_path + ".zst"
    with open(zst_path, "wb") as f:
        f.write(w.to_bytes(2, "big"))
        f.write(h.to_bytes(2, "big"))
        f.write(compressed)

    raw_kb = len(raw_pixels) / 1024
    zst_kb = os.path.getsize(zst_path) / 1024
    ratio = len(raw_pixels) / len(compressed) if compressed else 0
    return zst_path, zst_kb, ratio


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="TX Encoder — convert a B&W image to a .bwdata pixel file"
    )
    parser.add_argument("image", help="Path to the input image")
    parser.add_argument(
        "-o", "--output", default=None,
        help="Output .bwdata file path (default: <image_name>.bwdata)"
    )
    parser.add_argument(
        "-t", "--tile", type=int, default=1,
        help="Tile size for downsampling (1 = pixel-level, default: 1)"
    )
    parser.add_argument(
        "--preview", action="store_true",
        help="Show a matplotlib preview of the encoded data"
    )
    args = parser.parse_args()

    if not os.path.isfile(args.image):
        print(f"ERROR: File not found: {args.image}")
        sys.exit(1)

    output_path = args.output or os.path.splitext(args.image)[0] + ".bwdata"

    print("=" * 60)
    print("  TX ENCODER -- Image -> Pixel Data")
    print("=" * 60)
    print(f"  Input image : {args.image}")
    print(f"  Tile size   : {args.tile}x{args.tile}")

    # encode
    data = encode_image(args.image, tile_size=args.tile)
    hdr = data["header"]

    print(f"  Original res: {hdr['original_width']}×{hdr['original_height']}")
    print(f"  Encoded res : {hdr['encoded_width']}×{hdr['encoded_height']}")
    print(f"  Total pixels: {hdr['total_pixels']:,}")

    # shade distribution
    shade_counts = {}
    for p in data["pixels"]:
        shade_counts[p["s"]] = shade_counts.get(p["s"], 0) + 1
    print("\n  Shade distribution:")
    for shade, count in sorted(shade_counts.items(), key=lambda x: -x[1]):
        pct = count / hdr["total_pixels"] * 100
        print(f"    {shade:15s} : {count:>8,}  ({pct:5.1f}%)")

    # save JSON
    size_kb = save_bwdata(data, output_path)
    print(f"\n  JSON file   : {output_path}  ({size_kb:,.1f} KB)")

    # save compact binary
    bin_path, bin_kb = save_compact_bwdata(data, output_path)
    print(f"  Binary file : {bin_path}  ({bin_kb:,.1f} KB)")

    # save delta+zstd compressed (for laser OISL transmission)
    if HAS_ZSTD:
        zst_path, zst_kb, ratio = save_compressed_bwdata(data, output_path)
        if zst_path:
            tx_sec = (zst_kb * 1024 * 8) / 6000  # estimated TX time at 6 Kbps
            print(f"  Compressed  : {zst_path}  ({zst_kb:,.1f} KB)")
            print(f"  Compression : {ratio:.2f}x  (delta+zstd-19, lossless)")
            print(f"  Est. TX @6Kb: {tx_sec:.0f}s ({tx_sec/60:.1f} min)")
    else:
        print("  [SKIP] zstandard not installed -- no compressed output")
        print("         Install with:  pip install zstandard")

    print("=" * 60)
    print("  [OK] Encoding complete -- ready for laser transmission")
    print("=" * 60)

    # optional preview
    if args.preview:
        try:
            import matplotlib.pyplot as plt
            import numpy as np
            arr = np.array([p["v"] for p in data["pixels"]], dtype=np.uint8)
            arr = arr.reshape((hdr["encoded_height"], hdr["encoded_width"]))
            plt.figure(figsize=(8, 6))
            plt.imshow(arr, cmap="gray", vmin=0, vmax=255)
            plt.title(f"Encoded Preview  ({hdr['encoded_width']}×{hdr['encoded_height']})")
            plt.colorbar(label="Shade (0=black, 255=white)")
            plt.tight_layout()
            plt.show()
        except ImportError:
            print("Install matplotlib for preview:  pip install matplotlib")


if __name__ == "__main__":
    main()
