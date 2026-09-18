"""
RX Decoder — Pixel Data to Image Reconstructor
=================================================
Reconstructs an image from a .bwdata (JSON) or .bwdata.bin (binary) file
produced by tx_encoder.py.

Two modes:
  1. CLI mode   — directly reconstruct from a file on disk
  2. Web mode   — launches a Flask debug server with drag-and-drop upload

Usage:
    python rx_decoder.py <file.bwdata>              # CLI: reconstruct
    python rx_decoder.py <file.bwdata.bin>           # CLI: from binary
    python rx_decoder.py --web                       # launch debug web UI
    python rx_decoder.py --web --port 8080           # custom port

Options:
    --output, -o   Output image path (default: reconstructed_<timestamp>.png)
    --web          Launch the Flask debug upload server
    --port         Port for the web server (default: 5000)
    --show         Open the image after reconstruction (CLI mode)
"""

import argparse
import json
import os
import sys
import struct
import time
import io
import base64

try:
    from PIL import Image
except ImportError:
    print("ERROR: Pillow is required.  Install it with:  pip install Pillow")
    sys.exit(1)


# ── Core reconstruction ─────────────────────────────────────────────────────

def decode_json(file_path: str):
    """Decode a .bwdata JSON file and return (width, height, pixel_values)."""
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    hdr = data["header"]
    w, h = hdr["encoded_width"], hdr["encoded_height"]
    values = [p["v"] for p in data["pixels"]]
    return w, h, values, hdr


def decode_binary(file_path: str):
    """Decode a .bwdata.bin binary file and return (width, height, pixel_values)."""
    with open(file_path, "rb") as f:
        raw = f.read()
    w = int.from_bytes(raw[0:2], "big")
    h = int.from_bytes(raw[2:4], "big")
    values = list(raw[4:])
    hdr = {"encoded_width": w, "encoded_height": h, "total_pixels": w * h,
           "source": os.path.basename(file_path)}
    return w, h, values, hdr


def decode_from_bytes(raw_bytes: bytes, filename: str):
    """Decode from raw bytes (used by the web upload handler)."""
    if filename.endswith(".bin"):
        w = int.from_bytes(raw_bytes[0:2], "big")
        h = int.from_bytes(raw_bytes[2:4], "big")
        values = list(raw_bytes[4:])
        hdr = {"encoded_width": w, "encoded_height": h,
               "total_pixels": w * h, "source": filename}
    else:
        data = json.loads(raw_bytes.decode("utf-8"))
        hdr = data["header"]
        w, h = hdr["encoded_width"], hdr["encoded_height"]
        values = [p["v"] for p in data["pixels"]]
    return w, h, values, hdr


def reconstruct_image(w: int, h: int, values: list):
    """Build a PIL Image from width, height, and flat pixel-value list."""
    if len(values) != w * h:
        print(f"WARNING: Expected {w * h} pixels but got {len(values)}. "
              f"Padding/truncating.")
        if len(values) < w * h:
            values.extend([0] * (w * h - len(values)))
        else:
            values = values[:w * h]

    img = Image.new("L", (w, h))
    img.putdata(values)
    return img


def image_to_base64_png(img: Image.Image) -> str:
    """Convert a PIL image to a base64-encoded PNG string for web display."""
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


# ── CLI reconstruction ───────────────────────────────────────────────────────

def cli_reconstruct(file_path: str, output_path: str = None, show: bool = False):
    """Reconstruct image from a .bwdata or .bwdata.bin file."""
    if not os.path.isfile(file_path):
        print(f"ERROR: File not found: {file_path}")
        sys.exit(1)

    print("=" * 60)
    print("  RX DECODER -- Pixel Data -> Image")
    print("=" * 60)
    print(f"  Input file  : {file_path}")

    if file_path.endswith(".bin"):
        w, h, values, hdr = decode_binary(file_path)
    else:
        w, h, values, hdr = decode_json(file_path)

    print(f"  Resolution  : {w}×{h}")
    print(f"  Total pixels: {len(values):,}")

    # shade stats
    blacks = sum(1 for v in values if v < 50)
    whites = sum(1 for v in values if v > 200)
    mids = len(values) - blacks - whites
    print(f"  Dark pixels : {blacks:,}  ({blacks / len(values) * 100:.1f}%)")
    print(f"  Mid pixels  : {mids:,}  ({mids / len(values) * 100:.1f}%)")
    print(f"  Light pixels: {whites:,}  ({whites / len(values) * 100:.1f}%)")

    img = reconstruct_image(w, h, values)

    if output_path is None:
        ts = time.strftime("%Y%m%d_%H%M%S")
        output_path = f"reconstructed_{ts}.png"

    img.save(output_path)
    size_kb = os.path.getsize(output_path) / 1024
    print(f"\n  Output image: {output_path}  ({size_kb:,.1f} KB)")
    print("=" * 60)
    print("  [OK] Reconstruction complete")
    print("=" * 60)

    if show:
        img.show()

    return img


