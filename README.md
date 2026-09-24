# SIH SWside — B&W Image Pixel Serializer

Encode black-and-white (grayscale) images into pixel data streams for ESP-based wireless/laser (OISL) transmission, and reconstruct them on the receiver side — with lossless delta+zstd compression for low-bandwidth links.

## Pipeline Overview

```
                         ┌─────────────────────────────────────────────┐
                         │           TX ENCODER (Satellite)            │
                         │                                             │
  [Grayscale Image] ───► │  Grayscale → Pixel array → Encode/Compress  │
                         │         ▼            ▼            ▼         │
                         │     .bwdata     .bwdata.bin   .bwdata.zst   │
                         └────────┬────────────┬────────────┬──────────┘
                                  │   Laser/RF link (6 Kbps)│
                         ┌────────▼────────────▼────────────▼──────────┐
                         │           RX DECODER (Ground)               │
                         │                                             │
                         │  Decompress → Pixel array → Reconstruct     │
                         │         CLI mode  │  Web debug UI            │
                         └──────────────────────────────────────────────┘
                                             ▼
                                   [Reconstructed .png]
```

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Generate a test image (optional)
python make_test_image.py

# 3. Encode
python tx_encoder.py test_image.png

# 4. Decode (pick any format)
python rx_decoder.py test_image.bwdata.zst -o restored.png
```

## Setup

```bash
pip install -r requirements.txt
```

### Dependencies

| Package | Purpose |
|---------|---------|
| **Pillow** ≥ 10.0 | Image loading, pixel manipulation, reconstruction |
| **Flask** ≥ 3.0 | Debug web UI for drag-and-drop decoding |
| **zstandard** ≥ 0.20 | Delta+zstd lossless compression for OISL links |
| **matplotlib** ≥ 3.7 | Optional: preview encoded data as a heatmap |

## Usage

### TX Encoder — Image → Pixel Data

```bash
python tx_encoder.py image.png                    # full resolution, all 3 formats
python tx_encoder.py image.png --tile 4           # 4× downsampling (smaller output)
python tx_encoder.py image.png -o out.bwdata      # custom output path
python tx_encoder.py image.png --preview          # show matplotlib preview
```

The encoder outputs **three files** in one pass:

| File | Description |
|------|-------------|
| `image.bwdata` | JSON — human-readable, includes shade labels |
| `image.bwdata.bin` | Binary — 4-byte header + 1 byte/pixel, ideal for ESP |
| `image.bwdata.zst` | Compressed — delta-encoded + zstd-19, smallest size |

### RX Decoder — Pixel Data → Image

#### CLI Mode

```bash
python rx_decoder.py image.bwdata.zst              # from compressed (recommended)
python rx_decoder.py image.bwdata.bin               # from binary
python rx_decoder.py image.bwdata                   # from JSON
python rx_decoder.py image.bwdata -o restored.png   # custom output path
python rx_decoder.py image.bwdata --show            # auto-open the image
```

#### Web Debug UI

```bash
python rx_decoder.py --web                 # launch on port 5000
python rx_decoder.py --web --port 8080     # custom port
```

Then open `http://localhost:5000` — drag-and-drop any `.bwdata`, `.bwdata.bin`, or `.bwdata.zst` file to reconstruct and preview the image in your browser.

### Generate Test Image

```bash
python make_test_image.py
# Creates a 64×64 diagonal gradient with a dark circle — good for pipeline testing
```

## File Formats

### `.bwdata` (JSON)

```json
{
  "header": {
    "source_file": "image.png",
    "encoded_width": 1080,
    "encoded_height": 1080,
    "tile_size": 1,
    "total_pixels": 1166400,
    "timestamp": "2026-09-24T22:00:00+0530"
  },
  "pixels": [
    {"i": 0, "v": 42, "s": "very_dark"},
    {"i": 1, "v": 180, "s": "light_gray"},
    ...
  ]
}
```

### `.bwdata.bin` (Binary)

```
Bytes 0–1 : width   (uint16, big-endian)
Bytes 2–3 : height  (uint16, big-endian)
Bytes 4–N : raw pixel values (1 byte each, row-major raster order)
```

### `.bwdata.zst` (Delta + Zstd Compressed)

```
Bytes 0–1 : width   (uint16, big-endian)
Bytes 2–3 : height  (uint16, big-endian)
Bytes 4–N : zstd-compressed, delta-encoded pixel values
```

**Compression pipeline:** raw pixels → delta encoding (stores differences between consecutive pixels, mod 256) → zstd level-19 compression. Fully lossless — the decoder reverses both steps exactly.

## Compression Performance

For a **1080×1080** grayscale image (≈ 1.11 MB raw):

| Format | Size | Ratio | Notes |
|--------|------|-------|-------|
| `.bwdata` (JSON) | ~47 MB | — | Debug only, not for transmission |
| `.bwdata.bin` | ~1.11 MB | 1× | Uncompressed binary baseline |
| `.bwdata.zst` | ~183 KB | **~6×** | Delta+zstd, best for OISL |

## Tile Size Guide (bandwidth-limited links)

For a **1080×1080** image at **6 Kbps** (using `.bwdata.bin`):

| Tile | Output | Data | Est. TX Time |
|------|--------|------|-------------|
| `--tile 1` | 1080×1080 | 1.11 MB | ~25 min |
| `--tile 10` | 108×108 | 11 KB | ~15 sec |
| `--tile 16` | 67×67 | 4.4 KB | ~6 sec |
| `--tile 20` | 54×54 | 2.8 KB | ~4 sec |

> **Tip:** With `.bwdata.zst` compression at `--tile 1`, the same 1080×1080 image drops to ~183 KB — transmittable in ~4 min at 6 Kbps.

## Project Structure

```
sih-swside/
├── tx_encoder.py         # TX: image → .bwdata / .bin / .zst
├── rx_decoder.py         # RX: .bwdata / .bin / .zst → image (CLI + Web)
├── make_test_image.py    # Generate a 64×64 test gradient image
├── requirements.txt      # Python dependencies
├── .gitignore            # Excludes generated data & reconstructed images
└── README.md             # This file
```

## License

Internal — SIH project.
