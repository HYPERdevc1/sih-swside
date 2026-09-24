"""
what.py — Sender Side Python Bridge for ESP32 Laser Transmitter
===============================================================
Reads an image or pre-encoded .bwdata.zst / .bwdata.bin file,
packages it with a transmission header, and streams it chunk-by-chunk
to the ESP32 transmitter over USB Serial with ACK flow control.

Usage:
    python what.py <image_or_data_file> [--port COMx] [--baud 115200] [--chunk-size 64]

Example:
    python what.py test_image.png
    python what.py test_zst_restored.png --port COM3
"""

import os
import sys
import time
import struct
import argparse
import subprocess

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    print("ERROR: pyserial is required. Install it using:")
    print("    pip install pyserial")
    sys.exit(1)


MAGIC_HEADER = b"SIH1"  # 4-byte header identifier
ACK_BYTE = 0x06          # ASCII ACK byte expected from ESP32


def delta_encode(data):
    """Delta-encode a byte sequence: store differences between consecutive values."""
    out = bytearray(len(data))
    out[0] = data[0]
    for i in range(1, len(data)):
        out[i] = (data[i] - data[i - 1]) % 256
    return bytes(out)


def prepare_progressive(input_path: str, tile_size: int = 10):
    """
    Encode an image into a two-phase progressive payload for
    instant preview + full-detail lossless reconstruction.

    Phase 1: Tiled averages  -> delta + zstd  (quick blocky preview)
    Phase 2: Pixel residuals -> delta + zstd  (lossless refinement)

    Wire format (payload after SIH1 + 'P' + total_len header):
        [tile_size:1B] [p1_w:2B] [p1_h:2B] [full_w:2B] [full_h:2B]
        [p1_data_len:4B] [p2_data_len:4B]
        [p1_zst_data...] [p2_zst_data...]
    """
    try:
        from PIL import Image
    except ImportError:
        print("[-] ERROR: Pillow is required.  pip install Pillow")
        sys.exit(1)
    try:
        import zstandard as zstd
    except ImportError:
        print("[-] ERROR: zstandard is required.  pip install zstandard")
        sys.exit(1)

    if not os.path.exists(input_path):
        print(f"[-] ERROR: File not found: {input_path}")
        sys.exit(1)

    img = Image.open(input_path).convert("L")
    orig_w, orig_h = img.size

    if tile_size <= 1:
        print("[!] WARNING: --progressive with --tile 1 gives no preview benefit.")
        print("    Consider --tile 10 for a useful preview.")

    # Crop to exact multiple of tile_size
    crop_w = (orig_w // tile_size) * tile_size
    crop_h = (orig_h // tile_size) * tile_size
    img = img.crop((0, 0, crop_w, crop_h))

    # Phase 1: tiled averages (the preview image)
    tw = crop_w // tile_size
    th = crop_h // tile_size
    tiled_img = img.resize((tw, th), Image.LANCZOS)
    tiled_pixels = bytes(tiled_img.getdata())

    # Phase 2: per-pixel residuals from tile averages
    full_pixels = list(img.getdata())
    full_w, full_h = crop_w, crop_h

    print(f"[*] Computing residuals ({full_w}x{full_h} pixels)...")
    residuals = bytearray(len(full_pixels))
    for y in range(full_h):
        for x in range(full_w):
            idx = y * full_w + x
            tx_idx = x // tile_size
            ty_idx = y // tile_size
            avg_val = tiled_pixels[ty_idx * tw + tx_idx]
            residuals[idx] = (full_pixels[idx] - avg_val + 128) % 256

    # Compress both phases: delta -> zstd level 19
    cctx = zstd.ZstdCompressor(level=19)
    p1_zst = cctx.compress(delta_encode(tiled_pixels))
    p2_zst = cctx.compress(delta_encode(bytes(residuals)))

    # Build progressive payload
    payload = bytearray()
    payload.append(tile_size)                          # 1B
    payload.extend(tw.to_bytes(2, 'big'))              # 2B  preview width
    payload.extend(th.to_bytes(2, 'big'))              # 2B  preview height
    payload.extend(full_w.to_bytes(2, 'big'))          # 2B  full width
    payload.extend(full_h.to_bytes(2, 'big'))          # 2B  full height
    payload.extend(len(p1_zst).to_bytes(4, 'big'))     # 4B  phase 1 size
    payload.extend(len(p2_zst).to_bytes(4, 'big'))     # 4B  phase 2 size
    payload.extend(p1_zst)                             # phase 1 data
    payload.extend(p2_zst)                             # phase 2 data

    print(f"[+] Progressive encoding complete (tile={tile_size}):")
    print(f"    Original:  {orig_w}x{orig_h}")
    print(f"    Phase 1 (preview {tw}x{th}): {len(p1_zst):,} bytes")
    print(f"    Phase 2 (detail {full_w}x{full_h}): {len(p2_zst):,} bytes")
    print(f"    Total payload: {len(payload):,} bytes")

    return b"P", bytes(payload), input_path


def auto_detect_com_port():
    """Find and return the first available serial COM port."""
    ports = list(serial.tools.list_ports.comports())
    if not ports:
        return None
    for p in ports:
        # Prefer known ESP / CH340 / CP210x ports if described
        desc = (p.description or "").lower()
        if "ch340" in desc or "cp210" in desc or "uart" in desc or "usb" in desc:
            return p.device
    return ports[0].device


def prepare_file(input_path: str, tile_size: int = 1):
    """
    Ensure we have a valid .bwdata.zst or .bwdata.bin payload.
    If given an image (.png, .jpg, etc.), automatically run tx_encoder.py.
    """
    if not os.path.exists(input_path):
        print(f"[-] ERROR: File not found: {input_path}")
        sys.exit(1)

    ext = os.path.splitext(input_path)[1].lower()

    if ext == ".zst":
        format_flag = b"Z"
        target_path = input_path
    elif ext == ".bin":
        format_flag = b"B"
        target_path = input_path
    elif ext == ".bwdata":
        format_flag = b"J"
        target_path = input_path
    else:
        # It's an image file (.png, .jpg, etc.) -> invoke tx_encoder.py to create .zst
        print(f"[*] Input is an image ({input_path}). Invoking tx_encoder.py (tile={tile_size})...")
        cmd = [sys.executable, "tx_encoder.py", input_path, "--tile", str(tile_size)]
        res = subprocess.run(cmd)
        if res.returncode != 0:
            print("[-] tx_encoder.py failed to encode image.")
            sys.exit(1)

        # Look for the generated .bwdata.zst
        base = os.path.splitext(input_path)[0]
        zst_candidate = f"{base}.bwdata.zst"
        bin_candidate = f"{base}.bwdata.bin"

        if os.path.exists(zst_candidate):
            target_path = zst_candidate
            format_flag = b"Z"
        elif os.path.exists(bin_candidate):
            target_path = bin_candidate
            format_flag = b"B"
        else:
            print("[-] Could not find generated encoded output file.")
            sys.exit(1)

    with open(target_path, "rb") as f:
        file_bytes = f.read()

    print(f"[+] Loaded '{target_path}' ({len(file_bytes):,} bytes, format={format_flag.decode()})")
    return format_flag, file_bytes, target_path


def send_data(port: str, baud: int, format_flag: bytes, file_bytes: bytes, chunk_size: int = 64):
    """Package file with header and stream to ESP32 TX with ACK flow control."""
    print(f"[*] Opening serial port {port} at {baud} baud...")
    try:
        ser = serial.Serial(port, baud, timeout=3.0)
    except serial.SerialException as e:
        print(f"[-] Failed to open serial port {port}: {e}")
        sys.exit(1)

    # Allow ESP32 to reset after opening serial port
    time.sleep(2.0)
    ser.reset_input_buffer()
    ser.reset_output_buffer()

    # Build transmission packet:
    # MAGIC (4B) + FORMAT (1B) + FILE_SIZE (4B uint32) + RAW_FILE_BYTES
    file_len = len(file_bytes)
    header = MAGIC_HEADER + format_flag + struct.pack(">I", file_len)
    full_payload = header + file_bytes
    total_len = len(full_payload)

    print(f"[*] Total bytes to transmit (including header): {total_len:,} bytes")
    print(f"[*] Chunk size: {chunk_size} bytes ({ (total_len + chunk_size - 1) // chunk_size } packets)")
    print("[*] Starting transmission over laser link...\n")

    start_time = time.time()
    sent_bytes = 0

    offset = 0
    packet_num = 0
    max_retries = 3

    while offset < total_len:
        chunk = full_payload[offset : offset + chunk_size]
        chunk_len = len(chunk)

        # Wire packet to ESP32: [LEN_BYTE] [CHUNK_DATA]
        wire_frame = bytes([chunk_len]) + chunk

        ack_received = False
        for attempt in range(max_retries):
            ser.write(wire_frame)
            ser.flush()

            # Wait for ACK from ESP32
            response = ser.read(1)
            if response and response[0] == ACK_BYTE:
                ack_received = True
                break
            else:
                print(f"\n[!] Timeout/NACK on packet {packet_num}, retrying ({attempt+1}/{max_retries})...")
                time.sleep(0.1)

        if not ack_received:
            print(f"\n[-] FAILED: No ACK received for packet {packet_num} after {max_retries} attempts.")
            ser.close()
            sys.exit(1)

        offset += chunk_len
        sent_bytes += chunk_len
        packet_num += 1

        # Progress bar
        elapsed = time.time() - start_time
        rate = sent_bytes / elapsed if elapsed > 0 else 0
        pct = (sent_bytes / total_len) * 100
        bar_len = 30
        filled = int(bar_len * sent_bytes // total_len)
        bar = "=" * filled + "-" * (bar_len - filled)
        sys.stdout.write(f"\r[{bar}] {pct:5.1f}% | {sent_bytes}/{total_len} B | {rate:.1f} B/s | Pkt #{packet_num}")
        sys.stdout.flush()

    total_time = time.time() - start_time
    avg_speed = (sent_bytes * 8) / (total_time * 1000) if total_time > 0 else 0
    print(f"\n\n[+] TRANSMISSION COMPLETE!")
    print(f"[+] Total time: {total_time:.2f} seconds")
    print(f"[+] Average speed: {avg_speed:.2f} Kbps")

    ser.close()


def main():
    parser = argparse.ArgumentParser(description="Laser Image Transmitter — PC Side Bridge")
    parser.add_argument("file", help="Path to image (.png/.jpg) or pre-encoded file (.bwdata.zst / .bwdata.bin)")
    parser.add_argument("--port", "-p", default=None, help="COM port for ESP32 TX (e.g., COM3, COM4). Auto-detected if omitted.")
    parser.add_argument("--baud", "-b", type=int, default=115200, help="Serial baud rate (default: 115200)")
    parser.add_argument("--chunk-size", "-c", type=int, default=64, help="Laser packet payload chunk size (default: 64 bytes)")
    parser.add_argument("--tile", "-t", type=int, default=1, help="Tile size for downsampling (e.g., --tile 10 for 10x reduction). Only used when input is an image.")
    parser.add_argument("--progressive", action="store_true",
                        help="Two-phase progressive TX: instant blocky preview first, "
                             "then full lossless detail. Requires image input and --tile > 1.")

    args = parser.parse_args()

    port = args.port or auto_detect_com_port()
    if not port:
        print("[-] No COM port specified and none detected automatically.")
        print("    Available ports:")
        for p in serial.tools.list_ports.comports():
            print(f"      {p.device} — {p.description}")
        print("\nSpecify port with: python what.py <file> --port COMx")
        sys.exit(1)

    if args.progressive:
        ext = os.path.splitext(args.file)[1].lower()
        if ext in ('.zst', '.bin', '.bwdata'):
            print("[-] --progressive requires an image file (.png, .jpg, etc.)")
            print("    Pre-encoded files cannot be progressively re-encoded.")
            sys.exit(1)
        format_flag, file_bytes, _ = prepare_progressive(args.file, tile_size=args.tile)
    else:
        format_flag, file_bytes, _ = prepare_file(args.file, tile_size=args.tile)

    send_data(port, args.baud, format_flag, file_bytes, chunk_size=args.chunk_size)


if __name__ == "__main__":
    main()