# ── Flask Debug Web Server ───────────────────────────────────────────────────

WEB_HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>RX Decoder — Debug Upload</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
  *{margin:0;padding:0;box-sizing:border-box}
  :root{
    --bg:#0a0a0f;--surface:#12121a;--surface2:#1a1a28;
    --border:#2a2a3a;--text:#e0e0e8;--text2:#8888a0;
    --accent:#6c5ce7;--accent2:#a29bfe;--success:#00b894;
    --danger:#ff6b6b;--glow:rgba(108,92,231,0.3);
  }
  body{
    font-family:'Inter',sans-serif;background:var(--bg);color:var(--text);
    min-height:100vh;display:flex;flex-direction:column;align-items:center;
    padding:2rem;
  }
  h1{
    font-size:1.8rem;font-weight:700;margin-bottom:.3rem;
    background:linear-gradient(135deg,var(--accent2),var(--accent),#fd79a8);
    -webkit-background-clip:text;-webkit-text-fill-color:transparent;
  }
  .subtitle{color:var(--text2);font-size:.85rem;margin-bottom:2rem}
  .card{
    background:var(--surface);border:1px solid var(--border);border-radius:16px;
    padding:2rem;width:100%;max-width:720px;margin-bottom:1.5rem;
    transition:border-color .3s;
  }
  .card:hover{border-color:var(--accent)}
  .card h2{font-size:1.1rem;margin-bottom:1rem;color:var(--accent2)}

  /* drop zone */
  .dropzone{
    border:2px dashed var(--border);border-radius:12px;padding:3rem 1.5rem;
    text-align:center;cursor:pointer;transition:all .3s;position:relative;
    background:var(--surface2);
  }
  .dropzone.drag-over{
    border-color:var(--accent);background:rgba(108,92,231,.08);
    box-shadow:0 0 30px var(--glow);
  }
  .dropzone p{color:var(--text2);font-size:.95rem}
  .dropzone .icon{font-size:2.5rem;margin-bottom:.8rem;display:block}
  .dropzone input[type=file]{
    position:absolute;inset:0;opacity:0;cursor:pointer;
  }

  /* button */
  .btn{
    display:inline-flex;align-items:center;gap:.5rem;
    background:linear-gradient(135deg,var(--accent),#5a4bd1);
    color:#fff;border:none;padding:.7rem 1.6rem;border-radius:10px;
    font-size:.9rem;font-weight:600;cursor:pointer;transition:all .25s;
    margin-top:1rem;font-family:'Inter',sans-serif;
  }
  .btn:hover{transform:translateY(-2px);box-shadow:0 6px 20px var(--glow)}
  .btn:disabled{opacity:.5;cursor:not-allowed;transform:none;box-shadow:none}

  /* result area */
  .result{margin-top:1.5rem;display:none}
  .result.visible{display:block}
  .result img{
    max-width:100%;border-radius:10px;border:1px solid var(--border);
    margin-top:1rem;image-rendering:pixelated;
  }
  .meta-grid{
    display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));
    gap:.8rem;margin-top:1rem;
  }
  .meta-item{
    background:var(--surface2);border-radius:8px;padding:.8rem 1rem;
    border:1px solid var(--border);
  }
  .meta-item .label{font-size:.7rem;color:var(--text2);text-transform:uppercase;
    letter-spacing:.05em;margin-bottom:.2rem}
  .meta-item .value{font-family:'JetBrains Mono',monospace;font-size:1rem;
    font-weight:600;color:var(--accent2)}

  .status{
    margin-top:1rem;padding:.8rem 1rem;border-radius:8px;font-size:.85rem;
    font-family:'JetBrains Mono',monospace;
  }
  .status.error{background:rgba(255,107,107,.1);color:var(--danger);
    border:1px solid rgba(255,107,107,.2)}
  .status.success{background:rgba(0,184,148,.1);color:var(--success);
    border:1px solid rgba(0,184,148,.2)}

  .spinner{display:none;margin-top:1rem;text-align:center;color:var(--text2)}
  .spinner.visible{display:block}
  @keyframes pulse{0%,100%{opacity:.4}50%{opacity:1}}
  .spinner span{animation:pulse 1.2s ease-in-out infinite}

  footer{margin-top:auto;padding-top:2rem;color:var(--text2);font-size:.75rem}
