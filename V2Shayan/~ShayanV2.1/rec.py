"""
rec.py — Receiver Side Python Bridge for ESP32 Laser Receiver
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
    print("ERROR: pyserial is required. Install it using: pip install pyserial")
    sys.exit(1)

try:
    from rx_decoder import decode_from_bytes, reconstruct_image
except ImportError:
    print("[-] ERROR: rx_decoder.py must be in the same folder.")
    sys.exit(1)

MAGIC_HEADER = b"SIH1"

def auto_detect_com_port():
    ports = list(serial.tools.list_ports.comports())
    if not ports: return None
    for p in ports:
        desc = (p.description or "").lower()
        if "ch340" in desc or "cp210" in desc or "uart" in desc or "usb" in desc:
            return p.device
    return ports[0].device

STALL_TIMEOUT = 30.0  # seconds with no new data before giving up


def receive_loop(port: str, baud: int, default_output: str = None, show: bool = False):
    print(f"[*] Opening serial port {port} at {baud} baud...")
    try:
        ser = serial.Serial(port, baud, timeout=1.0)
    except serial.SerialException as e:
        print(f"[-] Failed to open serial port {port}: {e}")
        sys.exit(1)

    ser.reset_input_buffer()
    print("[*] Waiting for laser transmission from ESP32 RX...")
    print("    (Align laser with sensor. Press Ctrl+C to stop.)\n")

    stream_buffer = bytearray()
    total_bytes_seen = 0
    last_heartbeat = time.time()

    while True:
        try:
            raw = ser.read(ser.in_waiting or 1)
            if not raw:
                # Heartbeat: print diagnostic every 5s so "deaf" isn't silent
                if time.time() - last_heartbeat > 5.0:
                    last_heartbeat = time.time()
                    if total_bytes_seen == 0:
                        sys.stdout.write("\r[...] No bytes from ESP32 yet — check laser alignment & wiring")
                    else:
                        sys.stdout.write(f"\r[...] {total_bytes_seen:,} bytes received so far, searching for SIH1 header...")
                    sys.stdout.flush()
                continue

            total_bytes_seen += len(raw)
            stream_buffer.extend(raw)

            magic_idx = stream_buffer.find(MAGIC_HEADER)
            if magic_idx == -1:
                if len(stream_buffer) > 1024:
                    stream_buffer = bytearray(stream_buffer[-3:])
                continue

            if len(stream_buffer) < magic_idx + 9:
                continue

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
            last_data_time = time.time()

            while len(file_data) < file_len:
                remaining = file_len - len(file_data)
                to_read = min(remaining, ser.in_waiting or 1)
                chunk = ser.read(to_read)
                if chunk:
                    file_data.extend(chunk)
                    last_data_time = time.time()
                elif time.time() - last_data_time > STALL_TIMEOUT:
                    missing = file_len - len(file_data)
                    print(f"\n[!] TIMEOUT: No data for {STALL_TIMEOUT}s. Missing {missing:,} bytes.")
                    print(f"    Got {len(file_data):,}/{file_len:,} bytes ({len(file_data)/file_len*100:.1f}%)")
                    print(f"    Possible cause: laser misalignment or too many dropped packets.")
                    break

                now = time.time()
                elapsed = now - start_time
                rate = len(file_data) / elapsed if elapsed > 0 else 0
                pct = (len(file_data) / file_len) * 100
                filled = int(30 * len(file_data) // file_len)
                bar = "=" * filled + "-" * (30 - filled)
                sys.stdout.write(f"\r[{bar}] {pct:5.1f}% | {len(file_data):,}/{file_len:,} B | {rate:.1f} B/s")
                sys.stdout.flush()

            total_time = time.time() - start_time
            avg_speed = (len(file_data) * 8) / (total_time * 1000) if total_time > 0 else 0
            print(f"\n\n[+] RECEPTION COMPLETE!")
            print(f"[+] Received {len(file_data):,} bytes in {total_time:.2f}s ({avg_speed:.2f} Kbps)")

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            
            if format_flag == "1":
                w = int.from_bytes(file_data[0:2], "big")
                h = int.from_bytes(file_data[2:4], "big")
                packed_bits = file_data[4:]
                
                unpacked_pixels = []
                for byte in packed_bits:
                    for i in range(7, -1, -1):
                        bit = (byte >> i) & 0x01
                        unpacked_pixels.append(0 if bit == 1 else 255)
                
                unpacked_pixels = unpacked_pixels[:w * h]
                from PIL import Image
                img = Image.new("L", (w, h))
                img.putdata(unpacked_pixels)
                
                out_png = default_output or f"restored_1bit_{timestamp}.png"
                img.save(out_png)
                print(f"\n[✓] SUCCESS! 1-bit packed image unpacked & saved to: {out_png} ({w}x{h})")
                if show: img.show()
                print("\n[*] Ready for next transmission...\n")
                continue

            elif format_flag == "N":
                out_png = default_output or f"restored_native_{timestamp}.png"
                with open(out_png, "wb") as f: f.write(file_data)
                print(f"\n[✓] SUCCESS! Native image saved directly to: {out_png}")
                if show:
                    try:
                        from PIL import Image
                        Image.open(out_png).show()
                    except Exception as e: print(f"[-] Could not open image viewer: {e}")
                print("\n[*] Ready for next transmission...\n")
                continue
            else:
                ext = ".bwdata.zst" if format_flag == "Z" else (".bwdata.bin" if format_flag == "B" else ".bwdata")
                raw_filename = f"received_{timestamp}{ext}"
                with open(raw_filename, "wb") as f: f.write(file_data)
                try:
                    w, h, values, hdr = decode_from_bytes(bytes(file_data), raw_filename)
                    img = reconstruct_image(w, h, values)
                    out_png = default_output or f"restored_{timestamp}.png"
                    img.save(out_png)
                    print(f"[✓] SUCCESS! Reconstructed {w}x{h} image saved to: {out_png}")
                    if show: img.show()
                except Exception as e:
                    print(f"[-] Image reconstruction failed (expected if laser was blocked): {e}")
                print("\n[*] Ready for next transmission...\n")

        except KeyboardInterrupt:
            print("\n[*] Exiting receiver loop.")
            break
        except Exception as e:
            print(f"\n[!] Error in receive loop: {e}")
            time.sleep(1.0)

    ser.close()

def main():
    parser = argparse.ArgumentParser(description="Laser Image Receiver")
    parser.add_argument("--port", "-p", default=None, help="COM port for ESP32 RX")
    parser.add_argument("--baud", "-b", type=int, default=115200, help="Serial baud rate")
    parser.add_argument("--output", "-o", default=None, help="Output image file path")
    parser.add_argument("--show", action="store_true", help="Automatically open image")
    args = parser.parse_args()

    port = args.port or auto_detect_com_port()
    if not port:
        print("[-] No COM port specified and none detected automatically.")
        sys.exit(1)

    receive_loop(port, args.baud, default_output=args.output, show=args.show)

if __name__ == "__main__":
    main()