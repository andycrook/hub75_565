# bdf_hfm_editor.py
# Full editor + exporter for BDF/HFM fonts

import os
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, ttk
from tkinter import scrolledtext
from typing import Any


BUY_ME_A_COFFEE_URL = "https://www.buymeacoffee.com/andycrook"
BUY_ME_A_COFFEE_LABEL = "Buy me a coffee"

HFM_ABOUT_TEXT = """HFM format reference

Purpose
HFM is a compact bitmap font container for use on LED matrix panels. It stores only the glyph data needed for fast loading and rendering on constrained targets. It does not store kerning tables, ligatures, OpenType shaping data, or other advanced layout information.

Byte order
- All multi-byte integer fields are little-endian.
- Single-byte signed offsets are stored as raw bytes and should be decoded as signed 8-bit values.

File layout
Offset  Size  Field
0       4     Magic ASCII bytes: HFM1
4       1     Font ascent (unsigned byte)
5       1     Font descent (unsigned byte)
6       2     Glyph count (uint16 little-endian)
8       ...   Repeated glyph records

Glyph record layout
Each glyph record is stored back-to-back with no padding:

Offset  Size  Field
0       2     Codepoint (uint16 little-endian)
2       1     Glyph width in pixels
3       1     Glyph height in pixels
4       1     x offset, stored as signed 8-bit in a byte
5       1     y offset, stored as signed 8-bit in a byte
6       1     Bitmap payload length in bytes
7       N     Bitmap payload bytes

Signed offsets
The xoff and yoff fields are stored as one byte each.
- Values 0..127 mean 0..127
- Values 128..255 mean -128..-1 after decoding

Pseudo-code for decoding a signed offset:
if raw >= 128:
    signed = raw - 256
else:
    signed = raw

Bitmap packing
- Bitmap rows are stored top-to-bottom.
- Within each row, pixels are packed left-to-right.
- Bits are most-significant-bit first within each byte.
- bytes_per_row = (width + 7) // 8
- Expected bitmap length is usually bytes_per_row * height
- Rows may have unused padding bits at the right edge of the last byte. Ignore those extra bits beyond glyph width.

Example
If width is 10, bytes_per_row is 2.
Each row uses 16 bits on disk, but only the top 10 bits are meaningful.
When reconstructing a row integer, mask it with:
    (1 << width) - 1

Recommended loader algorithm
1. Read and verify the first 4 bytes are b'HFM1'.
2. Read ascent, descent, and glyph_count.
3. Loop glyph_count times:
   - Read 7-byte glyph header.
   - Decode codepoint, width, height, xoff, yoff, payload_len.
   - Read payload_len bytes.
   - Compute bytes_per_row = (width + 7) // 8.
   - For each row, combine bytes_per_row bytes into one integer row value.
   - Mask row value to width bits.
   - Store glyph by codepoint.

Reference Python decoder logic
    hdr = fp.read(4)
    if hdr != b'HFM1':
        raise ValueError('bad magic')
    ascent = int.from_bytes(fp.read(1), 'little')
    descent = int.from_bytes(fp.read(1), 'little')
    glyph_count = int.from_bytes(fp.read(2), 'little')
    glyphs = {}
    for _ in range(glyph_count):
        head = fp.read(7)
        cp = int.from_bytes(head[0:2], 'little')
        width = head[2]
        height = head[3]
        xoff_raw = head[4]
        yoff_raw = head[5]
        payload_len = head[6]
        xoff = xoff_raw - 256 if xoff_raw >= 128 else xoff_raw
        yoff = yoff_raw - 256 if yoff_raw >= 128 else yoff_raw
        payload = fp.read(payload_len)
        bytes_per_row = (width + 7) // 8
        bitmap = []
        for row_index in range(height):
            row = 0
            for byte_index in range(bytes_per_row):
                idx = row_index * bytes_per_row + byte_index
                row = (row << 8) | payload[idx]
            row &= (1 << width) - 1
            bitmap.append(row)
        glyphs[cp] = {
            'width': width,
            'height': height,
            'xoff': xoff,
            'yoff': yoff,
            'bitmap': bitmap,
        }

Rendering notes
- ascent and descent describe the font-wide baseline metrics.
- A typical line height is ascent + descent.
- Each glyph also carries its own xoff and yoff to position the bitmap relative to the text baseline/origin.
- This format assumes simple bitmap placement, not shaping.

Writer notes
- Codepoints are limited to uint16.
- Width, height, xoff, yoff, and bitmap length each occupy one byte in the file.
- The current exporter expects width/height and packed bitmap length to fit in 0..255.
- xoff and yoff must fit in signed-byte range when encoded.

What HFM does not store
- Kerning pairs
- Per-glyph names
- Font family/style metadata beyond ascent/descent and glyph payloads
- Unicode shaping behavior
- Compression beyond row packing

Best use case
HFM is ideal when you want a tiny, deterministic bitmap font file that can be parsed quickly on embedded targets or recreated easily on another platform.
"""

class BDFFont:
    def __init__(self):
        self.glyphs = {}
        self.ascent = 0
        self.descent = 0
        self.source_format = None
        self.source_path = None
        self.font_name = "generated"
        self.bdf_prefix_lines = []
        self.bdf_suffix_lines = []
        self.glyph_order = []

    # -------------------- load BDF --------------------
    @staticmethod
    def load(path):
        f = BDFFont()
        f.source_format = "bdf"
        f.source_path = path
        f.font_name = os.path.splitext(os.path.basename(path))[0]
        cur: dict[str, Any] | None = None
        seen_glyph = False
        in_bitmap = False
        with open(path, "r", encoding="latin1", errors="ignore") as fp:
            for raw_line in fp:
                line = raw_line.rstrip("\n")
                s = line.strip().split()
                if cur is None:
                    if s and s[0] == "STARTCHAR":
                        seen_glyph = True
                        cur = {
                            "bitmap": [],
                            "meta_lines": [],
                            "name": " ".join(s[1:]) if len(s) > 1 else "glyph",
                        }
                        in_bitmap = False
                        continue

                    if s and s[0] == "FONT_ASCENT":
                        f.ascent = int(s[1])
                    elif s and s[0] == "FONT_DESCENT":
                        f.descent = int(s[1])
                    elif s and s[0] == "FONT" and len(s) > 1:
                        f.font_name = " ".join(s[1:])

                    target = f.bdf_suffix_lines if seen_glyph else f.bdf_prefix_lines
                    target.append(line)
                    continue

                if s and s[0] == "BITMAP":
                    in_bitmap = True
                    continue

                if s and s[0] == "ENDCHAR":
                    if "encoding" in cur:
                        # normalize rows to exact width bits
                        bmp = []
                        width = int(cur.get("width", 0))
                        for row in cur["bitmap"]:
                            if width:
                                row &= (1 << width) - 1
                            else:
                                row = 0
                            bmp.append(row)
                        cur["bitmap"] = bmp
                        f.glyphs[cur["encoding"]] = cur
                        f.glyph_order.append(cur["encoding"])
                        cur["original_width"] = width
                    cur = None
                    in_bitmap = False
                    continue

                if in_bitmap and s and all(c in "0123456789ABCDEFabcdef" for c in s[0]):
                    row_hex = s[0]
                    row_val = int(row_hex, 16)
                    width_val = cur.get("width")
                    width = width_val if isinstance(width_val, int) else 0
                    bits_in_row = len(row_hex) * 4
                    if bits_in_row > width and width:
                        # Drop padding bits that BDF stores on the right side of each row.
                        row_val >>= (bits_in_row - width)
                    elif bits_in_row < width:
                        row_val <<= (width - bits_in_row)
                    if width:
                        row_val &= (1 << width) - 1
                    cur["bitmap"].append(row_val)
                    continue

                if s and s[0] == "ENCODING":
                    cur["encoding"] = int(s[1])
                elif s and s[0] == "BBX":
                    cur["width"] = int(s[1])
                    cur["height"] = int(s[2])
                    cur["xoff"] = int(s[3])
                    cur["yoff"] = int(s[4])
                elif s and s[0] == "SWIDTH":
                    cur["swidth"] = (
                        int(s[1]),
                        int(s[2]) if len(s) > 2 else 0,
                    )
                elif s and s[0] == "DWIDTH":
                    cur["dwidth"] = (
                        int(s[1]),
                        int(s[2]) if len(s) > 2 else 0,
                    )
                cur["meta_lines"].append(line)
        return f

    # -------------------- load HFM --------------------
    @staticmethod
    def load_hfm(path):
        f = BDFFont()
        f.source_format = "hfm"
        f.source_path = path
        f.font_name = os.path.splitext(os.path.basename(path))[0]
        with open(path, "rb") as fp:
            hdr = fp.read(4)
            if hdr != b"HFM1":
                raise ValueError("Not a valid HFM file")
            f.ascent = int.from_bytes(fp.read(1), "little")
            f.descent = int.from_bytes(fp.read(1), "little")
            glyph_count = int.from_bytes(fp.read(2), "little")
            for _ in range(glyph_count):
                data = fp.read(7)
                if len(data) != 7:
                    raise ValueError("Unexpected end of file while reading glyph header")
                cp = int.from_bytes(data[0:2], "little")
                w = data[2]
                h = data[3]
                xoff = _from_signed_byte(data[4])
                yoff = _from_signed_byte(data[5])
                dlen = data[6]
                bmp_bytes = fp.read(dlen)
                if len(bmp_bytes) != dlen:
                    raise ValueError(f"Glyph {cp} bitmap truncated: expected {dlen} bytes, got {len(bmp_bytes)}")
                bmp = []
                bytes_per_row = (w+7)//8
                for i in range(h):
                    row = 0
                    for b in range(bytes_per_row):
                        idx = i*bytes_per_row + b
                        if idx < len(bmp_bytes):
                            row = (row << 8) | bmp_bytes[idx]
                    row &= (1 << w) - 1
                    bmp.append(row)
                f.glyphs[cp] = {
                    "width": w,
                    "height": h,
                    "xoff": xoff,
                    "yoff": yoff,
                    "bitmap": bmp,
                    "name": f"uni{cp:04X}",
                    "meta_lines": [],
                    "original_width": w,
                    "dwidth": (w, 0),
                    "swidth": (w * 100, 0),
                }
                f.glyph_order.append(cp)
        return f

