# SIH SWside — B&W Image Pixel Serializer

Encode black-and-white images into pixel data arrays for ESP-based wireless transmission, and reconstruct them on the receiver side.

## How It Works

```
[B&W Image] → tx_encoder.py → .bwdata file → ESP TX → ESP RX → rx_decoder.py → [Reconstructed Image]
```

**Encoder** reads every pixel's grayscale shade (0-255), stores it in order (row-by-row, left-to-right) into a structured file. **Decoder** reads that file and rebuilds the image pixel-by-pixel.

## Setup

```bash
pip install -r requirements.txt
```

## Usage

### Encode an image
```bash
python tx_encoder.py image.png                    # full resolution
python tx_encoder.py image.png --tile 4           # 4x downsampling (smaller file)
python tx_encoder.py image.png -o out.bwdata      # custom output path
python tx_encoder.py image.png --preview          # show matplotlib preview
```

### Decode / Reconstruct
```bash
python rx_decoder.py image.bwdata -o restored.png       # from JSON format
python rx_decoder.py image.bwdata.bin -o restored.png    # from binary format
python rx_decoder.py image.bwdata --show                 # auto-open image
```

### Debug Web UI (upload & reconstruct in browser)
```bash
python rx_decoder.py --web
# Open http://localhost:5000 and drag-drop your .bwdata file
```

## File Formats

| Format | Extension | Use Case |
|--------|-----------|----------|
| JSON | `.bwdata` | Human-readable, includes shade labels, great for debugging |
| Binary | `.bwdata.bin` | Compact (1 byte/pixel + 4-byte header), ideal for ESP streaming |

## Tile Size Guide (for bandwidth-limited links)

For a **1080×1080** image at **6 Kbps**:

| Tile | Output | Data | TX Time |
|------|--------|------|---------|
| `--tile 10` | 108×108 | 11 KB | ~15 sec |
| `--tile 16` | 67×67 | 4.4 KB | ~6 sec |
| `--tile 20` | 54×54 | 2.8 KB | ~4 sec |

## Dependencies

- Python 3.8+
- Pillow (image processing)
- Flask (debug web UI)
- matplotlib (optional preview)
