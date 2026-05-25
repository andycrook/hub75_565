from __future__ import annotations

import argparse
import math
import re
from pathlib import Path
import tkinter as tk
import webbrowser
from tkinter import colorchooser, filedialog, messagebox, ttk

try:
    from PIL import Image, ImageTk
except ImportError as exc:  # pragma: no cover - desktop dependency
    raise RuntimeError("Pillow is required to run the RGB565 viewer tool") from exc

from rlea_core import (
    ImageAdjustments,
    RESAMPLE_NAMES,
    apply_adjustments,
    fit_frame,
    image_to_rgb565_words,
    rgb565_words_to_image,
    unpack_rgb565,
)


WINDOW_TITLE = "RGB565 Viewer + Converter"
BUY_ME_A_COFFEE_URL = "https://www.buymeacoffee.com/andycrook"
BUY_ME_A_COFFEE_LABEL = "Buy me a coffee"
DEFAULT_GAMMA = 2.2
DEFAULT_BACKGROUND = "#101820"

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DEFAULT_OPEN_DIR = PROJECT_ROOT / "rgb565"
DEFAULT_EXPORT_DIR = PROJECT_ROOT / "rgb565"

RGB565_SUFFIX = ".rgb565"
CUSTOM_SIZE_LABEL = "Custom"
SOURCE_FILE_TYPES = [
    ("RGB565 and image files", "*.rgb565 *.bmp *.png *.jpg *.jpeg *.gif *.webp"),
    ("RGB565 raw", "*.rgb565"),
    ("PNG", "*.png"),
    ("Bitmap", "*.bmp"),
    ("JPEG", "*.jpg *.jpeg"),
    ("GIF", "*.gif"),
    ("WebP", "*.webp"),
    ("All files", "*.*"),
]
EXPORT_IMAGE_FILE_TYPES = [
    ("PNG", "*.png"),
    ("Bitmap", "*.bmp"),
    ("JPEG", "*.jpg"),
    ("WebP", "*.webp"),
]

RESIZE_MODE_CHOICES = (
    ("Fit inside", "contain"),
    ("Zoom to fill", "cover"),
    ("Stretch to fit", "stretch"),
)
RESIZE_MODE_LABEL_TO_VALUE = {label: value for label, value in RESIZE_MODE_CHOICES}
RESIZE_MODE_VALUE_TO_LABEL = {value: label for label, value in RESIZE_MODE_CHOICES}

DIMENSION_PRESETS = (
    (8, 8),
    (10, 10),
    (12, 12),
    (16, 16),
    (24, 24),
    (32, 16),
    (16, 32),
    (32, 32),
    (64, 32),
    (32, 64),
    (64, 64),
    (128, 64),
    (128, 128),
)
PRESET_LABELS = (CUSTOM_SIZE_LABEL,) + tuple(
    f"{width}x{height}" for width, height in DIMENSION_PRESETS
)

if hasattr(Image, "Resampling"):
    PREVIEW_RESAMPLE = Image.Resampling.NEAREST
else:  # pragma: no cover - compatibility fallback for older Pillow builds
    PREVIEW_RESAMPLE = Image.NEAREST

if hasattr(Image, "Transpose"):
    _TRANSPOSE = Image.Transpose
    FLIP_LEFT_RIGHT = _TRANSPOSE.FLIP_LEFT_RIGHT
    FLIP_TOP_BOTTOM = _TRANSPOSE.FLIP_TOP_BOTTOM
    ROTATE_LEFT = _TRANSPOSE.ROTATE_90
    ROTATE_RIGHT = _TRANSPOSE.ROTATE_270
else:  # pragma: no cover - compatibility fallback for older Pillow builds
    FLIP_LEFT_RIGHT = Image.FLIP_LEFT_RIGHT
    FLIP_TOP_BOTTOM = Image.FLIP_TOP_BOTTOM
    ROTATE_LEFT = Image.ROTATE_90
    ROTATE_RIGHT = Image.ROTATE_270


def format_dimensions(width: int, height: int) -> str:
    return f"{int(width)}x{int(height)}"


def parse_dimensions_label(value: str) -> tuple[int, int] | None:
    match = re.search(r"(?<!\d)(\d{1,5})x(\d{1,5})(?!\d)", str(value).strip().lower())
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def parse_positive_int(value: str, field_name: str) -> int:
    try:
        parsed = int(str(value).strip())
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a positive integer") from exc
    if parsed <= 0:
        raise ValueError(f"{field_name} must be > 0")
    return parsed


def parse_hex_color(value: str) -> tuple[int, int, int]:
    raw = str(value).strip()
    if not raw:
        raise ValueError("background color is required")
    if not raw.startswith("#"):
        raw = "#" + raw
    if len(raw) != 7:
        raise ValueError("background color must be in #RRGGBB format")
    try:
        red = int(raw[1:3], 16)
        green = int(raw[3:5], 16)
        blue = int(raw[5:7], 16)
    except ValueError as exc:
        raise ValueError("background color must be in #RRGGBB format") from exc
    return red, green, blue


def rgb_triplet_to_hex(rgb: tuple[int, int, int]) -> str:
    return "#%02X%02X%02X" % (int(rgb[0]) & 0xFF, int(rgb[1]) & 0xFF, int(rgb[2]) & 0xFF)


def resize_mode_value(label: str) -> str:
    raw = str(label).strip()
    if raw in RESIZE_MODE_LABEL_TO_VALUE:
        return RESIZE_MODE_LABEL_TO_VALUE[raw]
    lowered = raw.lower()
    if lowered in RESIZE_MODE_VALUE_TO_LABEL:
        return lowered
    return "contain"


def resize_mode_label(value: str) -> str:
    return RESIZE_MODE_VALUE_TO_LABEL.get(str(value).strip().lower(), RESIZE_MODE_CHOICES[0][0])


def rgb565_words_to_bytes(words) -> bytes:
    output = bytearray(len(words) * 2)
    write_index = 0
    for pixel in words:
        value = int(pixel) & 0xFFFF
        output[write_index] = value & 0xFF
        output[write_index + 1] = (value >> 8) & 0xFF
        write_index += 2
    return bytes(output)


