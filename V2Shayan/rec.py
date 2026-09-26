"""
rec.py — Receiver Side Python Bridge for ESP32 Laser Receiver
==============================================================
Listens on the serial COM port connected to the ESP32 receiver,
assembles incoming laser packets, verifies the stream header,
and reconstructs the image using rx_decoder.py.

Usage:
    python rec.py [--port COMx] [--baud 115200] [--output restored.png] [--show]

Example:
    python rec.py --port COM5
    python rec.py --port COM5 --show
"""

import os
import sys
import time
import struct
import argparse
from datetime import datetime

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    print("ERROR: pyserial is required. Install it using:")
    print("    pip install pyserial")
    sys.exit(1)

# Import decoder functions from the local rx_decoder module
try:
    from rx_decoder import decode_from_bytes, reconstruct_image
except ImportError:
    print("[-] ERROR: rx_decoder.py must be in the same folder.")
    sys.exit(1)


MAGIC_HEADER = b"SIH1"  # 4-byte header identifier
STALL_TIMEOUT = 30.0     # seconds with no new data before giving up


def delta_decode(data):
    """Reverse delta encoding: cumulative sum mod 256."""
    out = [0] * len(data)
    out[0] = data[0]
    for i in range(1, len(data)):
        out[i] = (out[i - 1] + data[i]) % 256
    return out