# -------------------- helpers --------------------
def _as_byte(value, field_name):
    """Clamp signed fields into a single byte and fail fast if out of range."""
    if not isinstance(value, int):
        raise TypeError(f"{field_name} must be an integer, got {type(value)!r}")
    if value < -128 or value > 255:
        raise ValueError(f"{field_name}={value} is outside the supported byte range (-128..255)")
    return value & 0xFF


def _from_signed_byte(value):
    return value - 256 if value >= 128 else value


def _ordered_codepoints(font):
    seen = set()
    ordered = []
    for cp in getattr(font, "glyph_order", []):
        if cp in font.glyphs and cp not in seen:
            ordered.append(cp)
            seen.add(cp)
    for cp in sorted(font.glyphs.keys()):
        if cp not in seen:
            ordered.append(cp)
    return ordered


def _font_bbox(font):
    glyphs = list(font.glyphs.values())
    if not glyphs:
        height = max(1, int(font.ascent) + int(font.descent))
        return 1, height, 0, -int(font.descent)

    min_xoff = min(int(g.get("xoff", 0)) for g in glyphs)
    min_yoff = min(int(g.get("yoff", 0)) for g in glyphs)
    max_x = max(int(g.get("xoff", 0)) + int(g["width"]) for g in glyphs)
    max_y = max(int(g.get("yoff", 0)) + int(g["height"]) for g in glyphs)
    return (
        max(1, max_x - min_xoff),
        max(1, max_y - min_yoff),
        min_xoff,
        min_yoff,
    )


