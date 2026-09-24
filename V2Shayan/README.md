# SIH SWside V2 (Hardware & Progressive)

This is the V2 fork of the B&W Image Pixel Serializer. It extends the original offline compression pipeline to include **two-phase progressive transmission** over a real hardware link using ESP32s and PC-side USB serial bridges (`what.py` and `rec.py`).

## Pipeline Overview

```
                         ┌─────────────────────────────────────────────┐
                         │           TX ENCODER (Satellite)            │
                         │                                             │
  [Grayscale Image] ───► │  Grayscale → Pixel array → Encode/Compress  │
                         │         ▼            ▼            ▼         │
                         │     what.py     (Streams via USB to ESP32)  │
                         └────────┬────────────┬────────────┬──────────┘
                                  │   Laser/RF link (6 Kbps)│
                         ┌────────▼────────────▼────────────▼──────────┐
                         │           RX DECODER (Ground)               │
                         │                                             │
                         │  rec.py → Assembles packets from USB        │
                         │         ▼            ▼            ▼         │
                         │  Decompress → Pixel array → Reconstruct     │
                         └──────────────────────────────────────────────┘
                                             ▼
                                   [Reconstructed .png]
```

## Quick Start (Hardware Link)

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Flash ESP32s
# Flash esp32_tx/esp32_tx.ino to the transmitter ESP32
# Flash esp32_rx/esp32_rx.ino to the receiver ESP32

# 3. Start Receiver (listen on COM5, auto-show image)
python rec.py --port COM5 --show

# 4. Start Transmitter (Progressive mode, tile=10)
python what.py tomsahur-modified.png --port COM3 --tile 10 --progressive --chunk-size 200
```

## Hardware Pipeline (`what.py` and `rec.py`)

The `what.py` and `rec.py` scripts act as bridges between your PC and the ESP32s over USB Serial. 

### `what.py` (PC Transmitter Bridge)
Packages a file, adds a transmission header, and streams it chunk-by-chunk to the ESP32 transmitter over USB with ACK flow control.
```bash
# Full resolution
python what.py image.png --port COM3
# Progressive mode (Instant preview + lossless full detail)
python what.py image.png --port COM3 --tile 10 --progressive
```

### `rec.py` (PC Receiver Bridge)
Listens on the serial COM port, assembles laser packets, verifies headers, and automatically triggers `rx_decoder.py` to rebuild the image.
```bash
python rec.py --port COM5 --show
```

## Progressive Transmission

When using `what.py` with the `--progressive` flag, the image is sent in two phases:
1. **Phase 1 (Preview):** A tiny downsampled version (based on `--tile`) is sent first. It rebuilds instantly as a blocky preview.
2. **Phase 2 (Full Detail):** Per-pixel residuals are transmitted. The decoder mathematically combines them with the preview to form a 100% pixel-perfect lossless image.

*Result:* Instead of waiting 5 minutes for an image to load, you get a recognizable preview in ~10 seconds, which gradually sharpens to perfect quality. Both streams use delta encoding and `zstd` level 19.

## Original Scripts included
This fork retains the original `tx_encoder.py` and `rx_decoder.py` tools which can be used to manually generate, decode, and analyze `.bwdata`, `.bwdata.bin`, and `.bwdata.zst` files entirely offline.

## Project Structure

```
V2Shayan/
├── what.py               # Serial TX bridge (streams payload to ESP32)
├── rec.py                # Serial RX bridge (listens & reconstructs)
├── tx_encoder.py         # Offline TX: image → .bwdata / .bin / .zst
├── rx_decoder.py         # Offline RX: .bwdata / .bin / .zst → image
├── esp32_tx/             # ESP32 Transmitter Arduino sketch
├── esp32_rx/             # ESP32 Receiver Arduino sketch
├── requirements.txt      # Python dependencies
└── README.md             # This file
```