</style>
</head>
<body>

<h1>📡 RX Decoder — Debug Upload</h1>
<p class="subtitle">Upload a <code>.bwdata</code> or <code>.bwdata.bin</code> file to reconstruct the image</p>

<div class="card">
  <h2>⬆️ Upload Pixel Data</h2>
  <form id="uploadForm">
    <div class="dropzone" id="dropzone">
      <span class="icon">📂</span>
      <p>Drag & drop your <strong>.bwdata</strong> file here<br>or click to browse</p>
      <input type="file" id="fileInput" accept=".bwdata,.bin">
    </div>
    <div id="fileName" style="margin-top:.8rem;color:var(--accent2);font-family:'JetBrains Mono',monospace;font-size:.85rem"></div>
    <button type="submit" class="btn" id="submitBtn" disabled>
      🔄 Reconstruct Image
    </button>
  </form>
  <div class="spinner" id="spinner"><span>⏳ Reconstructing…</span></div>
  <div id="statusBox"></div>
</div>

<div class="card result" id="resultCard">
  <h2>🖼️ Reconstructed Image</h2>
  <div class="meta-grid" id="metaGrid"></div>
  <img id="resultImg" alt="Reconstructed image">
  <a id="downloadLink" class="btn" style="text-decoration:none;margin-top:1.2rem" download>
    💾 Download PNG
  </a>
</div>

<footer>SIH SWside — RX Decoder Debug Tool</footer>

<script>
const dropzone=document.getElementById('dropzone'),
      fileInput=document.getElementById('fileInput'),
      fileName=document.getElementById('fileName'),
      form=document.getElementById('uploadForm'),
      submitBtn=document.getElementById('submitBtn'),
      spinner=document.getElementById('spinner'),
      statusBox=document.getElementById('statusBox'),
      resultCard=document.getElementById('resultCard'),
      metaGrid=document.getElementById('metaGrid'),
      resultImg=document.getElementById('resultImg'),
      downloadLink=document.getElementById('downloadLink');

let selectedFile=null;

// drag & drop
['dragenter','dragover'].forEach(e=>dropzone.addEventListener(e,ev=>{
  ev.preventDefault();dropzone.classList.add('drag-over')}));
['dragleave','drop'].forEach(e=>dropzone.addEventListener(e,ev=>{
  ev.preventDefault();dropzone.classList.remove('drag-over')}));
dropzone.addEventListener('drop',e=>{
  if(e.dataTransfer.files.length){selectedFile=e.dataTransfer.files[0];updateFile()}});
fileInput.addEventListener('change',()=>{selectedFile=fileInput.files[0];updateFile()});

function updateFile(){
  if(selectedFile){
    fileName.textContent='📎 '+selectedFile.name+' ('+fmtSize(selectedFile.size)+')';
    submitBtn.disabled=false;
  }
}
function fmtSize(b){
  if(b<1024)return b+' B';
  if(b<1048576)return (b/1024).toFixed(1)+' KB';
  return (b/1048576).toFixed(1)+' MB';
}