def receive_progressive(ser, file_data, file_len, start_time, default_output, show):
    """
    Handle two-phase progressive reception:
      Phase 1: Receive compressed averages -> instant preview
      Phase 2: Receive compressed residuals -> full lossless image

    Progressive sub-header (17 bytes at start of payload):
        [tile_size:1B] [p1_w:2B] [p1_h:2B] [full_w:2B] [full_h:2B]
        [p1_data_len:4B] [p2_data_len:4B]
    """
    try:
        import zstandard as zstd
    except ImportError:
        print("[-] ERROR: zstandard required for progressive decoding.")
        sys.exit(1)
    try:
        from PIL import Image
    except ImportError:
        print("[-] ERROR: Pillow required for progressive decoding.")
        sys.exit(1)

    SUB_HEADER_SIZE = 17  # 1 + 2+2 + 2+2 + 4+4

    def collect_until(target_len):
        """Read serial until file_data has at least target_len bytes."""
        last_print = 0
        last_data_time = time.time()
        while len(file_data) < target_len:
            remaining = target_len - len(file_data)
            to_read = min(remaining, ser.in_waiting or 1)
            chunk = ser.read(to_read)
            if chunk:
                file_data.extend(chunk)
                last_data_time = time.time()
            elif time.time() - last_data_time > STALL_TIMEOUT:
                print(f"\n[!] TIMEOUT: No data received for {STALL_TIMEOUT}s.")
                print(f"    Got {len(file_data)}/{target_len} bytes ({len(file_data)/target_len*100:.1f}%)")
                print(f"    Possible cause: laser misalignment or too many dropped packets.")
                return False
            now = time.time()
            if now - last_print > 0.1 or len(file_data) >= target_len:
                last_print = now
                elapsed = now - start_time
                rate = len(file_data) / elapsed if elapsed > 0 else 0
                pct = (len(file_data) / file_len) * 100
                filled = int(30 * len(file_data) // file_len)
                bar = "=" * filled + "-" * (30 - filled)
                sys.stdout.write(
                    f"\r[{bar}] {pct:5.1f}% | "
                    f"{len(file_data):,}/{file_len:,} B | {rate:.1f} B/s"
                )
                sys.stdout.flush()
        return True

    # ── Collect and parse the progressive sub-header ──
    if not collect_until(SUB_HEADER_SIZE):
        print("[!] Failed to receive progressive sub-header. Aborting.")
        return

    tile_size = file_data[0]
    p1_w = int.from_bytes(file_data[1:3], 'big')
    p1_h = int.from_bytes(file_data[3:5], 'big')
    full_w = int.from_bytes(file_data[5:7], 'big')
    full_h = int.from_bytes(file_data[7:9], 'big')
    p1_len = int.from_bytes(file_data[9:13], 'big')
    p2_len = int.from_bytes(file_data[13:17], 'big')

    print(f"\n    Progressive mode (tile={tile_size})")
    print(f"    Phase 1: {p1_w}x{p1_h} preview ({p1_len:,} bytes compressed)")
    print(f"    Phase 2: {full_w}x{full_h} full detail ({p2_len:,} bytes compressed)")
    print(f"[*] Receiving Phase 1 (preview)...")

    # ── Phase 1: receive averages, reconstruct preview immediately ──
    p1_end = SUB_HEADER_SIZE + p1_len
    if not collect_until(p1_end):
        print("[!] Phase 1 reception timed out. Aborting.")
        return

    p1_time = time.time() - start_time
    print(f"\n[+] Phase 1 received in {p1_time:.1f}s -- reconstructing preview...")

    dctx = zstd.ZstdDecompressor()
    p1_zst = bytes(file_data[SUB_HEADER_SIZE:p1_end])
    p1_delta = dctx.decompress(p1_zst)
    p1_pixels = delta_decode(p1_delta)

    preview_img = Image.new("L", (p1_w, p1_h))
    preview_img.putdata(p1_pixels)
    upscaled = preview_img.resize((full_w, full_h), Image.NEAREST)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    final_path = default_output or f"restored_{timestamp}.png"
    preview_save = final_path.replace('.png', '_preview.png')

    upscaled.save(preview_save)
    print(f"[*] PREVIEW saved: {preview_save} ({p1_w}x{p1_h} upscaled to {full_w}x{full_h})")
    if show:
        upscaled.show()

    # ── Phase 2: receive residuals, reconstruct full lossless image ──
    print(f"\n[*] Receiving Phase 2 (full detail)...")
    p2_end = p1_end + p2_len
    if not collect_until(p2_end):
        print("[!] Phase 2 reception timed out.")
        print("    Preview image was saved. Full quality reconstruction skipped.")
        return

    total_time = time.time() - start_time
    avg_speed = (len(file_data) * 8) / (total_time * 1000) if total_time > 0 else 0
    print(f"\n\n[+] RECEPTION COMPLETE!")
    print(f"[+] Received {len(file_data):,} bytes in {total_time:.2f}s ({avg_speed:.2f} Kbps)")

    print("[*] Reconstructing full-quality image...")
    p2_zst = bytes(file_data[p1_end:p2_end])
    p2_delta = dctx.decompress(p2_zst)
    p2_residuals = delta_decode(p2_delta)

    # Lossless reconstruction: pixel = (residual - 128 + avg) mod 256
    full_pixels = [0] * (full_w * full_h)
    for y in range(full_h):
        for x in range(full_w):
            idx = y * full_w + x
            tx_idx = x // tile_size
            ty_idx = y // tile_size
            avg_val = p1_pixels[ty_idx * p1_w + tx_idx]
            full_pixels[idx] = (p2_residuals[idx] - 128 + avg_val) % 256

    full_img = Image.new("L", (full_w, full_h))
    full_img.putdata(full_pixels)
    full_img.save(final_path)
    print(f"[+] Full quality {full_w}x{full_h} image saved: {final_path}")
    if show:
        full_img.show()

    # Save raw progressive data for debugging
    raw_filename = f"received_{timestamp}_progressive.bin"
    with open(raw_filename, "wb") as f:
        f.write(file_data)
    print(f"[+] Raw data saved: {raw_filename}")
    print(f"\n[*] Ready for next transmission...\n")


def auto_detect_com_port():
    """Find and return the first available serial COM port."""
    ports = list(serial.tools.list_ports.comports())
    if not ports:
        return None
    for p in ports:
        desc = (p.description or "").lower()
        if "ch340" in desc or "cp210" in desc or "uart" in desc or "usb" in desc:
            return p.device
    return ports[0].device


def receive_loop(port: str, baud: int, default_output: str = None, show: bool = False):
    """Main reception loop: listens for packets, builds file, and reconstructs image."""
    print(f"[*] Opening serial port {port} at {baud} baud...")
    try:
        ser = serial.Serial(port, baud, timeout=1.0)
    except serial.SerialException as e:
        print(f"[-] Failed to open serial port {port}: {e}")
        sys.exit(1)

    ser.reset_input_buffer()
    print("[*] Waiting for laser transmission from ESP32 RX...")
    print("    (Align laser with BPW34 sensor. Press Ctrl+C to stop.)\n")

    stream_buffer = bytearray()

    while True:
        try:
            # Read whatever bytes are available from ESP32
            raw = ser.read(ser.in_waiting or 1)
            if not raw:
                continue

            stream_buffer.extend(raw)

            # Search for magic header
            magic_idx = stream_buffer.find(MAGIC_HEADER)
            if magic_idx == -1:
                # Keep only last 3 bytes in case magic is split across chunks
                if len(stream_buffer) > 3:
                    stream_buffer = stream_buffer[-3:]
                continue

            # Check if we have received the full 9-byte header:
            # MAGIC (4B) + FORMAT (1B) + FILE_LEN (4B)
            if len(stream_buffer) < magic_idx + 9:
                continue

            # Header is present!
            header_start = magic_idx
            format_flag = chr(stream_buffer[header_start + 4])
            file_len = struct.unpack(">I", stream_buffer[header_start + 5 : header_start + 9])[0]

            print(f"\n[+] Detected incoming file transmission!")
            print(f"    Format flag: '{format_flag}'")
            print(f"    Payload size: {file_len:,} bytes")

            payload_start = header_start + 9
            file_data = bytearray(stream_buffer[payload_start:])
            stream_buffer.clear()

            start_time = time.time()

            # Progressive mode: two-phase reception with instant preview
            if format_flag == 'P':
                receive_progressive(ser, file_data, file_len, start_time,
                                    default_output, show)
                continue

            last_print = 0
            last_data_time = time.time()
            stalled = False

            # Collect the rest of the file
            while len(file_data) < file_len:
                remaining = file_len - len(file_data)
                to_read = min(remaining, ser.in_waiting or 1)
                chunk = ser.read(to_read)
                if chunk:
                    file_data.extend(chunk)
                    last_data_time = time.time()
                elif time.time() - last_data_time > STALL_TIMEOUT:
                    print(f"\n[!] TIMEOUT: No data received for {STALL_TIMEOUT}s.")
                    print(f"    Got {len(file_data)}/{file_len} bytes ({len(file_data)/file_len*100:.1f}%)")
                    print(f"    Possible cause: laser misalignment or too many dropped packets.")
                    stalled = True
                    break

                # Progress display
                now = time.time()
                if now - last_print > 0.1 or len(file_data) >= file_len:
                    last_print = now
                    elapsed = now - start_time
                    rate = len(file_data) / elapsed if elapsed > 0 else 0
                    pct = (len(file_data) / file_len) * 100
                    bar_len = 30
                    filled = int(bar_len * len(file_data) // file_len)
                    bar = "=" * filled + "-" * (bar_len - filled)
                    sys.stdout.write(f"\r[{bar}] {pct:5.1f}% | {len(file_data):,}/{file_len:,} B | {rate:.1f} B/s")
                    sys.stdout.flush()

            if stalled:
                print("[*] Returning to listen mode...\n")
                continue

            total_time = time.time() - start_time
            avg_speed = (len(file_data) * 8) / (total_time * 1000) if total_time > 0 else 0
            print(f"\n\n[+] RECEPTION COMPLETE!")
            print(f"[+] Received {len(file_data):,} bytes in {total_time:.2f}s ({avg_speed:.2f} Kbps)")

            # Save raw file
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            ext = ".bwdata.zst" if format_flag == "Z" else (".bwdata.bin" if format_flag == "B" else ".bwdata")
            raw_filename = f"received_{timestamp}{ext}"

            with open(raw_filename, "wb") as f:
                f.write(file_data)
            print(f"[+] Saved raw received data to '{raw_filename}'")

            # Reconstruct image
            try:
                print("[*] Reconstructing image...")
                w, h, values, hdr = decode_from_bytes(bytes(file_data), raw_filename)
                img = reconstruct_image(w, h, values)

                out_png = default_output or f"restored_{timestamp}.png"
                img.save(out_png)
                print(f"[✓] SUCCESS! Reconstructed {w}x{h} image saved to: {out_png}")

                if show:
                    img.show()

            except Exception as e:
                print(f"[-] Image reconstruction failed: {e}")
                print(f"    Raw bytes are preserved in '{raw_filename}' for manual recovery.")

            print("\n[*] Ready for next transmission...\n")

        except KeyboardInterrupt:
            print("\n[*] Exiting receiver loop.")
            break
        except Exception as e:
            print(f"\n[!] Error in receive loop: {e}")
            time.sleep(1.0)

    ser.close()


def main():
    parser = argparse.ArgumentParser(description="Laser Image Receiver — PC Side Bridge")
    parser.add_argument("--port", "-p", default="COM6", help="COM port for ESP32 RX (default: COM6)")
    parser.add_argument("--baud", "-b", type=int, default=115200, help="Serial baud rate (default: 115200)")
    parser.add_argument("--output", "-o", default=None, help="Output image file path (default: restored_<timestamp>.png)")
    parser.add_argument("--show", action="store_true", help="Automatically open image after reconstruction")

    args = parser.parse_args()

    port = args.port or auto_detect_com_port()
    if not port:
        print("[-] No COM port specified and none detected automatically.")
        print("    Available ports:")
        for p in serial.tools.list_ports.comports():
            print(f"      {p.device} — {p.description}")
        print("\nSpecify port with: python rec.py --port COMx")
        sys.exit(1)

    receive_loop(port, args.baud, default_output=args.output, show=args.show)


if __name__ == "__main__":
    main()