def rgb565_bytes_to_words(data: bytes):
    if len(data) % 2:
        raise ValueError("RGB565 file size must be divisible by 2 bytes")

    out = []
    view = memoryview(data)
    for index in range(0, len(view), 2):
        out.append(int(view[index]) | (int(view[index + 1]) << 8))
    return tuple(out)


def extract_dimensions_from_name(path: Path) -> tuple[int, int] | None:
    matches = list(re.finditer(r"(?<!\d)(\d{1,5})x(\d{1,5})(?!\d)", path.stem.lower()))
    for match in reversed(matches):
        width = int(match.group(1))
        height = int(match.group(2))
        if width > 0 and height > 0:
            return width, height
    return None


def guess_rgb565_dimensions(
    path: Path,
    byte_count: int,
    current_dimensions: tuple[int, int] | None = None,
):
    if byte_count <= 0:
        raise ValueError("RGB565 file is empty")
    if byte_count % 2:
        raise ValueError("RGB565 file size must be divisible by 2 bytes")

    pixel_count = byte_count // 2
    seen = set()
    candidates = []

    def add_candidate(width: int, height: int, reason: str):
        width_val = int(width)
        height_val = int(height)
        if width_val <= 0 or height_val <= 0:
            return
        if width_val * height_val != pixel_count:
            return
        key = (width_val, height_val)
        if key in seen:
            return
        seen.add(key)
        candidates.append((width_val, height_val, reason))

    dims_from_name = extract_dimensions_from_name(path)
    if dims_from_name is not None:
        add_candidate(dims_from_name[0], dims_from_name[1], "filename")

    if current_dimensions is not None:
        add_candidate(current_dimensions[0], current_dimensions[1], "current")

    for preset_width, preset_height in DIMENSION_PRESETS:
        add_candidate(preset_width, preset_height, "preset")

    factor_candidates = []
    limit = int(math.isqrt(pixel_count))
    for height in range(1, limit + 1):
        if pixel_count % height:
            continue
        width = pixel_count // height
        if width < height:
            continue
        ratio = width / float(height)
        closeness = abs(math.log(ratio, 2.0))
        factor_candidates.append((closeness, abs(width - height), -width, width, height))

    factor_candidates.sort()
    for _closeness, _delta, _negative_width, width, height in factor_candidates[:16]:
        add_candidate(width, height, "factor")

    if not candidates:
        raise ValueError("unable to infer RGB565 image dimensions")

    preferred_width, preferred_height, preferred_reason = candidates[0]
    return (preferred_width, preferred_height, preferred_reason), tuple(candidates)


class LabeledScale(ttk.Frame):
    def __init__(
        self,
        master,
        label: str,
        variable: tk.DoubleVar,
        *,
        from_: float,
        to: float,
        precision: int,
        command=None,
    ):
        super().__init__(master)
        self._command = command
        self.variable = variable
        self.precision = precision

        title = ttk.Label(self, text=label)
        title.grid(row=0, column=0, sticky="w")

        self.value_label = ttk.Label(
            self,
            text=self._format(variable.get()),
            width=7,
            anchor="e",
        )
        self.value_label.grid(row=0, column=1, sticky="e")

        scale = ttk.Scale(
            self,
            from_=from_,
            to=to,
            orient="horizontal",
            variable=variable,
            command=self._on_change,
        )
        scale.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        self.columnconfigure(0, weight=1)

    def _format(self, value: float) -> str:
        return f"{float(value):.{self.precision}f}"

    def _on_change(self, raw_value: str):
        try:
            value = float(raw_value)
        except ValueError:
            value = float(self.variable.get())
        self.value_label.configure(text=self._format(value))
        if self._command is not None:
            self._command()