form.addEventListener('submit',async e=>{
  e.preventDefault();
  if(!selectedFile)return;
  submitBtn.disabled=true;
  spinner.classList.add('visible');
  statusBox.innerHTML='';
  resultCard.classList.remove('visible');

  const fd=new FormData();
  fd.append('file',selectedFile);
  try{
    const res=await fetch('/api/decode',{method:'POST',body:fd});
    const data=await res.json();
    spinner.classList.remove('visible');
    if(data.error){
      statusBox.innerHTML='<div class="status error">❌ '+data.error+'</div>';
      submitBtn.disabled=false;return;
    }
    statusBox.innerHTML='<div class="status success">✔ Reconstruction successful</div>';
    // meta
    metaGrid.innerHTML='';
    const items=[
      {label:'Width',value:data.width+'px'},
      {label:'Height',value:data.height+'px'},
      {label:'Pixels',value:data.total_pixels.toLocaleString()},
      {label:'Dark',value:data.dark_pct+'%'},
      {label:'Mid',value:data.mid_pct+'%'},
      {label:'Light',value:data.light_pct+'%'},
    ];
    items.forEach(it=>{
      metaGrid.innerHTML+=`<div class="meta-item"><div class="label">${it.label}</div><div class="value">${it.value}</div></div>`;
    });
    resultImg.src='data:image/png;base64,'+data.image_b64;
    downloadLink.href=resultImg.src;
    downloadLink.download='reconstructed_'+Date.now()+'.png';
    resultCard.classList.add('visible');
  }catch(err){
    spinner.classList.remove('visible');
    statusBox.innerHTML='<div class="status error">❌ Network error: '+err.message+'</div>';
  }
  submitBtn.disabled=false;
});
</script>
</body>
</html>
"""


def run_web_server(port: int = 5000):
    """Launch the Flask debug web server."""
    try:
        from flask import Flask, request, jsonify, render_template_string
    except ImportError:
        print("ERROR: Flask is required for web mode.  Install with:  pip install flask")
        sys.exit(1)

    app = Flask(__name__)

    @app.route("/")
    def index():
        return render_template_string(WEB_HTML)

    @app.route("/api/decode", methods=["POST"])
    def api_decode():
        if "file" not in request.files:
            return jsonify({"error": "No file uploaded"}), 400
        f = request.files["file"]
        if not f.filename:
            return jsonify({"error": "Empty filename"}), 400

        try:
            raw = f.read()
            w, h, values, hdr = decode_from_bytes(raw, f.filename)
            img = reconstruct_image(w, h, values)
            b64 = image_to_base64_png(img)

            dark = sum(1 for v in values if v < 50)
            light = sum(1 for v in values if v > 200)
            mid = len(values) - dark - light
            total = len(values) or 1

            return jsonify({
                "width": w,
                "height": h,
                "total_pixels": w * h,
                "dark_pct": round(dark / total * 100, 1),
                "mid_pct": round(mid / total * 100, 1),
                "light_pct": round(light / total * 100, 1),
                "image_b64": b64,
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    print("=" * 60)
    print("  RX DECODER -- Debug Web Server")
    print("=" * 60)
    print(f"  URL: http://localhost:{port}")
    print(f"  Upload .bwdata / .bwdata.bin files to reconstruct images")
    print("  Press Ctrl+C to stop")
    print("=" * 60)
    app.run(host="0.0.0.0", port=port, debug=False)


# ── CLI entry point ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="RX Decoder — reconstruct images from .bwdata pixel files"
    )
    parser.add_argument(
        "file", nargs="?", default=None,
        help="Path to .bwdata or .bwdata.bin file (CLI mode)"
    )
    parser.add_argument(
        "-o", "--output", default=None,
        help="Output image path (default: reconstructed_<timestamp>.png)"
    )
    parser.add_argument(
        "--show", action="store_true",
        help="Open the reconstructed image after saving"
    )
    parser.add_argument(
        "--web", action="store_true",
        help="Launch the Flask debug upload web server"
    )
    parser.add_argument(
        "--port", type=int, default=5000,
        help="Port for the web server (default: 5000)"
    )
    args = parser.parse_args()

    if args.web:
        run_web_server(port=args.port)
    elif args.file:
        cli_reconstruct(args.file, output_path=args.output, show=args.show)
    else:
        parser.print_help()
        print("\nTip: Use --web to launch the debug upload server")


if __name__ == "__main__":
    main()