def _glyph_hex_rows(glyph):
    width = int(glyph["width"])
    bytes_per_row = max(1, (width + 7) // 8)
    mask = (1 << width) - 1 if width else 0
    rows = []
    for row in glyph["bitmap"]:
        row_val = int(row) & mask if width else 0
        parts = []
        for index in range(bytes_per_row):
            shift = (bytes_per_row - 1 - index) * 8
            parts.append(f"{(row_val >> shift) & 0xFF:02X}")
        rows.append("".join(parts))
    return rows


def _glyph_dwidth(glyph):
    dwidth = glyph.get("dwidth")
    if isinstance(dwidth, tuple) and len(dwidth) >= 2:
        return int(glyph["width"]), int(dwidth[1])
    return int(glyph["width"]), 0


def _glyph_swidth(glyph):
    swidth = glyph.get("swidth")
    width = int(glyph["width"])
    if isinstance(swidth, tuple) and len(swidth) >= 2:
        sx = int(swidth[0])
        sy = int(swidth[1])
        original_width = int(glyph.get("original_width", width))
        if original_width > 0 and sx:
            sx = int(round((sx * width) / original_width))
        else:
            sx = width * 100
        return sx, sy
    return width * 100, 0


def _rewrite_bdf_header_line(line, font, glyph_count, bbox):
    stripped = line.strip()
    if not stripped:
        return line

    parts = stripped.split()
    key = parts[0]
    if key == "FONT_ASCENT":
        return f"FONT_ASCENT {int(font.ascent)}"
    if key == "FONT_DESCENT":
        return f"FONT_DESCENT {int(font.descent)}"
    if key == "CHARS":
        return f"CHARS {glyph_count}"
    if key == "FONTBOUNDINGBOX":
        bbox_w, bbox_h, bbox_x, bbox_y = bbox
        return f"FONTBOUNDINGBOX {bbox_w} {bbox_h} {bbox_x} {bbox_y}"
    return line


def _bdf_glyph_block(cp, glyph, preserve_meta):
    width = int(glyph["width"])
    height = int(glyph["height"])
    xoff = int(glyph.get("xoff", 0))
    yoff = int(glyph.get("yoff", 0))
    sx, sy = _glyph_swidth(glyph)
    dx, dy = _glyph_dwidth(glyph)

    lines = [f"STARTCHAR {glyph.get('name', f'uni{cp:04X}')}"]
    meta_lines = glyph.get("meta_lines", []) if preserve_meta else []
    seen = set()

    for line in meta_lines:
        stripped = line.strip()
        if not stripped:
            lines.append(line)
            continue

        key = stripped.split()[0]
        if key == "ENCODING":
            lines.append(f"ENCODING {cp}")
        elif key == "SWIDTH":
            lines.append(f"SWIDTH {sx} {sy}")
        elif key == "DWIDTH":
            lines.append(f"DWIDTH {dx} {dy}")
        elif key == "BBX":
            lines.append(f"BBX {width} {height} {xoff} {yoff}")
        elif key in ("STARTCHAR", "BITMAP", "ENDCHAR"):
            continue
        else:
            lines.append(line)
        seen.add(key)

    if "ENCODING" not in seen:
        lines.append(f"ENCODING {cp}")
    if "SWIDTH" not in seen:
        lines.append(f"SWIDTH {sx} {sy}")
    if "DWIDTH" not in seen:
        lines.append(f"DWIDTH {dx} {dy}")
    if "BBX" not in seen:
        lines.append(f"BBX {width} {height} {xoff} {yoff}")

    lines.append("BITMAP")
    lines.extend(_glyph_hex_rows(glyph))
    lines.append("ENDCHAR")
    return lines


def write_bdf(path, font):
    ordered_cps = _ordered_codepoints(font)
    glyph_count = len(ordered_cps)
    bbox = _font_bbox(font)

    with open(path, "w", encoding="latin1", newline="\n") as fp:
        if font.source_format == "bdf" and font.bdf_prefix_lines:
            for line in font.bdf_prefix_lines:
                fp.write(_rewrite_bdf_header_line(line, font, glyph_count, bbox) + "\n")

            for cp in ordered_cps:
                for line in _bdf_glyph_block(cp, font.glyphs[cp], preserve_meta=True):
                    fp.write(line + "\n")

            suffix_lines = list(font.bdf_suffix_lines) if font.bdf_suffix_lines else ["ENDFONT"]
            wrote_endfont = False
            for line in suffix_lines:
                out_line = _rewrite_bdf_header_line(line, font, glyph_count, bbox)
                stripped = out_line.strip()
                if stripped and stripped.split()[0] == "ENDFONT":
                    wrote_endfont = True
                fp.write(out_line + "\n")
            if not wrote_endfont:
                fp.write("ENDFONT\n")
            return

        bbox_w, bbox_h, bbox_x, bbox_y = bbox
        font_name = getattr(font, "font_name", None) or "generated"
        size = max(1, int(font.ascent) + int(font.descent), bbox_h)

        fp.write("STARTFONT 2.1\n")
        fp.write(f"FONT {font_name}\n")
        fp.write(f"SIZE {size} 75 75\n")
        fp.write(f"FONTBOUNDINGBOX {bbox_w} {bbox_h} {bbox_x} {bbox_y}\n")
        fp.write("STARTPROPERTIES 2\n")
        fp.write(f"FONT_ASCENT {int(font.ascent)}\n")
        fp.write(f"FONT_DESCENT {int(font.descent)}\n")
        fp.write("ENDPROPERTIES\n")
        fp.write(f"CHARS {glyph_count}\n")
        for cp in ordered_cps:
            for line in _bdf_glyph_block(cp, font.glyphs[cp], preserve_meta=False):
                fp.write(line + "\n")
        fp.write("ENDFONT\n")


# -------------------- write HFM --------------------
def write_hfm(path, font, start_cp, end_cp):
    with open(path, "wb") as f:
        f.write(b"HFM1")
        f.write(bytes([font.ascent]))
        f.write(bytes([font.descent]))
        f.write((0).to_bytes(2,"little"))  # placeholder for glyph count
        count = 0
        chunks = []
        for cp in sorted(font.glyphs.keys()):
            if cp < start_cp or cp > end_cp: continue
            g = font.glyphs[cp]
            w,h = g["width"], g["height"]
            bmp = g["bitmap"]
            packed = []
            rb = (w+7)//8
            for row in bmp:
                for b in range(rb):
                    shift = (rb-1-b)*8
                    packed.append((row >> shift) & 0xFF)
            width_byte = _as_byte(w, "width")
            height_byte = _as_byte(h, "height")
            xoff = _as_byte(g.get("xoff", 0), "xoff")
            yoff = _as_byte(g.get("yoff", 0), "yoff")
            packed_len = _as_byte(len(packed), "bitmap length")
            chunk = cp.to_bytes(2,"little") + bytes([width_byte,height_byte,xoff,yoff,packed_len]) + bytes(packed)
            chunks.append(chunk)
            count += 1
        f.seek(6)
        f.write(count.to_bytes(2,"little"))
        f.seek(0,2)
        for c in chunks:
            f.write(c)

# -------------------- GUI --------------------
class FontGUI:
    def __init__(self, root):
        self.root = root
        root.title("BDF/HFM Editor + Exporter")
        root.geometry("1360x720")
        root.minsize(1080, 680)
        root.configure(bg="#eef3f8")

        self.font = None
        self.current_cp = None
        self.zoom = 20
        self.start_var = tk.IntVar(value=32)
        self.end_var = tk.IntVar(value=127)
        self.font_summary_var = tk.StringVar(value="No font loaded")
        self.loaded_font_var = tk.StringVar(value="Loaded font: none")
        self.preview_title_var = tk.StringVar(value="Glyph Preview")
        self.preview_meta_var = tk.StringVar(value="Load a font to inspect and edit glyphs")
        self.zoom_var = tk.StringVar(value="18x")
        self.status_var = tk.StringVar(value="Load a BDF or HFM font to begin.")
        self.sample_text_var = tk.StringVar(value="12:34  0123456789")
        self.sample_spacing_var = tk.StringVar(value="1")
        self.batch_use_export_range_var = tk.BooleanVar(value=True)
        self.batch_auto_adjust_widths_var = tk.BooleanVar(value=False)
        self.xoff_var = tk.IntVar(value=0)
        self.yoff_var = tk.IntVar(value=0)
        self.ascent_var = tk.IntVar(value=0)
        self.descent_var = tk.IntVar(value=0)
        self.mono_align_var = tk.StringVar(value="center")

        self._configure_styles()
        self._build_layout()
        self.sample_text_var.trace_add("write", lambda *_args: self.render_sample_strip())
        self.sample_spacing_var.trace_add("write", lambda *_args: self.render_sample_strip())
        self._update_zoom_label()
        self.render_sample_strip()

    def _configure_styles(self):
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")

        style.configure("App.TFrame", background="#eef3f8")
        style.configure("Panel.TFrame", background="#ffffff")
        style.configure("Title.TLabel", background="#eef3f8", foreground="#0f172a", font=("Segoe UI Semibold", 21))
        style.configure("Subtitle.TLabel", background="#eef3f8", foreground="#52637a", font=("Segoe UI", 10))
        style.configure("App.TLabel", background="#eef3f8", foreground="#334155", font=("Segoe UI", 10))
        style.configure("PanelTitle.TLabel", background="#ffffff", foreground="#0f172a", font=("Segoe UI Semibold", 13))
        style.configure("PanelMeta.TLabel", background="#ffffff", foreground="#64748b", font=("Segoe UI", 10))
        style.configure("Status.TLabel", background="#ffffff", foreground="#334155", font=("Segoe UI", 10))
        style.configure("Hint.TLabel", background="#ffffff", foreground="#64748b", font=("Segoe UI", 9))
        style.configure("App.TCheckbutton", background="#eef3f8", foreground="#334155", font=("Segoe UI", 9))
        style.configure("App.TLabelframe", background="#eef3f8", foreground="#0f172a")
        style.configure("App.TLabelframe.Label", background="#eef3f8", foreground="#0f172a", font=("Segoe UI Semibold", 11))
        style.configure("Panel.TLabelframe", background="#ffffff", foreground="#0f172a")
        style.configure("Panel.TLabelframe.Label", background="#ffffff", foreground="#0f172a", font=("Segoe UI Semibold", 11))
        style.configure("Tool.TButton", font=("Segoe UI", 9), padding=(7, 4))
        style.configure("Accent.TButton", font=("Segoe UI Semibold", 9), padding=(8, 5), foreground="#ffffff")
        style.map("Accent.TButton", background=[("active", "#1d4ed8"), ("!disabled", "#2563eb")])
        style.configure("Danger.TButton", font=("Segoe UI Semibold", 9), padding=(8, 5), foreground="#ffffff")
        style.map("Danger.TButton", background=[("active", "#b91c1c"), ("!disabled", "#dc2626")])
        style.configure("Light.TEntry", fieldbackground="#ffffff", foreground="#0f172a")
        style.configure("Light.TCombobox", fieldbackground="#ffffff", foreground="#0f172a")

    def _build_layout(self):
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(2, weight=1)

        header = ttk.Frame(self.root, style="App.TFrame", padding=(14, 8, 14, 4))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        header.columnconfigure(1, weight=0)

        ttk.Label(header, text="BDF / HFM Font Editor", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            header,
            text="Browse glyphs, inspect the bitmap larger, preview live text, and edit directly on the centered canvas.",
            style="Subtitle.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))
        support = tk.Label(
            header,
            text=BUY_ME_A_COFFEE_LABEL,
            fg="#2563eb",
            bg="#eef3f8",
            cursor="hand2",
            font=("Segoe UI", 17, "underline"),
        )
        support.grid(row=0, column=1, rowspan=2, sticky="e")
        support.bind("<Button-1>", lambda _event: self.open_support_link())

        toolbar = ttk.Frame(self.root, style="App.TFrame", padding=(14, 0, 14, 6))
        toolbar.grid(row=1, column=0, sticky="ew")
        toolbar.columnconfigure(0, weight=1)

        files = ttk.LabelFrame(toolbar, text="Font Files", style="App.TLabelframe", padding=(8, 6))
        files.grid(row=0, column=0, sticky="w", padx=(0, 12))
        ttk.Button(files, text="Load BDF", command=self.load_bdf, style="Accent.TButton").grid(row=0, column=0, padx=(0, 8))
        ttk.Button(files, text="Load HFM", command=self.load_hfm, style="Tool.TButton").grid(row=0, column=1, padx=(0, 8))
        ttk.Button(files, text="Save HFM", command=self.export_hfm, style="Tool.TButton").grid(row=0, column=2)
        ttk.Button(files, text="Save BDF", command=self.export_bdf, style="Tool.TButton").grid(row=0, column=3, padx=(8, 0))
        ttk.Button(files, text="Batch BDF -> HFM", command=self.batch_convert_bdf_folder, style="Tool.TButton").grid(row=0, column=4, padx=(8, 0))
        ttk.Button(files, text="About HFM", command=self.show_hfm_about, style="Tool.TButton").grid(row=0, column=5, padx=(8, 0))
        ttk.Checkbutton(
            files,
            text="Batch auto adjust widths",
            variable=self.batch_auto_adjust_widths_var,
            style="App.TCheckbutton",
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Checkbutton(
            files,
            text="Batch uses export range",
            variable=self.batch_use_export_range_var,
            style="App.TCheckbutton",
        ).grid(row=1, column=4, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Label(files, textvariable=self.loaded_font_var, style="App.TLabel").grid(row=2, column=0, columnspan=6, sticky="w", pady=(6, 0))

        export = ttk.LabelFrame(toolbar, text="Export Range", style="App.TLabelframe", padding=(8, 6))
        export.grid(row=0, column=1, sticky="e")
        ttk.Label(export, text="Start", style="App.TLabel").grid(row=0, column=0, padx=(0, 6))
        ttk.Entry(export, width=8, textvariable=self.start_var, style="Light.TEntry").grid(row=0, column=1, padx=(0, 10))
        ttk.Label(export, text="End", style="App.TLabel").grid(row=0, column=2, padx=(0, 6))
        ttk.Entry(export, width=8, textvariable=self.end_var, style="Light.TEntry").grid(row=0, column=3)

        main = ttk.Frame(self.root, style="App.TFrame", padding=(14, 0, 14, 8))
        main.grid(row=2, column=0, sticky="nsew")
        main.columnconfigure(1, weight=1)
        main.rowconfigure(0, weight=1)

        self.sidebar = ttk.LabelFrame(main, text="Glyph List", style="App.TLabelframe", padding=(8, 8))
        self.sidebar.grid(row=0, column=0, sticky="ns", padx=(0, 14))
        sidebar = self.sidebar
        ttk.Label(sidebar, textvariable=self.font_summary_var, style="App.TLabel", justify="left").pack(anchor="w", pady=(0, 8))

        list_wrap = ttk.Frame(sidebar, style="App.TFrame")
        list_wrap.pack(fill="both", expand=True)
        self.listbox = tk.Listbox(
            list_wrap,
            width=24,
            font=("Consolas", 11),
            bg="#ffffff",
            fg="#0f172a",
            selectbackground="#2563eb",
            selectforeground="#ffffff",
            activestyle="none",
            exportselection=False,
            selectmode=tk.EXTENDED,
            relief="flat",
            borderwidth=0,
            highlightthickness=1,
            highlightbackground="#cbd5e1",
            highlightcolor="#3b82f6",
        )
        self.listbox.pack(side=tk.LEFT, fill="both", expand=True)
        self.listbox.bind("<<ListboxSelect>>", self.on_list_select)
        self.listbox.bind("<Delete>", self.delete_selected_glyphs)
        scrollbar = ttk.Scrollbar(list_wrap, orient="vertical", command=self.listbox.yview)
        scrollbar.pack(side=tk.LEFT, fill="y", padx=(8, 0))
        self.listbox.configure(yscrollcommand=scrollbar.set)

        preview = ttk.Frame(main, style="Panel.TFrame", padding=(12, 10))
        preview.grid(row=0, column=1, sticky="nsew", padx=(0, 14))
        preview.columnconfigure(0, weight=1)
        preview.rowconfigure(1, weight=1)

        preview_header = ttk.Frame(preview, style="Panel.TFrame")
        preview_header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        preview_header.columnconfigure(0, weight=1)
        ttk.Label(preview_header, textvariable=self.preview_title_var, style="PanelTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(preview_header, textvariable=self.preview_meta_var, style="PanelMeta.TLabel").grid(row=1, column=0, sticky="w", pady=(2, 0))

        canvas_shell = ttk.Frame(preview, style="Panel.TFrame")
        canvas_shell.grid(row=1, column=0, sticky="nsew")
        canvas_shell.columnconfigure(0, weight=1)
        canvas_shell.rowconfigure(0, weight=1)

        self.canvas = tk.Canvas(canvas_shell, bg="#eef3f8", highlightthickness=0, bd=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.canvas.bind("<Button-1>", self.canvas_click)
        self.canvas.bind("<Configure>", self.on_canvas_resize)
        self.canvas.bind("<MouseWheel>", self.on_canvas_mousewheel)

        sample_group = ttk.LabelFrame(preview, text="Live Sample", style="Panel.TLabelframe", padding=(8, 6))
        sample_group.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        sample_group.columnconfigure(1, weight=1)
        ttk.Label(sample_group, text="Text", style="PanelMeta.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(sample_group, textvariable=self.sample_text_var, style="Light.TEntry").grid(row=0, column=1, sticky="ew")
        ttk.Label(sample_group, text="Letter spacing", style="PanelMeta.TLabel").grid(row=0, column=2, sticky="w", padx=(12, 8))
        ttk.Spinbox(sample_group, from_=0, to=16, width=6, textvariable=self.sample_spacing_var).grid(row=0, column=3, sticky="w")
        self.sample_canvas = tk.Canvas(
            sample_group,
            height=80,
            bg="#ffffff",
            highlightthickness=1,
            highlightbackground="#d7dee8",
            bd=0,
        )
        self.sample_canvas.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        self.sample_canvas.bind("<Configure>", self.on_sample_resize)
        ttk.Label(
            sample_group,
            text="Preview spacing matches draw_text(letter_spacing=...).",
            style="Hint.TLabel",
            justify="left",
        ).grid(row=2, column=0, columnspan=4, sticky="w", pady=(6, 0))

        ttk.Label(
            preview,
            text="Click pixels to toggle them. Use the mouse wheel or the zoom buttons to scale the view.",
            style="Hint.TLabel",
        ).grid(row=3, column=0, sticky="w", pady=(6, 0))

        tools = ttk.LabelFrame(main, text="Tools", style="App.TLabelframe", padding=(8, 8))
        tools.grid(row=0, column=2, sticky="ns")
        tools.columnconfigure(0, weight=1)
        tools.columnconfigure(1, weight=1)

        nav = ttk.LabelFrame(tools, text="Navigate", style="App.TLabelframe", padding=(6, 6))
        nav.grid(row=0, column=0, sticky="new", padx=(0, 6), pady=(0, 6))
        nav.columnconfigure(0, weight=1)
        nav.columnconfigure(1, weight=1)
        ttk.Button(nav, text="Prev", command=self.prev_glyph, style="Tool.TButton").grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(nav, text="Next", command=self.next_glyph, style="Tool.TButton").grid(row=0, column=1, sticky="ew")

        zoomf = ttk.LabelFrame(tools, text="Zoom", style="App.TLabelframe", padding=(6, 6))
        zoomf.grid(row=0, column=1, sticky="new", pady=(0, 6))
        zoomf.columnconfigure(1, weight=1)
        ttk.Button(zoomf, text="-", command=lambda:self.change_zoom(-1), style="Tool.TButton").grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ttk.Label(zoomf, textvariable=self.zoom_var, style="App.TLabel", anchor="center").grid(row=0, column=1, sticky="ew")
        ttk.Button(zoomf, text="+", command=lambda:self.change_zoom(+1), style="Tool.TButton").grid(row=0, column=2, sticky="ew", padx=(8, 0))

        dimf = ttk.LabelFrame(tools, text="Glyph Dimensions", style="App.TLabelframe", padding=(6, 6))
        dimf.grid(row=1, column=0, sticky="new", padx=(0, 6), pady=(0, 6))
        dimf.columnconfigure(0, weight=1)
        dimf.columnconfigure(1, weight=1)
        ttk.Button(dimf, text="Width -", command=lambda:self.change_width(-1), style="Tool.TButton").grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(dimf, text="Width +", command=lambda:self.change_width(+1), style="Tool.TButton").grid(row=0, column=1, sticky="ew")
        ttk.Button(dimf, text="Height -", command=lambda:self.change_height(-1), style="Tool.TButton").grid(row=1, column=0, sticky="ew", padx=(0, 6), pady=(6, 0))
        ttk.Button(dimf, text="Height +", command=lambda:self.change_height(+1), style="Tool.TButton").grid(row=1, column=1, sticky="ew", pady=(6, 0))
        ttk.Button(dimf, text="Auto Adjust Widths", command=self.auto_adjust_widths, style="Tool.TButton").grid(
            row=2,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(6, 0),
        )

        shiftf = ttk.LabelFrame(tools, text="Shift Pixels", style="App.TLabelframe", padding=(6, 6))
        shiftf.grid(row=1, column=1, sticky="new", pady=(0, 6))
        shiftf.columnconfigure(0, weight=1)
        shiftf.columnconfigure(1, weight=1)
        ttk.Button(shiftf, text="Shift Left", command=lambda:self.shift_pixels(dx=-1), style="Tool.TButton").grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(shiftf, text="Shift Right", command=lambda:self.shift_pixels(dx=+1), style="Tool.TButton").grid(row=0, column=1, sticky="ew")
        ttk.Button(shiftf, text="Shift Up", command=lambda:self.shift_pixels(dy=-1), style="Tool.TButton").grid(row=1, column=0, sticky="ew", padx=(0, 6), pady=(6, 0))
        ttk.Button(shiftf, text="Shift Down", command=lambda:self.shift_pixels(dy=+1), style="Tool.TButton").grid(row=1, column=1, sticky="ew", pady=(6, 0))

        offsetf = ttk.LabelFrame(tools, text="Glyph Offsets", style="App.TLabelframe", padding=(6, 6))
        offsetf.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        offsetf.columnconfigure(1, weight=1)
        offsetf.columnconfigure(3, weight=1)
        ttk.Label(offsetf, text="xoff", style="App.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Spinbox(offsetf, from_=-128, to=127, width=6, textvariable=self.xoff_var).grid(row=0, column=1, sticky="ew")
        ttk.Label(offsetf, text="yoff", style="App.TLabel").grid(row=0, column=2, sticky="w", padx=(12, 8))
        ttk.Spinbox(offsetf, from_=-128, to=127, width=6, textvariable=self.yoff_var).grid(row=0, column=3, sticky="ew")
        ttk.Button(offsetf, text="X -", command=lambda:self.nudge_selected_offsets(dx=-1), style="Tool.TButton").grid(
            row=1,
            column=0,
            sticky="ew",
            padx=(0, 6),
            pady=(8, 0),
        )
        ttk.Button(offsetf, text="X +", command=lambda:self.nudge_selected_offsets(dx=+1), style="Tool.TButton").grid(
            row=1,
            column=1,
            sticky="ew",
            pady=(8, 0),
        )
        ttk.Button(offsetf, text="Y -", command=lambda:self.nudge_selected_offsets(dy=-1), style="Tool.TButton").grid(
            row=1,
            column=2,
            sticky="ew",
            padx=(12, 6),
            pady=(8, 0),
        )
        ttk.Button(offsetf, text="Y +", command=lambda:self.nudge_selected_offsets(dy=+1), style="Tool.TButton").grid(
            row=1,
            column=3,
            sticky="ew",
            pady=(8, 0),
        )
        ttk.Button(offsetf, text="Apply To Selection", command=self.apply_selected_offsets, style="Accent.TButton").grid(
            row=2,
            column=0,
            columnspan=4,
            sticky="ew",
            pady=(8, 0),
        )

        monof = ttk.LabelFrame(tools, text="Monospace Selected", style="App.TLabelframe", padding=(6, 6))
        monof.grid(row=3, column=0, sticky="new", padx=(0, 6), pady=(0, 6))
        monof.columnconfigure(1, weight=1)
        ttk.Label(monof, text="Justify", style="App.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Combobox(
            monof,
            textvariable=self.mono_align_var,
            values=("left", "center", "right"),
            state="readonly",
            style="Light.TCombobox",
            width=10,
        ).grid(row=0, column=1, sticky="ew")
        ttk.Button(monof, text="Apply To Selection", command=self.monospace_selected, style="Accent.TButton").grid(
            row=1,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(8, 0),
        )

        fontf = ttk.LabelFrame(tools, text="Font Metrics", style="App.TLabelframe", padding=(6, 6))
        fontf.grid(row=3, column=1, sticky="new", pady=(0, 6))
        fontf.columnconfigure(1, weight=1)
        fontf.columnconfigure(3, weight=1)
        ttk.Label(fontf, text="Ascent", style="App.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Spinbox(fontf, from_=0, to=255, width=6, textvariable=self.ascent_var).grid(row=0, column=1, sticky="ew")
        ttk.Label(fontf, text="Descent", style="App.TLabel").grid(row=0, column=2, sticky="w", padx=(12, 8))
        ttk.Spinbox(fontf, from_=0, to=255, width=6, textvariable=self.descent_var).grid(row=0, column=3, sticky="ew")
        ttk.Button(fontf, text="Apply Font Metrics", command=self.apply_font_metrics, style="Accent.TButton").grid(
            row=1,
            column=0,
            columnspan=4,
            sticky="ew",
            pady=(8, 0),
        )

        deletef = ttk.LabelFrame(tools, text="Delete", style="App.TLabelframe", padding=(6, 6))
        deletef.grid(row=4, column=0, columnspan=2, sticky="ew")
        deletef.columnconfigure(0, weight=1)
        ttk.Button(deletef, text="Delete Selected Glyphs", command=self.delete_selected_glyphs, style="Danger.TButton").grid(
            row=0,
            column=0,
            sticky="ew",
        )

        status = ttk.Frame(self.root, style="Panel.TFrame", padding=(14, 6))
        status.grid(row=3, column=0, sticky="ew")
        status.columnconfigure(0, weight=1)
        ttk.Label(status, textvariable=self.status_var, style="Status.TLabel").grid(row=0, column=0, sticky="w")

    def _set_status(self, text):
        self.status_var.set(text)

    def _sample_letter_spacing(self):
        try:
            return max(0, int(self.sample_spacing_var.get()))
        except (TypeError, ValueError):
            return 1

    def _read_int_var(self, variable, field_name, minimum=None, maximum=None):
        try:
            value = int(variable.get())
        except (tk.TclError, ValueError):
            messagebox.showerror("Invalid Value", f"{field_name} must be an integer.")
            return None

        if minimum is not None and value < minimum:
            messagebox.showerror("Invalid Value", f"{field_name} must be between {minimum} and {maximum}.")
            return None
        if maximum is not None and value > maximum:
            messagebox.showerror("Invalid Value", f"{field_name} must be between {minimum} and {maximum}.")
            return None
        return value

    def _sync_metric_controls(self):
        font = self.font
        if font is None:
            self.xoff_var.set(0)
            self.yoff_var.set(0)
            self.ascent_var.set(0)
            self.descent_var.set(0)
            return

        self.ascent_var.set(int(font.ascent))
        self.descent_var.set(int(font.descent))

        glyph = font.glyphs.get(self.current_cp) if self.current_cp is not None else None
        if glyph is None:
            self.xoff_var.set(0)
            self.yoff_var.set(0)
            return

        self.xoff_var.set(int(glyph.get("xoff", 0)))
        self.yoff_var.set(int(glyph.get("yoff", 0)))

    def _update_loaded_font_label(self):
        font = self.font
        if not font:
            self.loaded_font_var.set("Loaded font: none")
            return

        source_name = os.path.basename(font.source_path) if getattr(font, "source_path", None) else getattr(font, "font_name", "font")
        source_format = font.source_format.upper() if font.source_format else "FONT"
        self.loaded_font_var.set(f"Loaded font: {source_name} ({source_format})")

    def open_support_link(self):
        try:
            webbrowser.open(BUY_ME_A_COFFEE_URL)
            self._set_status("Opened support link in your browser")
        except Exception as exc:
            messagebox.showerror("Error", f"Failed to open link:\n{exc}")

    def show_hfm_about(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("About HFM")
        dialog.geometry("900x760")
        dialog.minsize(720, 560)
        dialog.configure(bg="#eef3f8")
        dialog.transient(self.root)

        shell = ttk.Frame(dialog, style="App.TFrame", padding=(18, 18, 18, 18))
        shell.pack(fill="both", expand=True)
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(2, weight=1)

        ttk.Label(shell, text="About HFM", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            shell,
            text="Reference notes for developers and AI agents implementing HFM loaders on other platforms.",
            style="Subtitle.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(8, 0))

        text = scrolledtext.ScrolledText(
            shell,
            wrap=tk.WORD,
            font=("Consolas", 10),
            bg="#ffffff",
            fg="#0f172a",
            insertbackground="#0f172a",
            relief="flat",
            borderwidth=1,
            highlightthickness=1,
            highlightbackground="#cbd5e1",
            highlightcolor="#3b82f6",
            padx=14,
            pady=12,
        )
        text.grid(row=2, column=0, sticky="nsew", pady=(14, 12))
        text.insert("1.0", HFM_ABOUT_TEXT)
        text.configure(state="disabled")

        action_bar = ttk.Frame(shell, style="App.TFrame")
        action_bar.grid(row=3, column=0, sticky="e")
        ttk.Button(action_bar, text="Close", command=dialog.destroy, style="Accent.TButton").pack(side=tk.RIGHT)

        dialog.grab_set()
        self._set_status("Opened HFM format reference")

    def batch_convert_bdf_folder(self):
        initial_dir = None
        if self.font is not None and self.font.source_path:
            initial_dir = os.path.dirname(self.font.source_path)

        use_export_range = bool(self.batch_use_export_range_var.get())
        auto_adjust_widths = bool(self.batch_auto_adjust_widths_var.get())
        if use_export_range:
            try:
                start_cp = int(self.start_var.get())
                end_cp = int(self.end_var.get())
            except (tk.TclError, ValueError):
                messagebox.showerror("Batch Convert", "Export range must contain whole numbers before batch convert can use it.")
                return

            if start_cp < 0 or end_cp > 0xFFFF or start_cp > end_cp:
                messagebox.showerror("Batch Convert", "Export range must be within 0..65535 and Start must be less than or equal to End.")
                return
        else:
            start_cp = 0
            end_cp = 0xFFFF

        range_desc = f"glyphs {start_cp} to {end_cp}" if use_export_range else "the full glyph range"
        adjust_desc = " with auto width adjustment" if auto_adjust_widths else ""

        folder = filedialog.askdirectory(initialdir=initial_dir or os.getcwd())
        if not folder:
            return

        bdf_paths = []
        for root_dir, _dir_names, file_names in os.walk(folder):
            for file_name in file_names:
                if file_name.lower().endswith(".bdf"):
                    bdf_paths.append(os.path.join(root_dir, file_name))

        bdf_paths.sort()
        count = len(bdf_paths)
        if count == 0:
            messagebox.showinfo("Batch Convert", "No BDF fonts were found in that folder or its subfolders.")
            self._set_status("Batch convert found no BDF fonts")
            return

        should_convert = messagebox.askokcancel(
            "Batch Convert BDF -> HFM",
            f"Found {count} BDF font(s) under:\n{folder}\n\nBatch convert will export {range_desc}{adjust_desc}.\n\nPress OK to create matching .hfm files beside each .bdf source.",
            default=messagebox.OK,
        )
        if not should_convert:
            self._set_status("Batch convert cancelled")
            return

        converted = 0
        failures = []
        for bdf_path in bdf_paths:
            try:
                font = BDFFont.load(bdf_path)
                if auto_adjust_widths:
                    for glyph in font.glyphs.values():
                        self._trim_glyph_horizontal_space(glyph)
                out_path = os.path.splitext(bdf_path)[0] + ".hfm"
                write_hfm(out_path, font, start_cp, end_cp)
                converted += 1
            except Exception as exc:
                failures.append((bdf_path, str(exc)))

        if failures:
            details = "\n".join(
                f"{os.path.basename(path)}: {error}" for path, error in failures[:10]
            )
            if len(failures) > 10:
                details += f"\n... and {len(failures) - 10} more"
            messagebox.showwarning(
                "Batch Convert Completed With Errors",
                f"Converted {converted} of {count} BDF font(s) using {range_desc}{adjust_desc}.\n\nFailures:\n{details}",
            )
            self._set_status(f"Batch converted {converted}/{count} BDF fonts to HFM using {range_desc}{adjust_desc}")
            return

        messagebox.showinfo(
            "Batch Convert Complete",
            f"Converted {converted} BDF font(s) to HFM in place using {range_desc}{adjust_desc}.",
        )
        self._set_status(f"Batch converted {converted} BDF fonts to HFM using {range_desc}{adjust_desc}")

    def _update_font_summary(self):
        if not self.font:
            self.font_summary_var.set("No font loaded")
            self.sidebar.configure(text="Glyph List")
            self._update_loaded_font_label()
            self._sync_metric_controls()
            return
        glyph_count = len(self.font.glyphs)
        self.sidebar.configure(text=f"Glyph List ({glyph_count})")
        source = self.font.source_format.upper() if self.font.source_format else "FONT"
        source_name = os.path.basename(self.font.source_path) if self.font.source_path else self.font.font_name
        self.font_summary_var.set(
            f"{source_name}\n{source} source   Ascent: {self.font.ascent}  Descent: {self.font.descent}\nCtrl-click to multi-select glyphs"
        )
        self._update_loaded_font_label()
        self._sync_metric_controls()

    def _update_zoom_label(self):
        self.zoom_var.set(f"{self.zoom}x")

    def _get_selected_codepoints(self):
        font = self.font
        if font is None:
            return []
        cps = sorted(font.glyphs.keys())
        selected = []
        for index in self.listbox.curselection():
            if 0 <= index < len(cps):
                selected.append(cps[index])
        if not selected and self.current_cp is not None:
            selected.append(self.current_cp)
        return selected

    def _apply_to_selected_glyphs(self, action):
        font = self.font
        if font is None:
            return 0, []

        selected = [cp for cp in self._get_selected_codepoints() if cp in font.glyphs]
        changed = 0
        for cp in selected:
            glyph = font.glyphs.get(cp)
            if glyph is None:
                continue
            if action(cp, glyph):
                changed += 1
        return changed, selected

    def _refresh_after_bulk_edit(self, selected, status_text):
        if not selected:
            return
        focus_cp = self.current_cp if self.current_cp in selected else selected[0]
        if self.font is not None and focus_cp not in self.font.glyphs:
            focus_cp = selected[0]
        self.show_glyph(focus_cp)
        self._set_status(status_text)

    def render_sample_strip(self):
        canvas = getattr(self, "sample_canvas", None)
        if canvas is None:
            return

        canvas.delete("all")
        canvas_w = max(1, canvas.winfo_width())
        canvas_h = max(1, canvas.winfo_height())
        canvas.create_rectangle(0, 0, canvas_w, canvas_h, fill="#ffffff", outline="")
        canvas.create_rectangle(10, 12, canvas_w - 10, canvas_h - 12, fill="#fbfdff", outline="#d7dee8")

        font = self.font
        sample_text = self.sample_text_var.get()
        letter_spacing = self._sample_letter_spacing()
        if font is None or not font.glyphs:
            canvas.create_text(
                canvas_w // 2,
                canvas_h // 2,
                text="Live sample appears here once a font is loaded.",
                fill="#7a8797",
                font=("Segoe UI", 11),
            )
            return

        if not sample_text:
            canvas.create_text(
                canvas_w // 2,
                canvas_h // 2,
                text="Type sample text above to preview the font.",
                fill="#7a8797",
                font=("Segoe UI", 11),
            )
            return

        fallback_advance = max(3, max((int(g["width"]) for g in font.glyphs.values()), default=6) // 2) + letter_spacing
        glyph_runs = []
        max_top = max(1, int(font.ascent))
        max_bottom = max(1, int(font.descent))

        for char in sample_text:
            glyph = font.glyphs.get(ord(char))
            if glyph is None:
                glyph_runs.append((None, fallback_advance))
                continue

            yoff = int(glyph.get("yoff", 0))
            height = int(glyph["height"])
            max_top = max(max_top, height + yoff)
            max_bottom = max(max_bottom, -yoff)
            glyph_runs.append((glyph, int(glyph["width"]) + letter_spacing))

        raw_width = max(1, sum(advance for _glyph, advance in glyph_runs))
        raw_height = max(1, max_top + max_bottom)
        avail_w = max(1, canvas_w - 36)
        avail_h = max(1, canvas_h - 32)
        scale = max(1, min(8, avail_w // raw_width, avail_h // raw_height))

        draw_x = 18 + max(0, (avail_w - raw_width * scale) // 2)
        baseline_y = 16 + max(0, (avail_h - raw_height * scale) // 2) + max_top * scale

        for glyph, advance in glyph_runs:
            if glyph is None:
                draw_x += advance * scale
                continue

            glyph_x = draw_x + int(glyph.get("xoff", 0)) * scale
            glyph_y = baseline_y - (int(glyph["height"]) + int(glyph.get("yoff", 0))) * scale
            width = int(glyph["width"])
            for y, row in enumerate(glyph["bitmap"]):
                for bit in range(width):
                    if ((int(row) >> (width - 1 - bit)) & 1) == 0:
                        continue
                    x1 = glyph_x + bit * scale
                    y1 = glyph_y + y * scale
                    canvas.create_rectangle(
                        x1,
                        y1,
                        x1 + scale,
                        y1 + scale,
                        fill="#111827",
                        outline="#111827",
                    )

            draw_x += advance * scale

    def _refresh_current_glyph(self):
        if self.current_cp is None:
            self.render_sample_strip()
            return
        self.show_glyph(self.current_cp)

    def on_canvas_resize(self, _event):
        if self.current_cp is not None:
            self.show_glyph(self.current_cp)

    def on_sample_resize(self, _event):
        self.render_sample_strip()

    def on_canvas_mousewheel(self, event):
        step = 1 if event.delta > 0 else -1
        self.change_zoom(step)
        return "break"

    # -------------------- load BDF --------------------
    def load_bdf(self):
        p = filedialog.askopenfilename(filetypes=[("BDF files","*.bdf")])
        if not p: return
        try:
            self.font = BDFFont.load(p)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load BDF:\n{e}")
            return
        self.populate_listbox()
        self._set_status(f"Loaded BDF font: {os.path.basename(p)} ({len(self.font.glyphs)} glyphs)")

    # -------------------- load HFM --------------------
    def load_hfm(self):
        p = filedialog.askopenfilename(filetypes=[("HFM files","*.hfm")])
        if not p: return
        try:
            self.font = BDFFont.load_hfm(p)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load HFM:\n{e}")
            return
        self.populate_listbox()
        self._set_status(f"Loaded HFM font: {os.path.basename(p)} ({len(self.font.glyphs)} glyphs)")

    # -------------------- populate charset listbox --------------------
    def populate_listbox(self, selected_cps=None, active_cp=None):
        font = self.font
        if font is None:
            return
        self.listbox.delete(0, tk.END)
        self._update_font_summary()
        cps = sorted(font.glyphs.keys())
        for cp in cps:
            ch = chr(cp) if cp >= 32 else '?'
            self.listbox.insert(tk.END,f"{cp:3d} 0x{cp:04X} '{ch}'")
        if cps:
            selected = [cp for cp in (selected_cps or []) if cp in font.glyphs]
            focus_cp = active_cp if active_cp in font.glyphs else None
            if focus_cp is None:
                focus_cp = selected[0] if selected else cps[0]

            for cp in selected or [focus_cp]:
                self.listbox.selection_set(cps.index(cp))

            focus_idx = cps.index(focus_cp)
            self.listbox.activate(focus_idx)
            self.listbox.see(focus_idx)
            self.show_glyph(focus_cp)
        else:
            self.current_cp = None
            self.canvas.delete("all")
            self.preview_title_var.set("Glyph Preview")
            self.preview_meta_var.set("Load a font to inspect and edit glyphs")
            self._sync_metric_controls()
            self.render_sample_strip()

    # -------------------- charset select --------------------
    def on_list_select(self, e):
        font = self.font
        if font is None:
            return
        if not self.listbox.curselection(): return
        active_idx = self.listbox.index(tk.ACTIVE)
        idx = active_idx if active_idx in self.listbox.curselection() else self.listbox.curselection()[0]
        cps = sorted(font.glyphs.keys())
        self.show_glyph(cps[idx])

    # -------------------- draw glyph --------------------
    def show_glyph(self, cp):
        font = self.font
        if font is None:
            return
        self.current_cp = cp
        g = font.glyphs.get(cp)
        self.canvas.delete("all")
        if not g: return
        self._sync_metric_controls()
        w,h = g["width"], g["height"]
        bmp = g["bitmap"]
        z = self.zoom
        canvas_w = max(1, self.canvas.winfo_width())
        canvas_h = max(1, self.canvas.winfo_height())
        grid_w = w * z
        grid_h = h * z
        pad = 34

        self._draw_x0 = max(pad, (canvas_w - grid_w) // 2)
        self._draw_y0 = max(pad, (canvas_h - grid_h) // 2)
        x0,y0 = self._draw_x0, self._draw_y0

        panel_x0 = max(16, x0 - pad)
        panel_y0 = max(16, y0 - pad)
        panel_x1 = min(canvas_w - 16, x0 + grid_w + pad)
        panel_y1 = min(canvas_h - 16, y0 + grid_h + pad)
        self.canvas.create_rectangle(0, 0, canvas_w, canvas_h, fill="#eef3f8", outline="")
        self.canvas.create_rectangle(panel_x0, panel_y0, panel_x1, panel_y1, fill="#ffffff", outline="#cbd5e1", width=2)

        for y,row in enumerate(bmp):
            for bit in range(w):
                on = (row >> (w-1-bit)) & 1
                col = "#0f172a" if on else "#ffffff"
                self.canvas.create_rectangle(
                    x0+bit*z,
                    y0+y*z,
                    x0+(bit+1)*z,
                    y0+(y+1)*z,
                    fill=col,
                    outline="#dbe3ed",
                )
        self.canvas.create_rectangle(x0, y0, x0+w*z, y0+h*z, outline="#1d4ed8", width=2)
        ch = chr(cp) if cp >= 32 else '?'
        selected_count = len(self._get_selected_codepoints())
        selection_note = f"   selected {selected_count}" if selected_count > 1 else ""
        self.preview_title_var.set(f"Glyph U+{cp:04X} '{ch}'")
        self.preview_meta_var.set(
            f"{w}x{h} pixels   xoff {g.get('xoff', 0)}   yoff {g.get('yoff', 0)}   zoom {self.zoom}x{selection_note}"
        )
        self._update_zoom_label()
        self._set_status(f"Editing glyph {cp} (0x{cp:04X})")
        self.render_sample_strip()

    # -------------------- navigation --------------------
    def prev_glyph(self):
        if not self.font or self.current_cp is None: return
        cps = sorted(self.font.glyphs.keys())
        i = cps.index(self.current_cp)
        if i>0:
            self.listbox.selection_clear(0, tk.END)
            self.listbox.selection_set(i-1)
            self.listbox.activate(i-1)
            self.listbox.see(i-1)
            self.show_glyph(cps[i-1])

    def next_glyph(self):
        if not self.font or self.current_cp is None: return
        cps = sorted(self.font.glyphs.keys())
        i = cps.index(self.current_cp)
        if i < len(cps)-1:
            self.listbox.selection_clear(0, tk.END)
            self.listbox.selection_set(i+1)
            self.listbox.activate(i+1)
            self.listbox.see(i+1)
            self.show_glyph(cps[i+1])

    # -------------------- zoom --------------------
    def change_zoom(self,d):
        self.zoom = max(2, self.zoom + d)
        self._update_zoom_label()
        if self.current_cp is not None:
            self.show_glyph(self.current_cp)

    # -------------------- width change (adds/removes blank columns on right) --------------------
    def change_width(self,d):
        if not self.font or self.current_cp is None: return

        def apply_width(_cp, glyph):
            oldw = int(glyph["width"])
            neww = max(1, oldw + d)
            if neww == oldw:
                return False

            newbmp = []
            if neww > oldw:
                shift = neww - oldw
                for row in glyph["bitmap"]:
                    newbmp.append(int(row) << shift)
            else:
                shift = oldw - neww
                for row in glyph["bitmap"]:
                    newbmp.append(int(row) >> shift)

            glyph["width"] = neww
            glyph["bitmap"] = newbmp
            return True

        changed, selected = self._apply_to_selected_glyphs(apply_width)
        if changed == 0:
            return
        self._refresh_after_bulk_edit(selected, f"Adjusted width on {changed} glyph{'s' if changed != 1 else ''}")

    # -------------------- height change (adds/removes blank rows at bottom) --------------------
    def change_height(self, d):
        if not self.font or self.current_cp is None: return

        def apply_height(_cp, glyph):
            oldh = int(glyph["height"])
            newh = max(1, oldh + d)
            if newh == oldh:
                return False

            if newh > oldh:
                newbmp = list(glyph["bitmap"]) + ([0] * (newh - oldh))
            else:
                newbmp = list(glyph["bitmap"][:newh])

            glyph["height"] = newh
            glyph["bitmap"] = newbmp
            return True

        changed, selected = self._apply_to_selected_glyphs(apply_height)
        if changed == 0:
            return
        self._refresh_after_bulk_edit(selected, f"Adjusted height on {changed} glyph{'s' if changed != 1 else ''}")

    def _trailing_blank_columns(self, row, width):
        count = 0
        while count < width and ((row >> count) & 1) == 0:
            count += 1
        return count

    def _trim_glyph_horizontal_space(self, glyph):
        width = int(glyph["width"])
        if width <= 0:
            return False

        mask = (1 << width) - 1
        rows = [int(row) & mask for row in glyph["bitmap"]]
        ink_rows = [row for row in rows if row]
        if not ink_rows:
            return False

        left_trim = min(width - row.bit_length() for row in ink_rows)
        right_trim = min(self._trailing_blank_columns(row, width) for row in ink_rows)
        if left_trim == 0 and right_trim == 0:
            return False

        new_width = width - left_trim - right_trim
        if new_width <= 0:
            return False

        new_mask = (1 << new_width) - 1
        glyph["width"] = new_width
        glyph["bitmap"] = [((row >> right_trim) & new_mask) for row in rows]
        return True

    def auto_adjust_widths(self):
        if not self.font or self.current_cp is None:
            return

        selected = self._get_selected_codepoints()
        if not selected:
            messagebox.showinfo("Auto Adjust Widths", "Select one or more glyphs first.")
            return

        changed, selected = self._apply_to_selected_glyphs(lambda _cp, glyph: self._trim_glyph_horizontal_space(glyph))
        noun = "glyph" if len(selected) == 1 else "glyphs"
        if changed == 0:
            self._refresh_after_bulk_edit(selected, f"No side space to trim on {len(selected)} {noun}")
            return
        self._refresh_after_bulk_edit(selected, f"Auto adjusted widths on {changed} glyph{'s' if changed != 1 else ''}")

    # -------------------- shift pixels inside glyph box --------------------
    def shift_pixels(self, dx=0, dy=0):
        if not self.font or self.current_cp is None: return
        if dx == 0 and dy == 0: return

        def apply_shift(_cp, glyph):
            w = int(glyph["width"])
            h = int(glyph["height"])
            newbmp = [int(row) for row in glyph["bitmap"]]

            if dx < 0:
                mask = (1 << (w - 1)) - 1 if w > 1 else 0
                shifted = []
                for row in newbmp:
                    newrow = (row & mask) << 1
                    newrow &= (1 << w) - 1
                    shifted.append(newrow)
                newbmp = shifted
            elif dx > 0:
                newbmp = [row >> 1 for row in newbmp]

            if dy < 0:
                newbmp = newbmp[1:] + [0]
            elif dy > 0:
                newbmp = [0] + newbmp[:-1]

            glyph["bitmap"] = newbmp[:h]
            return True

        changed, selected = self._apply_to_selected_glyphs(apply_shift)
        if changed == 0:
            return
        self._refresh_after_bulk_edit(selected, f"Shifted pixels in {changed} glyph{'s' if changed != 1 else ''}")

    def nudge_selected_offsets(self, dx=0, dy=0):
        xoff = self._read_int_var(self.xoff_var, "Glyph x offset", -128, 127)
        if xoff is None:
            return
        yoff = self._read_int_var(self.yoff_var, "Glyph y offset", -128, 127)
        if yoff is None:
            return

        self.xoff_var.set(max(-128, min(127, xoff + dx)))
        self.yoff_var.set(max(-128, min(127, yoff + dy)))
        self.apply_selected_offsets()

    def apply_selected_offsets(self):
        if not self.font or self.current_cp is None:
            return

        selected = self._get_selected_codepoints()
        if not selected:
            messagebox.showinfo("Glyph Offsets", "Select one or more glyphs first.")
            return

        xoff = self._read_int_var(self.xoff_var, "Glyph x offset", -128, 127)
        if xoff is None:
            return
        yoff = self._read_int_var(self.yoff_var, "Glyph y offset", -128, 127)
        if yoff is None:
            return

        def apply_offsets(_cp, glyph):
            changed = int(glyph.get("xoff", 0)) != xoff or int(glyph.get("yoff", 0)) != yoff
            glyph["xoff"] = xoff
            glyph["yoff"] = yoff
            return changed

        changed, selected = self._apply_to_selected_glyphs(apply_offsets)
        noun = "glyph" if len(selected) == 1 else "glyphs"
        changed_noun = "glyph" if changed == 1 else "glyphs"
        if changed == 0:
            self._refresh_after_bulk_edit(selected, f"Offsets already matched on {len(selected)} {noun}")
            return
        self._refresh_after_bulk_edit(selected, f"Updated offsets on {changed} {changed_noun}")

    def apply_font_metrics(self):
        font = self.font
        if font is None:
            return

        ascent = self._read_int_var(self.ascent_var, "Font ascent", 0, 255)
        if ascent is None:
            return
        descent = self._read_int_var(self.descent_var, "Font descent", 0, 255)
        if descent is None:
            return

        font.ascent = ascent
        font.descent = descent
        self._update_font_summary()
        if self.current_cp is not None and self.current_cp in font.glyphs:
            self.show_glyph(self.current_cp)
        else:
            self.render_sample_strip()
        self._set_status(f"Updated font metrics: ascent {ascent}, descent {descent}")

    def monospace_selected(self):
        font = self.font
        if font is None:
            return

        selected = self._get_selected_codepoints()
        if not selected:
            messagebox.showinfo("Monospace", "Select one or more glyphs first.")
            return

        target_width = max(int(font.glyphs[cp]["width"]) for cp in selected)
        align = self.mono_align_var.get().lower()

        for cp in selected:
            glyph = font.glyphs[cp]
            old_width = int(glyph["width"])
            if old_width >= target_width:
                continue

            pad = target_width - old_width
            if align == "left":
                right_pad = pad
            elif align == "center":
                right_pad = pad - (pad // 2)
            else:
                right_pad = 0

            widened = []
            mask = (1 << target_width) - 1
            for row in glyph["bitmap"]:
                widened.append((int(row) << right_pad) & mask)

            glyph["width"] = target_width
            glyph["bitmap"] = widened

        self._update_font_summary()
        if self.current_cp in selected:
            self.show_glyph(self.current_cp)
        else:
            self.show_glyph(selected[0])
        self._set_status(f"Monospaced {len(selected)} glyphs to width {target_width} using {align} alignment")

    def delete_selected_glyphs(self, _event=None):
        font = self.font
        if font is None:
            return

        selected = [cp for cp in self._get_selected_codepoints() if cp in font.glyphs]
        if not selected:
            messagebox.showinfo("Delete Glyphs", "Select one or more glyphs first.")
            return

        ordered = sorted(font.glyphs.keys())
        first_index = min(ordered.index(cp) for cp in selected)
        count = len(selected)
        noun = "glyph" if count == 1 else "glyphs"
        if not messagebox.askyesno("Delete Glyphs", f"Delete {count} selected {noun}?"):
            return

        to_delete = set(selected)
        remaining = [cp for cp in ordered if cp not in to_delete]
        next_focus = remaining[min(first_index, len(remaining) - 1)] if remaining else None

        for cp in to_delete:
            font.glyphs.pop(cp, None)
        font.glyph_order = [cp for cp in font.glyph_order if cp not in to_delete]

        self.populate_listbox(
            selected_cps=[next_focus] if next_focus is not None else None,
            active_cp=next_focus,
        )
        self._set_status(f"Deleted {count} {noun}")

    # -------------------- toggle pixel --------------------
    def canvas_click(self,e):
        if not self.font or self.current_cp is None: return
        z = self.zoom
        x0 = getattr(self,'_draw_x0',20)
        y0 = getattr(self,'_draw_y0',20)
        cx = (e.x - x0)//z
        cy = (e.y - y0)//z

        def apply_toggle(_cp, glyph):
            width = int(glyph["width"])
            height = int(glyph["height"])
            if cx < 0 or cy < 0 or cx >= width or cy >= height:
                return False
            mask = 1 << (width - 1 - cx)
            glyph["bitmap"][cy] ^= mask
            return True

        changed, selected = self._apply_to_selected_glyphs(apply_toggle)
        if changed == 0:
            return
        self._refresh_after_bulk_edit(selected, f"Toggled pixel ({cx}, {cy}) in {changed} glyph{'s' if changed != 1 else ''}")

    # -------------------- export --------------------
    def export_hfm(self):
        if not self.font: return
        p = filedialog.asksaveasfilename(defaultextension=".hfm")
        if not p: return
        write_hfm(p, self.font, self.start_var.get(), self.end_var.get())
        self._set_status(f"Saved HFM: {os.path.basename(p)}")
        messagebox.showinfo("OK","Saved")

    def export_bdf(self):
        if not self.font:
            return
        p = filedialog.asksaveasfilename(
            defaultextension=".bdf",
            filetypes=[("BDF files", "*.bdf")],
        )
        if not p:
            return
        try:
            write_bdf(p, self.font)
        except Exception as exc:
            messagebox.showerror("Error", f"Failed to save BDF:\n{exc}")
            return
        self._set_status(f"Saved BDF: {os.path.basename(p)}")
        messagebox.showinfo("OK", "Saved")

# -------------------- main --------------------
if __name__=="__main__":
    root = tk.Tk()
    FontGUI(root)
    root.mainloop()