class RGB565ViewerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(WINDOW_TITLE)
        self.root.geometry("1360x940")
        self.root.minsize(1160, 820)

        self.source_path: Path | None = None
        self.source_kind: str | None = None
        self.loaded_source_image = None
        self.source_image = None
        self.preview_image = None
        self.preview_rgb565_words = ()
        self.tk_preview_image: ImageTk.PhotoImage | None = None
        self._preview_origin = (0, 0)
        self._preview_size = (0, 0)
        self._last_good_output_size = (64, 64)
        self._last_good_background = parse_hex_color(DEFAULT_BACKGROUND)
        self._refresh_job: str | None = None
        self._raw_size_candidates: dict[str, tuple[int, int]] = {}
        self._source_note = ""
        self._suspend_events = False

        self.source_path_var = tk.StringVar()
        self.width_var = tk.StringVar(value="64")
        self.height_var = tk.StringVar(value="64")
        self.preset_var = tk.StringVar(value="64x64")
        self.raw_guess_var = tk.StringVar(value="")
        self.resize_mode_var = tk.StringVar(value=resize_mode_label("contain"))
        self.resample_var = tk.StringVar(value="bicubic")
        self.background_var = tk.StringVar(value=DEFAULT_BACKGROUND)
        self.show_grid_var = tk.BooleanVar(value=True)

        self.gamma_var = tk.DoubleVar(value=DEFAULT_GAMMA)
        self.brightness_var = tk.DoubleVar(value=1.0)
        self.exposure_var = tk.DoubleVar(value=0.0)
        self.contrast_var = tk.DoubleVar(value=1.0)
        self.saturation_var = tk.DoubleVar(value=1.0)

        self.status_var = tk.StringVar(value="Open a .rgb565 or image file to begin.")
        self.source_summary_var = tk.StringVar(value="-")
        self.output_summary_var = tk.StringVar(value="-")
        self.rgb565_summary_var = tk.StringVar(value="-")
        self.panel_summary_var = tk.StringVar(value="-")
        self.raw_guess_summary_var = tk.StringVar(
            value="Headerless RGB565 files can be reinterpreted with the dimension guess list."
        )
        self.pixel_info_var = tk.StringVar(value="Hover over the preview for pixel values.")
        self.preview_summary_var = tk.StringVar(value="No preview loaded")

        self._build_theme()
        self._build_layout()
        self._bind_events()
        self._sync_preset_from_dimensions()
        self._render_placeholder()

    def _build_theme(self):
        self.root.configure(bg="#edf3f4")
        style = ttk.Style()
        if "clam" in style.theme_names():
            style.theme_use("clam")

        style.configure(".", background="#edf3f4", foreground="#11202a")
        style.configure("TFrame", background="#edf3f4")
        style.configure("Card.TFrame", background="#ffffff", relief="flat")
        style.configure(
            "Header.TLabel",
            background="#edf3f4",
            foreground="#11202a",
            font=("Segoe UI Semibold", 22),
        )
        style.configure(
            "Subhead.TLabel",
            background="#ffffff",
            foreground="#36505e",
            font=("Segoe UI Semibold", 10),
        )
        style.configure(
            "Muted.TLabel",
            background="#edf3f4",
            foreground="#56717f",
            font=("Segoe UI", 10),
        )
        style.configure(
            "Value.TLabel",
            background="#ffffff",
            foreground="#11202a",
            font=("Segoe UI", 11),
        )
        style.configure("TButton", padding=(10, 7), font=("Segoe UI", 10))
        style.configure("Accent.TButton", padding=(12, 8), font=("Segoe UI Semibold", 10))
        style.configure("TLabelframe", background="#ffffff", foreground="#11202a")
        style.configure(
            "TLabelframe.Label",
            background="#ffffff",
            foreground="#11202a",
            font=("Segoe UI Semibold", 10),
        )
        style.configure("TCheckbutton", background="#ffffff", foreground="#11202a")
        style.configure("TEntry", fieldbackground="#ffffff")
        style.configure("TCombobox", fieldbackground="#ffffff")
        style.configure("TSpinbox", fieldbackground="#ffffff")

    def _build_layout(self):
        outer = ttk.Frame(self.root, padding=14)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=0)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(1, weight=1)

        header = ttk.Frame(outer)
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        header.columnconfigure(0, weight=1)
        header.columnconfigure(1, weight=0)
        ttk.Label(header, text=WINDOW_TITLE, style="Header.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            header,
            text=(
                "Open raw RGB565 or common image files, preview the exact RGB565 output, "
                "adjust tone, and export panel-ready assets."
            ),
            style="Muted.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))
        support = tk.Label(
            header,
            text=BUY_ME_A_COFFEE_LABEL,
            fg="#2563eb",
            bg="#edf3f4",
            cursor="hand2",
            font=("Segoe UI", 13, "underline"),
        )
        support.grid(row=0, column=1, rowspan=2, sticky="e")
        support.bind("<Button-1>", lambda _event: self.open_support_link())

        left = ttk.Frame(outer, style="Card.TFrame", padding=12)
        left.grid(row=1, column=0, sticky="nsw", padx=(0, 12))
        left.columnconfigure(0, weight=1)

        right = ttk.Frame(outer, style="Card.TFrame", padding=12)
        right.grid(row=1, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)

        self._build_left_panel(left)
        self._build_right_panel(right)

        status = ttk.Label(
            self.root,
            textvariable=self.status_var,
            anchor="w",
            padding=(14, 8),
            style="Muted.TLabel",
        )
        status.pack(fill="x")

    def open_support_link(self):
        try:
            webbrowser.open(BUY_ME_A_COFFEE_URL)
            self.status_var.set("Opened support link in your browser")
        except Exception as exc:
            messagebox.showerror("RGB565 Viewer + Converter", f"Failed to open link:\n{exc}")

    def _build_left_panel(self, parent: ttk.Frame):
        parent.columnconfigure(0, weight=1)
        parent.columnconfigure(1, weight=1)

        left_column = ttk.Frame(parent, style="Card.TFrame")
        left_column.grid(row=0, column=0, sticky="new", padx=(0, 10))
        left_column.columnconfigure(0, weight=1)

        right_column = ttk.Frame(parent, style="Card.TFrame")
        right_column.grid(row=0, column=1, sticky="new")
        right_column.columnconfigure(0, weight=1)

        source_frame = ttk.LabelFrame(left_column, text="Source", padding=10)
        source_frame.grid(row=0, column=0, sticky="ew")
        source_frame.columnconfigure(0, weight=1)
        source_frame.columnconfigure(1, weight=1)
        source_frame.columnconfigure(2, weight=1)

        ttk.Entry(source_frame, textvariable=self.source_path_var).grid(
            row=0, column=0, columnspan=3, sticky="ew"
        )
        ttk.Button(
            source_frame,
            text="Open...",
            style="Accent.TButton",
            command=self.open_source_dialog,
        ).grid(row=1, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(source_frame, text="Reload", command=self.reload_source).grid(
            row=1, column=1, sticky="ew", padx=(8, 0), pady=(8, 0)
        )
        ttk.Button(source_frame, text="Revert", command=self.revert_to_loaded_source).grid(
            row=1, column=2, sticky="ew", padx=(8, 0), pady=(8, 0)
        )
        ttk.Button(
            source_frame,
            text="Export RGB565...",
            style="Accent.TButton",
            command=self.export_rgb565_dialog,
        ).grid(row=2, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(source_frame, text="Export Image...", command=self.export_image_dialog).grid(
            row=2, column=1, sticky="ew", padx=(8, 0), pady=(8, 0)
        )
        ttk.Button(source_frame, text="Use Source Size", command=self.use_source_size).grid(
            row=2, column=2, sticky="ew", padx=(8, 0), pady=(8, 0)
        )

        geometry_frame = ttk.LabelFrame(left_column, text="Geometry", padding=10)
        geometry_frame.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        for column in range(4):
            geometry_frame.columnconfigure(column, weight=1)

        ttk.Label(geometry_frame, text="Width", style="Subhead.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(geometry_frame, text="Height", style="Subhead.TLabel").grid(
            row=0, column=1, sticky="w"
        )
        ttk.Spinbox(
            geometry_frame,
            from_=1,
            to=4096,
            increment=1,
            textvariable=self.width_var,
            width=8,
        ).grid(row=1, column=0, sticky="ew", pady=(4, 0))
        ttk.Spinbox(
            geometry_frame,
            from_=1,
            to=4096,
            increment=1,
            textvariable=self.height_var,
            width=8,
        ).grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(4, 0))
        ttk.Button(geometry_frame, text="Swap", command=self.swap_output_dimensions).grid(
            row=1, column=2, sticky="ew", padx=(8, 0), pady=(4, 0)
        )
        ttk.Checkbutton(
            geometry_frame,
            text="Grid",
            variable=self.show_grid_var,
            command=self._render_preview,
        ).grid(row=1, column=3, sticky="e", padx=(8, 0), pady=(4, 0))

        ttk.Label(geometry_frame, text="Preset", style="Subhead.TLabel").grid(
            row=2, column=0, sticky="w", pady=(10, 0)
        )
        self.preset_combo = ttk.Combobox(
            geometry_frame,
            textvariable=self.preset_var,
            values=PRESET_LABELS,
            state="readonly",
        )
        self.preset_combo.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(4, 0))

        ttk.Label(geometry_frame, text="Raw Guess", style="Subhead.TLabel").grid(
            row=2, column=2, sticky="w", padx=(8, 0), pady=(10, 0)
        )
        self.raw_guess_combo = ttk.Combobox(
            geometry_frame,
            textvariable=self.raw_guess_var,
            values=(),
            state="disabled",
        )
        self.raw_guess_combo.grid(row=3, column=2, columnspan=2, sticky="ew", padx=(8, 0), pady=(4, 0))

        ttk.Label(geometry_frame, text="Resize Mode", style="Subhead.TLabel").grid(
            row=4, column=0, sticky="w", pady=(10, 0)
        )
        self.resize_mode_combo = ttk.Combobox(
            geometry_frame,
            textvariable=self.resize_mode_var,
            values=tuple(label for label, _value in RESIZE_MODE_CHOICES),
            state="readonly",
        )
        self.resize_mode_combo.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(4, 0))

        ttk.Label(geometry_frame, text="Resample", style="Subhead.TLabel").grid(
            row=4, column=2, sticky="w", padx=(8, 0), pady=(10, 0)
        )
        self.resample_combo = ttk.Combobox(
            geometry_frame,
            textvariable=self.resample_var,
            values=RESAMPLE_NAMES,
            state="readonly",
        )
        self.resample_combo.grid(row=5, column=2, columnspan=2, sticky="ew", padx=(8, 0), pady=(4, 0))

        ttk.Label(geometry_frame, text="Background", style="Subhead.TLabel").grid(
            row=6, column=0, sticky="w", pady=(10, 0)
        )
        background_row = ttk.Frame(geometry_frame, style="Card.TFrame")
        background_row.grid(row=7, column=0, columnspan=4, sticky="ew", pady=(4, 0))
        background_row.columnconfigure(0, weight=1)
        self.background_entry = ttk.Entry(background_row, textvariable=self.background_var)
        self.background_entry.grid(row=0, column=0, sticky="ew")
        ttk.Button(background_row, text="Pick", command=self.choose_background_color).grid(
            row=0, column=1, sticky="ew", padx=(8, 0)
        )

        edit_frame = ttk.LabelFrame(left_column, text="Image Tools", padding=10)
        edit_frame.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        edit_frame.columnconfigure(0, weight=1)
        edit_frame.columnconfigure(1, weight=1)

        ttk.Button(edit_frame, text="Rotate Left", command=self.rotate_left).grid(
            row=0, column=0, sticky="ew"
        )
        ttk.Button(edit_frame, text="Rotate Right", command=self.rotate_right).grid(
            row=0, column=1, sticky="ew", padx=(8, 0)
        )
        ttk.Button(edit_frame, text="Flip Horizontal", command=self.flip_horizontal).grid(
            row=1, column=0, sticky="ew", pady=(8, 0)
        )
        ttk.Button(edit_frame, text="Flip Vertical", command=self.flip_vertical).grid(
            row=1, column=1, sticky="ew", padx=(8, 0), pady=(8, 0)
        )

        adjustments_frame = ttk.LabelFrame(right_column, text="Adjustments", padding=10)
        adjustments_frame.grid(row=0, column=0, sticky="ew")
        adjustments_frame.columnconfigure(0, weight=1)

        self.gamma_scale = LabeledScale(
            adjustments_frame,
            "Gamma",
            self.gamma_var,
            from_=0.2,
            to=4.0,
            precision=2,
            command=self.schedule_refresh,
        )
        self.gamma_scale.grid(row=0, column=0, sticky="ew")
        self.brightness_scale = LabeledScale(
            adjustments_frame,
            "Brightness",
            self.brightness_var,
            from_=0.2,
            to=2.5,
            precision=2,
            command=self.schedule_refresh,
        )
        self.brightness_scale.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        self.exposure_scale = LabeledScale(
            adjustments_frame,
            "Exposure",
            self.exposure_var,
            from_=-2.0,
            to=2.0,
            precision=2,
            command=self.schedule_refresh,
        )
        self.exposure_scale.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        self.contrast_scale = LabeledScale(
            adjustments_frame,
            "Contrast",
            self.contrast_var,
            from_=0.2,
            to=2.5,
            precision=2,
            command=self.schedule_refresh,
        )
        self.contrast_scale.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        self.saturation_scale = LabeledScale(
            adjustments_frame,
            "Saturation",
            self.saturation_var,
            from_=0.0,
            to=2.5,
            precision=2,
            command=self.schedule_refresh,
        )
        self.saturation_scale.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        ttk.Button(adjustments_frame, text="Reset Tone", command=self.reset_adjustments).grid(
            row=5, column=0, sticky="ew", pady=(12, 0)
        )

        details_frame = ttk.LabelFrame(right_column, text="Details", padding=10)
        details_frame.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        details_frame.columnconfigure(0, weight=1)
        details_frame.columnconfigure(1, weight=1)
        self._detail_row(details_frame, 0, "Source", self.source_summary_var)
        self._detail_row(details_frame, 1, "Output", self.output_summary_var)
        self._detail_row(details_frame, 2, "RGB565", self.rgb565_summary_var)
        self._detail_row(details_frame, 3, "Notes", self.panel_summary_var)
        ttk.Label(details_frame, text="Guessing", style="Subhead.TLabel").grid(
            row=4, column=0, sticky="nw", pady=(10, 0)
        )
        ttk.Label(
            details_frame,
            textvariable=self.raw_guess_summary_var,
            style="Value.TLabel",
            wraplength=250,
            justify="left",
        ).grid(row=4, column=1, sticky="w", pady=(10, 0))

    def _detail_row(self, parent: ttk.Frame, row: int, title: str, variable: tk.StringVar):
        ttk.Label(parent, text=title, style="Subhead.TLabel").grid(row=row, column=0, sticky="w")
        ttk.Label(parent, textvariable=variable, style="Value.TLabel", wraplength=250, justify="left").grid(
            row=row, column=1, sticky="w", padx=(10, 0)
        )

    def _build_right_panel(self, parent: ttk.Frame):
        header = ttk.Frame(parent, style="Card.TFrame")
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="Preview", style="Subhead.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(header, textvariable=self.preview_summary_var, style="Value.TLabel").grid(
            row=0, column=1, sticky="e"
        )

        preview_shell = ttk.Frame(parent, style="Card.TFrame")
        preview_shell.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        preview_shell.columnconfigure(0, weight=1)
        preview_shell.rowconfigure(0, weight=1)

        self.preview_canvas = tk.Canvas(
            preview_shell,
            background="#0b1720",
            highlightthickness=0,
            bd=0,
            relief="flat",
        )
        self.preview_canvas.grid(row=0, column=0, sticky="nsew")

        footer = ttk.Frame(parent, style="Card.TFrame")
        footer.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.pixel_info_var, style="Value.TLabel").grid(
            row=0, column=0, sticky="w"
        )

    def _bind_events(self):
        self.root.bind("<Control-o>", lambda _event: self.open_source_dialog())
        self.root.bind("<Control-s>", lambda _event: self.export_rgb565_dialog())
        self.root.bind("<Control-S>", lambda _event: self.export_image_dialog())
        self.root.bind("<F5>", lambda _event: self.reload_source())

        self.preset_combo.bind("<<ComboboxSelected>>", self._on_preset_selected)
        self.raw_guess_combo.bind("<<ComboboxSelected>>", self._on_raw_guess_selected)
        self.resize_mode_combo.bind("<<ComboboxSelected>>", lambda _event: self.schedule_refresh())
        self.resample_combo.bind("<<ComboboxSelected>>", lambda _event: self.schedule_refresh())

        self.preview_canvas.bind("<Configure>", lambda _event: self._render_preview())
        self.preview_canvas.bind("<Motion>", self._on_preview_motion)
        self.preview_canvas.bind("<Leave>", self._on_preview_leave)

        self.background_entry.bind("<Return>", lambda _event: self._commit_background())
        self.background_entry.bind("<FocusOut>", lambda _event: self._commit_background())

        self.width_var.trace_add("write", self._on_dimension_changed)
        self.height_var.trace_add("write", self._on_dimension_changed)

    def _on_dimension_changed(self, *_args):
        if self._suspend_events:
            return
        self._sync_preset_from_dimensions()
        self.schedule_refresh()

    def _on_preset_selected(self, _event=None):
        if self._suspend_events:
            return
        dims = parse_dimensions_label(self.preset_var.get())
        if dims is None:
            return
        self._set_output_size(dims[0], dims[1])
        self.schedule_refresh()

    def _on_raw_guess_selected(self, _event=None):
        dims = self._raw_size_candidates.get(self.raw_guess_var.get())
        if dims is None:
            return
        self._set_output_size(dims[0], dims[1])
        if self.source_kind == "rgb565" and self.source_path is not None:
            self.load_source(self.source_path, use_current_raw_dimensions=True, reset_output_size=False)

    def _set_output_size(self, width: int, height: int):
        self._suspend_events = True
        try:
            self.width_var.set(str(int(width)))
            self.height_var.set(str(int(height)))
            self._last_good_output_size = (int(width), int(height))
            self._sync_preset_from_dimensions()
        finally:
            self._suspend_events = False

    def _sync_preset_from_dimensions(self):
        try:
            dims = self._parse_output_size(strict=True)
        except ValueError:
            self.preset_var.set(CUSTOM_SIZE_LABEL)
            return
        label = format_dimensions(dims[0], dims[1])
        if label in PRESET_LABELS:
            self.preset_var.set(label)
        else:
            self.preset_var.set(CUSTOM_SIZE_LABEL)

    def _parse_output_size(self, *, strict: bool) -> tuple[int, int]:
        try:
            width = parse_positive_int(self.width_var.get(), "width")
            height = parse_positive_int(self.height_var.get(), "height")
        except ValueError:
            if strict:
                raise
            return self._last_good_output_size

        self._last_good_output_size = (width, height)
        return width, height

    def _current_background_rgb(self) -> tuple[int, int, int]:
        try:
            rgb = parse_hex_color(self.background_var.get())
        except ValueError:
            return self._last_good_background
        self._last_good_background = rgb
        return rgb

    def _commit_background(self):
        try:
            normalized = rgb_triplet_to_hex(parse_hex_color(self.background_var.get()))
        except ValueError as exc:
            self.status_var.set(str(exc))
            return
        self.background_var.set(normalized)
        self._last_good_background = parse_hex_color(normalized)
        self.schedule_refresh()

    def _current_adjustments(self) -> ImageAdjustments:
        return ImageAdjustments(
            gamma=float(self.gamma_var.get()),
            brightness=float(self.brightness_var.get()),
            exposure=float(self.exposure_var.get()),
            contrast=float(self.contrast_var.get()),
            saturation=float(self.saturation_var.get()),
        )

    def _default_gamma_for_source_kind(self, source_kind: str | None) -> float:
        if source_kind == "rgb565":
            return 1.0
        return DEFAULT_GAMMA

    def choose_background_color(self):
        chosen, hex_value = colorchooser.askcolor(color=self.background_var.get(), parent=self.root)
        if chosen is None or not hex_value:
            return
        self.background_var.set(hex_value.upper())
        self._commit_background()

    def schedule_refresh(self):
        if self._refresh_job is not None:
            self.root.after_cancel(self._refresh_job)
        self._refresh_job = self.root.after(80, self.refresh_preview)

    def refresh_preview(self):
        self._refresh_job = None
        if self.source_image is None:
            self.preview_image = None
            self.preview_rgb565_words = ()
            self._update_detail_labels()
            self._render_placeholder()
            return

        try:
            output_width, output_height = self._parse_output_size(strict=False)
            fitted = fit_frame(
                self.source_image,
                output_width,
                output_height,
                resize_mode=resize_mode_value(self.resize_mode_var.get()),
                resample_filter=str(self.resample_var.get()).lower(),
                background_rgb=self._current_background_rgb(),
            )
            adjusted = apply_adjustments(fitted, self._current_adjustments())
            words = image_to_rgb565_words(adjusted)
            quantized = rgb565_words_to_image(words, output_width, output_height)
        except Exception as exc:
            self.preview_image = None
            self.preview_rgb565_words = ()
            self.status_var.set(f"Preview update failed: {exc}")
            self.preview_summary_var.set("Preview unavailable")
            self._update_detail_labels(error_text=str(exc))
            self._render_placeholder(str(exc))
            return

        self.preview_image = quantized
        self.preview_rgb565_words = words
        self._update_detail_labels()
        self._render_preview()
        self.status_var.set(
            f"Preview ready: {output_width}x{output_height}, {len(words) * 2:,} RGB565 bytes."
        )

    def _update_detail_labels(self, error_text: str | None = None):
        if self.source_image is None:
            self.source_summary_var.set("-")
            self.output_summary_var.set("-")
            self.rgb565_summary_var.set("-")
            self.panel_summary_var.set(error_text or "-")
            self.preview_summary_var.set("No preview loaded")
            return

        source_width, source_height = self.source_image.size
        source_kind = "RGB565 raw" if self.source_kind == "rgb565" else "Image"
        source_name = self.source_path.name if self.source_path is not None else "unsaved"
        self.source_summary_var.set(
            f"{source_kind} {source_name} ({source_width}x{source_height})"
        )

        try:
            output_width, output_height = self._parse_output_size(strict=True)
            output_label = format_dimensions(output_width, output_height)
        except ValueError:
            output_width, output_height = self._last_good_output_size
            output_label = format_dimensions(output_width, output_height) + " (last valid)"
        self.output_summary_var.set(output_label)

        if self.preview_rgb565_words:
            byte_count = len(self.preview_rgb565_words) * 2
            self.rgb565_summary_var.set(f"{byte_count:,} bytes raw, {len(self.preview_rgb565_words):,} pixels")
            scale_note = ""
            if self.preview_image is not None:
                scale_note = f"  |  preview {self.preview_image.width}x{self.preview_image.height}"
            self.preview_summary_var.set(f"RGB565 output {output_label}{scale_note}")
        else:
            self.rgb565_summary_var.set("-")
            self.preview_summary_var.set("Preview unavailable")

        notes = []
        if output_width == 64 and output_height == 64:
            notes.append("64x64 HUB75 panel sized")
        elif output_width == 128 and output_height == 64:
            notes.append("128x64 HUB75 panel sized")
        elif output_width <= 128 and output_height <= 64:
            notes.append("sprite-friendly size")
        else:
            notes.append("custom size")

        if self._source_note:
            notes.append(self._source_note)
        if error_text:
            notes.append(error_text)
        self.panel_summary_var.set(" | ".join(notes))

    def _load_standard_image(self, path: Path):
        with Image.open(path) as source_image:
            frame_count = int(getattr(source_image, "n_frames", 1) or 1)
            if frame_count > 1:
                source_image.seek(0)
            image = source_image.convert("RGBA")
        note = ""
        if frame_count > 1:
            note = f"loaded first frame of animated source ({frame_count} frames)"
        return image, note

    def _load_rgb565_image(self, path: Path, *, use_current_raw_dimensions: bool):
        data = path.read_bytes()
        current_dims = None
        try:
            current_dims = self._parse_output_size(strict=True)
        except ValueError:
            current_dims = None

        if use_current_raw_dimensions:
            if current_dims is None:
                raise ValueError("set width and height before reloading the RGB565 file")
            width, height = current_dims
            expected_bytes = width * height * 2
            if expected_bytes != len(data):
                raise ValueError(
                    f"current dimensions {width}x{height} expect {expected_bytes} bytes, file has {len(data)}"
                )
            reason = "using current width and height"
            candidates = ((width, height, "current"),)
        else:
            (width, height, reason), candidates = guess_rgb565_dimensions(
                path,
                len(data),
                current_dimensions=current_dims,
            )

        words = rgb565_bytes_to_words(data)
        if len(words) != width * height:
            raise ValueError("RGB565 pixel count does not match the chosen dimensions")

        image = rgb565_words_to_image(words, width, height).convert("RGBA")
        return image, width, height, reason, candidates

    def load_source(
        self,
        path: Path | str,
        *,
        use_current_raw_dimensions: bool = False,
        reset_output_size: bool = True,
    ):
        source_path = Path(path)
        if not source_path.exists():
            raise FileNotFoundError(source_path)

        self.source_path = source_path
        self.source_path_var.set(str(source_path))

        if source_path.suffix.lower() == RGB565_SUFFIX:
            image, width, height, reason, candidates = self._load_rgb565_image(
                source_path,
                use_current_raw_dimensions=use_current_raw_dimensions,
            )
            self.source_kind = "rgb565"
            self._source_note = reason
            self._populate_raw_guess_candidates(candidates)
            if reset_output_size or not use_current_raw_dimensions:
                self._set_output_size(width, height)
        else:
            image, note = self._load_standard_image(source_path)
            self.source_kind = "image"
            self._source_note = note
            self._populate_raw_guess_candidates(())
            if reset_output_size:
                self._set_output_size(image.width, image.height)

        self.gamma_var.set(self._default_gamma_for_source_kind(self.source_kind))
        self.loaded_source_image = image.copy()
        self.source_image = image.copy()
        self.refresh_preview()

    def _populate_raw_guess_candidates(self, candidates):
        self._raw_size_candidates = {}
        values = []
        for width, height, reason in candidates:
            label = f"{width}x{height} ({reason})"
            self._raw_size_candidates[label] = (int(width), int(height))
            values.append(label)

        if values:
            self.raw_guess_combo.configure(values=tuple(values), state="readonly")
            if self.raw_guess_var.get() not in self._raw_size_candidates:
                self.raw_guess_var.set(values[0])
            self.raw_guess_summary_var.set(
                "Guesses: " + ", ".join(values[:8]) + ("..." if len(values) > 8 else "")
            )
        else:
            self.raw_guess_combo.configure(values=(), state="disabled")
            self.raw_guess_var.set("")
            self.raw_guess_summary_var.set(
                "Headerless RGB565 files can be reinterpreted with the dimension guess list."
            )

    def open_source_dialog(self):
        initial_dir = self._initial_open_dir()
        path = filedialog.askopenfilename(
            parent=self.root,
            title="Open RGB565 or image file",
            initialdir=str(initial_dir),
            filetypes=SOURCE_FILE_TYPES,
        )
        if not path:
            return

        try:
            self.load_source(path, use_current_raw_dimensions=False, reset_output_size=True)
        except Exception as exc:
            self.status_var.set(f"Load failed: {exc}")
            messagebox.showerror("Load failed", str(exc), parent=self.root)

    def _initial_open_dir(self) -> Path:
        if self.source_path is not None:
            return self.source_path.parent
        if DEFAULT_OPEN_DIR.exists():
            return DEFAULT_OPEN_DIR
        return PROJECT_ROOT

    def reload_source(self):
        if self.source_path is None:
            self.status_var.set("Choose a source file before reloading.")
            return
        try:
            self.load_source(
                self.source_path,
                use_current_raw_dimensions=(self.source_kind == "rgb565"),
                reset_output_size=False,
            )
        except Exception as exc:
            self.status_var.set(f"Reload failed: {exc}")
            messagebox.showerror("Reload failed", str(exc), parent=self.root)

    def revert_to_loaded_source(self):
        if self.loaded_source_image is None:
            self.status_var.set("Open a source file before reverting.")
            return
        self.source_image = self.loaded_source_image.copy()
        self.refresh_preview()
        self.status_var.set("Reverted image transforms to the last loaded source.")

    def use_source_size(self):
        if self.source_image is None:
            self.status_var.set("Open a source file before copying its size.")
            return
        self._set_output_size(self.source_image.width, self.source_image.height)
        self.refresh_preview()

    def swap_output_dimensions(self):
        width, height = self._parse_output_size(strict=False)
        self._set_output_size(height, width)
        self.refresh_preview()

    def reset_adjustments(self):
        self.gamma_var.set(self._default_gamma_for_source_kind(self.source_kind))
        self.brightness_var.set(1.0)
        self.exposure_var.set(0.0)
        self.contrast_var.set(1.0)
        self.saturation_var.set(1.0)
        self.schedule_refresh()

    def _apply_transform(self, transform_name: str, transpose_op):
        if self.source_image is None:
            self.status_var.set(f"Open a source file before using {transform_name}.")
            return
        self.source_image = self.source_image.transpose(transpose_op)
        self.refresh_preview()
        self.status_var.set(f"Applied {transform_name}.")

    def rotate_left(self):
        self._apply_transform("rotate left", ROTATE_LEFT)

    def rotate_right(self):
        self._apply_transform("rotate right", ROTATE_RIGHT)

    def flip_horizontal(self):
        self._apply_transform("horizontal flip", FLIP_LEFT_RIGHT)

    def flip_vertical(self):
        self._apply_transform("vertical flip", FLIP_TOP_BOTTOM)

    def _default_rgb565_path(self) -> Path:
        base_dir = self.source_path.parent if self.source_path is not None else DEFAULT_EXPORT_DIR
        width, height = self._parse_output_size(strict=False)
        stem = "untitled"
        if self.source_path is not None:
            stem = self.source_path.stem
        if f"{width}x{height}" not in stem:
            stem = f"{stem}_{width}x{height}"
        return base_dir / f"{stem}.rgb565"

    def _default_export_image_path(self) -> Path:
        base_dir = self.source_path.parent if self.source_path is not None else DEFAULT_EXPORT_DIR
        stem = self.source_path.stem if self.source_path is not None else "preview"
        return base_dir / f"{stem}_preview.png"

    def export_rgb565_dialog(self):
        if not self.preview_rgb565_words:
            messagebox.showerror(
                "Nothing to export",
                "Open a source file and generate a preview before exporting RGB565.",
                parent=self.root,
            )
            return

        default_path = self._default_rgb565_path()
        chosen = filedialog.asksaveasfilename(
            parent=self.root,
            title="Export RGB565",
            initialdir=str(default_path.parent),
            initialfile=default_path.name,
            defaultextension=RGB565_SUFFIX,
            filetypes=[("RGB565 raw", "*.rgb565"), ("All files", "*.*")],
        )
        if not chosen:
            return

        output_path = Path(chosen)
        output_path.write_bytes(rgb565_words_to_bytes(self.preview_rgb565_words))
        self.status_var.set(
            f"Exported {len(self.preview_rgb565_words) * 2:,} bytes of RGB565 to {output_path.name}."
        )

    def export_image_dialog(self):
        if self.preview_image is None:
            messagebox.showerror(
                "Nothing to export",
                "Open a source file and generate a preview before exporting an image.",
                parent=self.root,
            )
            return

        default_path = self._default_export_image_path()
        chosen = filedialog.asksaveasfilename(
            parent=self.root,
            title="Export Preview Image",
            initialdir=str(default_path.parent),
            initialfile=default_path.name,
            defaultextension=".png",
            filetypes=EXPORT_IMAGE_FILE_TYPES + [("All files", "*.*")],
        )
        if not chosen:
            return

        output_path = Path(chosen)
        image_to_save = self.preview_image.convert("RGB")
        image_to_save.save(output_path)
        self.status_var.set(f"Exported preview image to {output_path.name}.")

    def _render_placeholder(self, message: str | None = None):
        canvas = self.preview_canvas
        width = max(1, canvas.winfo_width())
        height = max(1, canvas.winfo_height())
        canvas.delete("all")
        canvas.create_rectangle(0, 0, width, height, fill="#0b1720", outline="")
        canvas.create_text(
            width // 2,
            height // 2 - 12,
            text="RGB565 preview",
            fill="#d9e6ef",
            font=("Segoe UI Semibold", 18),
        )
        canvas.create_text(
            width // 2,
            height // 2 + 16,
            text=message or "Open a .rgb565 or image file to preview it here.",
            fill="#7f93a5",
            font=("Segoe UI", 11),
            width=max(320, width - 80),
        )
        self._preview_origin = (0, 0)
        self._preview_size = (0, 0)
        self.tk_preview_image = None

    def _render_preview(self):
        canvas = self.preview_canvas
        canvas_width = max(1, canvas.winfo_width())
        canvas_height = max(1, canvas.winfo_height())

        if self.preview_image is None:
            self._render_placeholder()
            return

        image = self.preview_image
        fit_scale = min(canvas_width / float(image.width), canvas_height / float(image.height))
        if fit_scale >= 1.0:
            display_scale = max(1.0, float(int(fit_scale)))
        else:
            display_scale = fit_scale

        display_width = max(1, int(round(image.width * display_scale)))
        display_height = max(1, int(round(image.height * display_scale)))
        display = image.resize((display_width, display_height), resample=PREVIEW_RESAMPLE)
        self.tk_preview_image = ImageTk.PhotoImage(display)

        origin_x = (canvas_width - display_width) // 2
        origin_y = (canvas_height - display_height) // 2
        self._preview_origin = (origin_x, origin_y)
        self._preview_size = (display_width, display_height)

        canvas.delete("all")
        canvas.create_rectangle(0, 0, canvas_width, canvas_height, fill="#0b1720", outline="")
        canvas.create_rectangle(
            origin_x - 1,
            origin_y - 1,
            origin_x + display_width + 1,
            origin_y + display_height + 1,
            outline="#2c4251",
        )
        canvas.create_image(origin_x, origin_y, anchor="nw", image=self.tk_preview_image)

        if self.show_grid_var.get() and display_width >= image.width * 8 and display_height >= image.height * 8:
            x_step = display_width / float(image.width)
            y_step = display_height / float(image.height)
            for index in range(1, image.width):
                x = origin_x + int(round(index * x_step))
                canvas.create_line(x, origin_y, x, origin_y + display_height, fill="#33515f")
            for index in range(1, image.height):
                y = origin_y + int(round(index * y_step))
                canvas.create_line(origin_x, y, origin_x + display_width, y, fill="#33515f")

        scale_label = f"fit {display_width}x{display_height}"
        if image.width > 0:
            scale_ratio = display_width / float(image.width)
            scale_label += f" | scale {scale_ratio:.2f}x"
        canvas.create_text(
            14,
            14,
            anchor="nw",
            text=f"{image.width}x{image.height} RGB565 | {scale_label}",
            fill="#d9e6ef",
            font=("Segoe UI", 10),
        )

    def _on_preview_leave(self, _event=None):
        self.pixel_info_var.set("Hover over the preview for pixel values.")

    def _on_preview_motion(self, event):
        if self.preview_image is None or not self.preview_rgb565_words:
            self.pixel_info_var.set("Hover over the preview for pixel values.")
            return

        origin_x, origin_y = self._preview_origin
        display_width, display_height = self._preview_size
        if display_width <= 0 or display_height <= 0:
            self.pixel_info_var.set("Hover over the preview for pixel values.")
            return

        if not (
            origin_x <= event.x < origin_x + display_width
            and origin_y <= event.y < origin_y + display_height
        ):
            self.pixel_info_var.set("Hover over the preview for pixel values.")
            return

        image = self.preview_image
        rel_x = (event.x - origin_x) / float(display_width)
        rel_y = (event.y - origin_y) / float(display_height)
        pixel_x = min(image.width - 1, max(0, int(rel_x * image.width)))
        pixel_y = min(image.height - 1, max(0, int(rel_y * image.height)))
        index = pixel_y * image.width + pixel_x
        pixel565 = int(self.preview_rgb565_words[index]) & 0xFFFF
        red, green, blue = unpack_rgb565(pixel565)
        self.pixel_info_var.set(
            f"x={pixel_x}, y={pixel_y}  |  RGB {red:3d}, {green:3d}, {blue:3d}  |  0x{pixel565:04X}"
        )


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Desktop RGB565 viewer, converter, and minor editor."
    )
    parser.add_argument(
        "path",
        nargs="?",
        help="optional source file to open (.rgb565, .bmp, .png, .jpg, .gif, .webp)",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=None,
        help="optional width override for raw .rgb565 files",
    )
    parser.add_argument(
        "--height",
        type=int,
        default=None,
        help="optional height override for raw .rgb565 files",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    root = tk.Tk()
    app = RGB565ViewerApp(root)

    if args.width is not None and args.height is not None:
        app._set_output_size(int(args.width), int(args.height))

    if args.path:
        initial_path = Path(args.path)

        def load_initial_path():
            try:
                app.load_source(
                    initial_path,
                    use_current_raw_dimensions=(
                        initial_path.suffix.lower() == RGB565_SUFFIX
                        and args.width is not None
                        and args.height is not None
                    ),
                    reset_output_size=not (
                        initial_path.suffix.lower() == RGB565_SUFFIX
                        and args.width is not None
                        and args.height is not None
                    ),
                )
            except Exception as exc:
                app.status_var.set(f"Initial load failed: {exc}")
                messagebox.showerror("Initial load failed", str(exc), parent=root)

        root.after(120, load_initial_path)

    root.mainloop()


if __name__ == "__main__":
    main()