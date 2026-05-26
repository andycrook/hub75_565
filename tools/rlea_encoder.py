from __future__ import annotations

from pathlib import Path
import queue
import threading
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, ttk

try:
    from PIL import Image, ImageTk
except ImportError as exc:  # pragma: no cover - desktop dependency
    raise RuntimeError("Pillow is required to run the RLEA encoder UI") from exc

from rlea_core import (
    EncoderOptions,
    ImageAdjustments,
    PreparedAnimation,
    RESAMPLE_NAMES,
    EncodedAnimation,
    encode_animation,
    prepare_animation_source,
    rgb565_words_to_image,
    validate_round_trip,
    write_rlea,
)


WINDOW_TITLE = "RLEA Animation Encoder"
BUY_ME_A_COFFEE_URL = "https://www.buymeacoffee.com/andycrook"
BUY_ME_A_COFFEE_LABEL = "Buy me a coffee"
SOURCE_FILE_TYPES = [
    ("Supported media", "*.gif *.png *.bmp *.jpg *.jpeg *.webp *.mkv *.mp4 *.mov *.avi *.webm *.m4v"),
    ("Video", "*.mkv *.mp4 *.mov *.avi *.webm *.m4v"),
    ("GIF", "*.gif"),
    ("PNG", "*.png"),
    ("Bitmap", "*.bmp"),
    ("JPEG", "*.jpg *.jpeg"),
    ("WebP", "*.webp"),
    ("All files", "*.*"),
]

if hasattr(Image, "Resampling"):
    PREVIEW_RESAMPLE = Image.Resampling.NEAREST
else:  # pragma: no cover - compatibility fallback for older Pillow builds
    PREVIEW_RESAMPLE = Image.NEAREST


RESIZE_MODE_CHOICES = (
    ("Fit inside", "contain"),
    ("Zoom to fill", "cover"),
    ("Stretch to fit", "stretch"),
)
RESIZE_MODE_LABEL_TO_VALUE = {label: value for label, value in RESIZE_MODE_CHOICES}
RESIZE_MODE_VALUE_TO_LABEL = {value: label for label, value in RESIZE_MODE_CHOICES}
RESIZE_MODE_LABELS = tuple(label for label, _value in RESIZE_MODE_CHOICES)


def resize_mode_label(value: str) -> str:
    return RESIZE_MODE_VALUE_TO_LABEL.get(str(value).strip().lower(), RESIZE_MODE_LABELS[0])


def resize_mode_value(label: str) -> str:
    raw = str(label).strip()
    if raw in RESIZE_MODE_LABEL_TO_VALUE:
        return RESIZE_MODE_LABEL_TO_VALUE[raw]

    lowered = raw.lower()
    if lowered in RESIZE_MODE_VALUE_TO_LABEL:
        return lowered
    return "contain"


class LabeledScale(ttk.Frame):
    def __init__(self, master, label: str, variable: tk.DoubleVar, *, from_: float, to: float, precision: int, command=None):
        super().__init__(master)
        self._command = command
        self.variable = variable
        self.precision = precision

        title = ttk.Label(self, text=label)
        title.grid(row=0, column=0, sticky="w")

        self.value_label = ttk.Label(self, text=self._format(variable.get()), width=8, anchor="e")
        self.value_label.grid(row=0, column=1, sticky="e")

        scale = ttk.Scale(self, from_=from_, to=to, orient="horizontal", variable=variable, command=self._on_change)
        scale.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        self.columnconfigure(0, weight=1)

    def _format(self, value: float) -> str:
        return f"{value:.{self.precision}f}"

    def _on_change(self, raw_value: str):
        try:
            value = float(raw_value)
        except ValueError:
            value = self.variable.get()
        self.value_label.configure(text=self._format(value))
        if self._command is not None:
            self._command()

    def set_state(self, state: str):
        for child in self.winfo_children():
            try:
                child.configure(state=state)
            except tk.TclError:
                continue


class RLEAEncoderApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(WINDOW_TITLE)
        self.root.geometry("1220x900")
        self.root.minsize(1100, 900)

        self.prepared: PreparedAnimation | None = None
        self.encoded: EncodedAnimation | None = None
        self.preview_image_cache: dict[int, Image.Image] = {}
        self.tk_preview_image: ImageTk.PhotoImage | None = None
        self.play_job: str | None = None
        self.refresh_job: str | None = None
        self._busy = False
        self._busy_widgets: list[object] = []
        self._job_token = 0
        self._active_job_label: str | None = None
        self._worker_results: queue.Queue = queue.Queue()
        self._syncing_tree_selection = False

        self.source_path_var = tk.StringVar()
        self.output_path_var = tk.StringVar()
        self.geometry_var = tk.StringVar(value="64x64")
        self.fps_var = tk.IntVar(value=12)
        self.keyframe_var = tk.IntVar(value=12)
        self.enable_deltas_var = tk.BooleanVar(value=True)
        self.resize_mode_var = tk.StringVar(value=resize_mode_label("contain"))
        self.resample_var = tk.StringVar(value="bicubic")
        self.background_var = tk.StringVar(value="#000000")
        self.zoom_var = tk.DoubleVar(value=1.00)
        self.pan_x_var = tk.DoubleVar(value=0.00)
        self.pan_y_var = tk.DoubleVar(value=0.00)
        self.auto_refresh_var = tk.BooleanVar(value=False)
        self.verify_roundtrip_var = tk.BooleanVar(value=False)

        self.gamma_var = tk.DoubleVar(value=1.15)
        self.brightness_var = tk.DoubleVar(value=1.10)
        self.exposure_var = tk.DoubleVar(value=0.15)
        self.contrast_var = tk.DoubleVar(value=1.08)
        self.saturation_var = tk.DoubleVar(value=1.00)

        self.frame_index_var = tk.IntVar(value=0)
        self.progress_var = tk.DoubleVar(value=0.0)
        self.status_var = tk.StringVar(value="Choose a media file or frame directory to begin.")
        self.source_summary_var = tk.StringVar(value="-")
        self.output_summary_var = tk.StringVar(value="-")
        self.keyframe_summary_var = tk.StringVar(value="-")
        self.delta_summary_var = tk.StringVar(value="-")
        self.file_size_summary_var = tk.StringVar(value="-")
        self.bandwidth_summary_var = tk.StringVar(value="-")
        self.frame_summary_var = tk.StringVar(value="Frame 0 / 0")

        self._build_theme()
        self._build_layout()
        self._schedule_worker_poll()

    def _build_theme(self):
        self.root.configure(bg="#edf2f6")
        style = ttk.Style()
        if "clam" in style.theme_names():
            style.theme_use("clam")

        style.configure(".", background="#edf2f6", foreground="#14202b")
        style.configure("TFrame", background="#edf2f6")
        style.configure("Card.TFrame", background="#ffffff", relief="flat")
        style.configure("Header.TLabel", background="#edf2f6", foreground="#14202b", font=("Segoe UI Semibold", 18))
        style.configure("Subhead.TLabel", background="#ffffff", foreground="#33485d", font=("Segoe UI Semibold", 10))
        style.configure("Value.TLabel", background="#ffffff", foreground="#14202b", font=("Segoe UI", 11))
        style.configure("Muted.TLabel", background="#edf2f6", foreground="#567086", font=("Segoe UI", 10))
        style.configure("Primary.TButton", padding=(12, 8), font=("Segoe UI Semibold", 10))
        style.configure("TButton", padding=(10, 7), font=("Segoe UI", 10))
        style.configure("TLabelframe", background="#ffffff", foreground="#14202b")
        style.configure("TLabelframe.Label", background="#ffffff", foreground="#14202b", font=("Segoe UI Semibold", 10))
        style.configure("Treeview", rowheight=24, font=("Consolas", 10))
        style.configure("Treeview.Heading", font=("Segoe UI Semibold", 10))
        style.configure("TCheckbutton", background="#ffffff", foreground="#14202b")
        style.configure("TEntry", fieldbackground="#ffffff")
        style.configure("TCombobox", fieldbackground="#ffffff")
        style.configure("TSpinbox", fieldbackground="#ffffff")

    def _build_layout(self):
        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=0)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(1, weight=1)

        header = ttk.Frame(outer)
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        header.columnconfigure(0, weight=1)
        header.columnconfigure(1, weight=0)
        ttk.Label(header, text=WINDOW_TITLE, style="Header.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            header,
            text="Compact RGB565 animation export for custom frame sizes with RLE keyframes and delta frames.",
            style="Muted.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))
        support = tk.Label(
            header,
            text=BUY_ME_A_COFFEE_LABEL,
            fg="#2563eb",
            bg="#edf2f6",
            cursor="hand2",
            font=("Segoe UI", 13, "underline"),
        )
        support.grid(row=0, column=1, rowspan=2, sticky="e")
        support.bind("<Button-1>", lambda _event: self.open_support_link())

        left = ttk.Frame(outer, style="Card.TFrame", padding=10)
        left.grid(row=1, column=0, sticky="nsw", padx=(0, 12))
        left.columnconfigure(0, weight=1)

        right = ttk.Frame(outer, style="Card.TFrame", padding=10)
        right.grid(row=1, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)
        right.rowconfigure(2, weight=1)

        self._build_left_panel(left)
        self._build_right_panel(right)

        status_shell = ttk.Frame(self.root, padding=(10, 6))
        status_shell.pack(fill="x")
        status_shell.columnconfigure(0, weight=1)

        self.progress_bar = ttk.Progressbar(
            status_shell,
            variable=self.progress_var,
            maximum=100.0,
            mode="determinate",
        )
        self.progress_bar.grid(row=0, column=0, sticky="ew", pady=(0, 6))

        status = ttk.Label(status_shell, textvariable=self.status_var, anchor="w", style="Muted.TLabel")
        status.grid(row=1, column=0, sticky="ew")

    def open_support_link(self):
        try:
            webbrowser.open(BUY_ME_A_COFFEE_URL)
            self.status_var.set("Opened support link in your browser")
        except Exception as exc:
            messagebox.showerror("RLEA Animation Encoder", f"Failed to open link:\n{exc}")

    def _build_left_panel(self, parent: ttk.Frame):
        source_frame = ttk.LabelFrame(parent, text="Source", padding=8)
        source_frame.grid(row=0, column=0, sticky="ew")
        source_frame.columnconfigure(0, weight=1)

        ttk.Label(source_frame, text="Input", style="Subhead.TLabel").grid(row=0, column=0, sticky="w")
        input_row = ttk.Frame(source_frame)
        input_row.grid(row=1, column=0, sticky="ew", pady=(4, 6))
        input_row.columnconfigure(0, weight=1)
        ttk.Entry(input_row, textvariable=self.source_path_var).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.source_file_button = ttk.Button(input_row, text="File", command=self.choose_source_file)
        self.source_file_button.grid(row=0, column=1, padx=(0, 4))
        self._busy_widgets.append(self.source_file_button)
        self.source_folder_button = ttk.Button(input_row, text="Folder", command=self.choose_source_folder)
        self.source_folder_button.grid(row=0, column=2)
        self._busy_widgets.append(self.source_folder_button)

        ttk.Label(source_frame, text="Output", style="Subhead.TLabel").grid(row=2, column=0, sticky="w")
        output_row = ttk.Frame(source_frame)
        output_row.grid(row=3, column=0, sticky="ew")
        output_row.columnconfigure(0, weight=1)
        ttk.Entry(output_row, textvariable=self.output_path_var).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.output_browse_button = ttk.Button(output_row, text="Browse", command=self.choose_output_file)
        self.output_browse_button.grid(row=0, column=1)
        self._busy_widgets.append(self.output_browse_button)

        target_frame = ttk.LabelFrame(parent, text="Target", padding=8)
        target_frame.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        for column in range(2):
            target_frame.columnconfigure(column, weight=1)

        ttk.Label(target_frame, text="Frame size", style="Subhead.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(target_frame, text="FPS", style="Subhead.TLabel").grid(row=0, column=1, sticky="w")
        self.geometry_combo = ttk.Combobox(target_frame, textvariable=self.geometry_var, values=("16x16", "32x32", "64x64", "128x64"))
        self.geometry_combo.grid(row=1, column=0, sticky="ew", padx=(0, 6), pady=(4, 6))
        self._busy_widgets.append(self.geometry_combo)
        self.fps_spinbox = ttk.Spinbox(target_frame, from_=1, to=60, textvariable=self.fps_var, width=8)
        self.fps_spinbox.grid(row=1, column=1, sticky="ew", pady=(4, 6))
        self._busy_widgets.append(self.fps_spinbox)

        ttk.Label(target_frame, text="Keyframe interval", style="Subhead.TLabel").grid(row=2, column=0, sticky="w")
        ttk.Label(target_frame, text="Frame fit", style="Subhead.TLabel").grid(row=2, column=1, sticky="w")
        self.keyframe_spinbox = ttk.Spinbox(target_frame, from_=1, to=240, textvariable=self.keyframe_var, width=8)
        self.keyframe_spinbox.grid(row=3, column=0, sticky="ew", padx=(0, 6), pady=(4, 6))
        self._busy_widgets.append(self.keyframe_spinbox)
        self.resize_combo = ttk.Combobox(target_frame, textvariable=self.resize_mode_var, values=RESIZE_MODE_LABELS, state="readonly")
        self.resize_combo.grid(row=3, column=1, sticky="ew", pady=(4, 6))
        self._busy_widgets.append(self.resize_combo)

        ttk.Label(target_frame, text="Resample", style="Subhead.TLabel").grid(row=4, column=0, sticky="w")
        ttk.Label(target_frame, text="Background", style="Subhead.TLabel").grid(row=4, column=1, sticky="w")
        self.resample_combo = ttk.Combobox(target_frame, textvariable=self.resample_var, values=RESAMPLE_NAMES, state="readonly")
        self.resample_combo.grid(row=5, column=0, sticky="ew", padx=(0, 6), pady=(4, 6))
        self._busy_widgets.append(self.resample_combo)
        self.background_entry = ttk.Entry(target_frame, textvariable=self.background_var)
        self.background_entry.grid(row=5, column=1, sticky="ew", pady=(4, 6))
        self._busy_widgets.append(self.background_entry)

        self.enable_deltas_check = ttk.Checkbutton(target_frame, text="Allow delta frames", variable=self.enable_deltas_var, command=self._schedule_refresh)
        self.enable_deltas_check.grid(row=6, column=0, sticky="w")
        self._busy_widgets.append(self.enable_deltas_check)
        self.auto_refresh_check = ttk.Checkbutton(target_frame, text="Auto refresh preview", variable=self.auto_refresh_var)
        self.auto_refresh_check.grid(row=6, column=1, sticky="w")
        self._busy_widgets.append(self.auto_refresh_check)
        self.verify_roundtrip_check = ttk.Checkbutton(target_frame, text="Verify round-trip before export", variable=self.verify_roundtrip_var)
        self.verify_roundtrip_check.grid(row=7, column=0, columnspan=2, sticky="w", pady=(2, 0))
        self._busy_widgets.append(self.verify_roundtrip_check)

        framing_frame = ttk.LabelFrame(parent, text="Stage framing", padding=8)
        framing_frame.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        framing_frame.columnconfigure(0, weight=1)

        self.zoom_scale = LabeledScale(framing_frame, "Zoom", self.zoom_var, from_=0.25, to=4.00, precision=2, command=self._schedule_refresh)
        self.zoom_scale.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        self._busy_widgets.append(self.zoom_scale)
        self.pan_x_scale = LabeledScale(framing_frame, "Pan X", self.pan_x_var, from_=-1.00, to=1.00, precision=2, command=self._schedule_refresh)
        self.pan_x_scale.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        self._busy_widgets.append(self.pan_x_scale)
        self.pan_y_scale = LabeledScale(framing_frame, "Pan Y", self.pan_y_var, from_=-1.00, to=1.00, precision=2, command=self._schedule_refresh)
        self.pan_y_scale.grid(row=2, column=0, sticky="ew")
        self._busy_widgets.append(self.pan_y_scale)

        tuning_frame = ttk.LabelFrame(parent, text="Image tuning", padding=8)
        tuning_frame.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        tuning_frame.columnconfigure(0, weight=1)

        self.gamma_scale = LabeledScale(tuning_frame, "Gamma", self.gamma_var, from_=0.40, to=2.40, precision=2, command=self._schedule_refresh)
        self.gamma_scale.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        self._busy_widgets.append(self.gamma_scale)
        self.brightness_scale = LabeledScale(tuning_frame, "Brightness", self.brightness_var, from_=0.40, to=1.80, precision=2, command=self._schedule_refresh)
        self.brightness_scale.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        self._busy_widgets.append(self.brightness_scale)
        self.exposure_scale = LabeledScale(tuning_frame, "Exposure", self.exposure_var, from_=-2.00, to=2.00, precision=2, command=self._schedule_refresh)
        self.exposure_scale.grid(row=2, column=0, sticky="ew", pady=(0, 6))
        self._busy_widgets.append(self.exposure_scale)
        self.contrast_scale = LabeledScale(tuning_frame, "Contrast", self.contrast_var, from_=0.50, to=1.80, precision=2, command=self._schedule_refresh)
        self.contrast_scale.grid(row=3, column=0, sticky="ew", pady=(0, 6))
        self._busy_widgets.append(self.contrast_scale)
        self.saturation_scale = LabeledScale(tuning_frame, "Saturation", self.saturation_var, from_=0.00, to=2.00, precision=2, command=self._schedule_refresh)
        self.saturation_scale.grid(row=4, column=0, sticky="ew")
        self._busy_widgets.append(self.saturation_scale)

        actions = ttk.Frame(parent, style="Card.TFrame")
        actions.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        actions.columnconfigure(0, weight=1)
        actions.columnconfigure(1, weight=1)
        self.refresh_button = ttk.Button(actions, text="Refresh Preview", style="Primary.TButton", command=self.refresh_preview)
        self.refresh_button.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self._busy_widgets.append(self.refresh_button)
        self.export_button = ttk.Button(actions, text="Export .rlea", style="Primary.TButton", command=self.export_rlea)
        self.export_button.grid(row=0, column=1, sticky="ew")
        self._busy_widgets.append(self.export_button)

    def _build_right_panel(self, parent: ttk.Frame):
        preview_card = ttk.LabelFrame(parent, text="Preview", padding=8)
        preview_card.grid(row=0, column=0, sticky="ew")
        preview_card.columnconfigure(0, weight=1)

        controls = ttk.Frame(preview_card)
        controls.grid(row=0, column=0, sticky="ew")
        controls.columnconfigure(1, weight=1)
        self.play_button = ttk.Button(controls, text="Play", command=self.toggle_playback)
        self.play_button.grid(row=0, column=0, padx=(0, 8))
        self._busy_widgets.append(self.play_button)
        slider = ttk.Scale(controls, from_=0, to=0, orient="horizontal", variable=self.frame_index_var, command=self._on_frame_scrub)
        slider.grid(row=0, column=1, sticky="ew")
        self.frame_slider = slider
        ttk.Label(controls, textvariable=self.frame_summary_var, width=18, anchor="e").grid(row=0, column=2, padx=(8, 0))

        canvas_shell = ttk.Frame(preview_card)
        canvas_shell.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        canvas_shell.columnconfigure(0, weight=1)
        canvas_shell.rowconfigure(0, weight=1)
        preview_card.rowconfigure(1, weight=1)

        canvas = tk.Canvas(canvas_shell, background="#101a23", highlightthickness=0, height=280)
        canvas.grid(row=0, column=0, sticky="nsew")
        canvas.bind("<Configure>", lambda _event: self._render_current_frame())
        self.preview_canvas = canvas

        summary_card = ttk.LabelFrame(parent, text="Compression summary", padding=8)
        summary_card.grid(row=1, column=0, sticky="nsew", pady=(8, 8))
        summary_card.columnconfigure(1, weight=1)
        summary_card.columnconfigure(3, weight=1)

        summary_rows = [
            ("Source", self.source_summary_var),
            ("Output", self.output_summary_var),
            ("Keyframes", self.keyframe_summary_var),
            ("Delta frames", self.delta_summary_var),
            ("File size", self.file_size_summary_var),
            ("Bandwidth", self.bandwidth_summary_var),
        ]
        for index, (label, variable) in enumerate(summary_rows):
            row = index // 2
            col = (index % 2) * 2
            ttk.Label(summary_card, text=label, style="Subhead.TLabel").grid(row=row, column=col, sticky="w", padx=(0, 10), pady=(0, 6))
            ttk.Label(summary_card, textvariable=variable, style="Value.TLabel").grid(row=row, column=col + 1, sticky="w", pady=(0, 6))

        frames_card = ttk.LabelFrame(parent, text="Per-frame stats", padding=10)
        frames_card.grid(row=2, column=0, sticky="nsew")
        frames_card.columnconfigure(0, weight=1)
        frames_card.rowconfigure(0, weight=1)

        columns = ("frame", "kind", "payload", "total", "ratio")
        tree = ttk.Treeview(frames_card, columns=columns, show="headings", height=9)
        for column, heading, width in (
            ("frame", "Frame", 70),
            ("kind", "Type", 90),
            ("payload", "Payload", 100),
            ("total", "Total", 100),
            ("ratio", "Ratio", 90),
        ):
            tree.heading(column, text=heading)
            tree.column(column, width=width, anchor="center")
        tree.grid(row=0, column=0, sticky="nsew")
        tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.stats_tree = tree

        scrollbar = ttk.Scrollbar(frames_card, orient="vertical", command=tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        tree.configure(yscrollcommand=scrollbar.set)

    def choose_source_file(self):
        path = filedialog.askopenfilename(title="Choose animation source", filetypes=SOURCE_FILE_TYPES)
        if not path:
            return
        self.source_path_var.set(path)
        if not self.output_path_var.get().strip():
            self.output_path_var.set(str(Path(path).with_suffix(".rlea")))
        self._schedule_refresh(force=True)

    def choose_source_folder(self):
        path = filedialog.askdirectory(title="Choose frame directory")
        if not path:
            return
        self.source_path_var.set(path)
        if not self.output_path_var.get().strip():
            folder = Path(path)
            self.output_path_var.set(str(folder.parent / f"{folder.name}.rlea"))
        self._schedule_refresh(force=True)

    def choose_output_file(self):
        start_path = self.output_path_var.get().strip()
        initialdir = str(Path(start_path).parent) if start_path else None
        initialfile = Path(start_path).name if start_path else None
        path = filedialog.asksaveasfilename(
            title="Save RLEA animation",
            defaultextension=".rlea",
            filetypes=[("RLEA animation", "*.rlea"), ("All files", "*.*")],
            initialdir=initialdir,
            initialfile=initialfile,
        )
        if path:
            self.output_path_var.set(path)

    def _parse_geometry(self) -> tuple[int, int]:
        raw = self.geometry_var.get().strip().lower()
        if "x" not in raw:
            raise ValueError("Frame size must be WIDTHxHEIGHT")
        width_text, _, height_text = raw.partition("x")
        if not width_text or not height_text:
            raise ValueError("Frame size must be WIDTHxHEIGHT")
        return int(width_text.strip()), int(height_text.strip())

    def _parse_background(self) -> tuple[int, int, int]:
        raw = self.background_var.get().strip()
        if not raw:
            return 0, 0, 0
        if raw.startswith("#"):
            text = raw[1:]
            if len(text) != 6:
                raise ValueError("Background hex must be #RRGGBB")
            return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)
        parts = [part.strip() for part in raw.split(",")]
        if len(parts) != 3:
            raise ValueError("Background must be #RRGGBB or r,g,b")
        values = tuple(int(part) for part in parts)
        if any(value < 0 or value > 255 for value in values):
            raise ValueError("Background RGB values must be between 0 and 255")
        return values

    def _build_options(self) -> EncoderOptions:
        width, height = self._parse_geometry()
        return EncoderOptions(
            width=width,
            height=height,
            fps=max(1, int(self.fps_var.get())),
            keyframe_interval=max(1, int(self.keyframe_var.get())),
            enable_deltas=bool(self.enable_deltas_var.get()),
            resize_mode=resize_mode_value(self.resize_mode_var.get()),
            resample_filter=self.resample_var.get(),
            background_rgb=self._parse_background(),
            zoom=float(self.zoom_var.get()),
            pan_x=float(self.pan_x_var.get()),
            pan_y=float(self.pan_y_var.get()),
            adjustments=ImageAdjustments(
                gamma=float(self.gamma_var.get()),
                brightness=float(self.brightness_var.get()),
                exposure=float(self.exposure_var.get()),
                contrast=float(self.contrast_var.get()),
                saturation=float(self.saturation_var.get()),
            ),
        )

    def _schedule_refresh(self, force: bool = False):
        if not self.auto_refresh_var.get() and not force:
            return
        if self.refresh_job is not None:
            self.root.after_cancel(self.refresh_job)
        self.refresh_job = self.root.after(220, self.refresh_preview)

    def _cancel_playback(self):
        if self.play_job is not None:
            self.root.after_cancel(self.play_job)
            self.play_job = None

    def _set_busy_state(self, busy: bool, message: str | None = None):
        self._busy = busy
        cursor = "watch" if busy else ""
        try:
            self.root.configure(cursor=cursor)
        except tk.TclError:
            pass
        try:
            self.preview_canvas.configure(cursor=cursor)
        except tk.TclError:
            pass

        state = "disabled" if busy else "normal"
        for widget in self._busy_widgets:
            if hasattr(widget, "set_state"):
                widget.set_state(state)
                continue
            try:
                widget.configure(state=state)
            except tk.TclError:
                continue

        if not busy:
            self._reset_progress_bar()
        else:
            self._apply_progress_update({"mode": "determinate", "value": 0.0})

        if message is not None:
            self.status_var.set(message)

    def _reset_progress_bar(self):
        if str(self.progress_bar.cget("mode")) == "indeterminate":
            self.progress_bar.stop()
        self.progress_bar.configure(mode="determinate")
        self.progress_var.set(0.0)

    def _apply_progress_update(self, payload):
        mode = payload.get("mode")
        current_mode = str(self.progress_bar.cget("mode"))
        if mode is not None and mode != current_mode:
            if current_mode == "indeterminate":
                self.progress_bar.stop()
            self.progress_bar.configure(mode=mode)
            if mode == "indeterminate":
                self.progress_bar.start(10)

        value = payload.get("value")
        if value is not None:
            if str(self.progress_bar.cget("mode")) != "determinate":
                self.progress_bar.stop()
                self.progress_bar.configure(mode="determinate")
            self.progress_var.set(max(0.0, min(100.0, float(value))))

        message = payload.get("message")
        if message is not None:
            self.status_var.set(str(message))

    def _schedule_worker_poll(self):
        try:
            self.root.after(25, self._poll_worker_results)
        except (RuntimeError, tk.TclError):
            return

    def _poll_worker_results(self):
        while True:
            try:
                event = self._worker_results.get_nowait()
            except queue.Empty:
                break

            kind = event[0]
            if kind == "progress":
                _tag, job_token, payload = event
                self._handle_progress_update(job_token, payload)
                continue

            _tag, job_token, error_title, on_success, result, error = event
            self._finish_background_job(job_token, error_title, on_success, result, error)
        self._schedule_worker_poll()

    def _handle_progress_update(self, job_token: int, payload):
        if job_token != self._job_token:
            return
        self._apply_progress_update(payload)

    def _start_background_job(self, job_label: str, status_message: str, error_title: str, worker, on_success):
        if self._busy:
            self.status_var.set(f"{self._active_job_label or 'A task'} is still running. Please wait.")
            return False

        self._job_token += 1
        job_token = self._job_token
        self._active_job_label = job_label
        self._cancel_playback()
        self._set_busy_state(True, status_message)

        def report_progress(payload):
            self._worker_results.put(("progress", job_token, payload))

        def runner():
            try:
                result = worker(report_progress)
            except Exception as exc:
                self._worker_results.put(("result", job_token, error_title, on_success, None, exc))
            else:
                self._worker_results.put(("result", job_token, error_title, on_success, result, None))

        threading.Thread(target=runner, daemon=True, name=f"rlea-{job_label}").start()
        return True

    def _finish_background_job(self, job_token: int, error_title: str, on_success, result, error):
        if job_token != self._job_token:
            return

        self._active_job_label = None
        self._set_busy_state(False)
        if error is not None:
            self.status_var.set(f"{error_title}: {error}")
            messagebox.showerror(error_title, str(error))
            return
        on_success(result)

    def _process_source(self, source_path: str, options: EncoderOptions, verify_roundtrip: bool, output_path: str | None = None, progress_callback=None):
        if progress_callback is not None:
            progress_callback({"mode": "determinate", "value": 0.0, "message": "Loading source..."})

        prepared = prepare_animation_source(source_path, options, progress_callback=progress_callback)
        encoded = encode_animation(prepared, options, progress_callback=progress_callback)
        if verify_roundtrip:
            if progress_callback is not None:
                progress_callback({"mode": "indeterminate", "message": "Verifying round-trip..."})
            validate_round_trip(encoded, prepared.rgb565_frames)

        if output_path is not None:
            if progress_callback is not None:
                progress_callback({"mode": "indeterminate", "message": "Writing .rlea file..."})
            written_path = write_rlea(output_path, encoded)
        else:
            written_path = None

        if progress_callback is not None:
            progress_callback({"mode": "determinate", "value": 100.0})
        return prepared, encoded, written_path

    def _frame_count(self) -> int:
        if self.prepared is None:
            return 0
        return len(self.prepared.rgb565_frames)

    def _get_preview_image(self, frame_index: int):
        image = self.preview_image_cache.get(frame_index)
        if image is not None:
            return image
        if self.prepared is None:
            raise ValueError("No prepared animation loaded")
        image = rgb565_words_to_image(
            self.prepared.rgb565_frames[frame_index],
            self.prepared.width,
            self.prepared.height,
        )
        self.preview_image_cache[frame_index] = image
        return image

    def _apply_loaded_animation(self, prepared: PreparedAnimation, encoded: EncodedAnimation):
        self.prepared = prepared
        self.encoded = encoded
        self.preview_image_cache.clear()
        self._update_summary()
        self._populate_frame_stats()
        self.frame_index_var.set(0)
        self.frame_slider.configure(to=max(0, self._frame_count() - 1))
        self._render_current_frame()

    def _on_preview_ready(self, result):
        prepared, encoded, _written_path = result
        self._apply_loaded_animation(prepared, encoded)
        self.status_var.set(
            f"Prepared {self._frame_count()} frames at {prepared.width}x{prepared.height} and {prepared.fps} FPS."
        )

    def _on_export_ready(self, result):
        prepared, encoded, written_path = result
        self._apply_loaded_animation(prepared, encoded)
        self.status_var.set(f"Wrote {written_path} ({encoded.file_bytes:,} bytes).")
        messagebox.showinfo(
            "Export complete",
            f"Saved {self._frame_count()} frames to\n{written_path}\n\n"
            f"Estimated bandwidth: {encoded.estimated_bandwidth_bytes_per_second:,.0f} bytes/s",
        )

    def refresh_preview(self):
        if self.refresh_job is not None:
            self.root.after_cancel(self.refresh_job)
            self.refresh_job = None

        source_path = self.source_path_var.get().strip()
        if not source_path:
            self.status_var.set("Choose a source file or folder first.")
            return

        try:
            options = self._build_options()
        except Exception as exc:
            self.status_var.set(f"Preview failed: {exc}")
            messagebox.showerror("Preview failed", str(exc))
            return

        self._start_background_job(
            job_label="preview load",
            status_message="Loading source and rebuilding preview in the background...",
            error_title="Preview failed",
            worker=lambda report_progress: self._process_source(
                source_path,
                options,
                False,
                progress_callback=report_progress,
            ),
            on_success=self._on_preview_ready,
        )

    def _update_summary(self):
        assert self.prepared is not None
        assert self.encoded is not None
        keyframes = sum(1 for stat in self.encoded.frame_stats if stat.frame_kind == "keyframe")
        delta_frames = len(self.encoded.frame_stats) - keyframes
        average_total = 0.0
        if self.encoded.frame_stats:
            average_total = sum(stat.total_bytes for stat in self.encoded.frame_stats) / len(self.encoded.frame_stats)

        self.source_summary_var.set(
            f"{self.prepared.source_kind}, {self.prepared.source_frame_count} source frames"
        )
        self.output_summary_var.set(
            f"{len(self.prepared.rgb565_frames)} output frames @ {self.prepared.fps} FPS"
        )
        self.keyframe_summary_var.set(f"{keyframes}")
        self.delta_summary_var.set(f"{delta_frames}")
        self.file_size_summary_var.set(
            f"{self.encoded.file_bytes:,} bytes ({average_total:.1f} avg/frame)"
        )
        self.bandwidth_summary_var.set(
            f"{self.encoded.estimated_bandwidth_bytes_per_second:,.0f} bytes/s"
        )

    def _populate_frame_stats(self):
        assert self.encoded is not None
        tree = self.stats_tree
        tree.delete(*tree.get_children())
        for stat in self.encoded.frame_stats:
            tree.insert(
                "",
                "end",
                iid=str(stat.index),
                values=(
                    stat.index,
                    stat.frame_kind,
                    f"{stat.payload_bytes:,}",
                    f"{stat.total_bytes:,}",
                    f"{stat.ratio:.3f}",
                ),
            )

    def _on_tree_select(self, _event):
        if self._syncing_tree_selection:
            return
        selection = self.stats_tree.selection()
        if not selection:
            return
        self.frame_index_var.set(int(selection[0]))
        self._render_current_frame()

    def _on_frame_scrub(self, raw_value: str):
        try:
            index = int(round(float(raw_value)))
        except ValueError:
            index = self.frame_index_var.get()
        self.frame_index_var.set(index)
        self._render_current_frame()

    def _render_current_frame(self):
        frame_count = self._frame_count()
        if frame_count == 0:
            self.preview_canvas.delete("all")
            self.preview_canvas.create_text(
                self.preview_canvas.winfo_width() // 2,
                self.preview_canvas.winfo_height() // 2,
                text="No preview loaded",
                fill="#7f93a5",
                font=("Segoe UI", 16),
            )
            self.frame_summary_var.set("Frame 0 / 0")
            return

        frame_index = max(0, min(int(self.frame_index_var.get()), frame_count - 1))
        self.frame_index_var.set(frame_index)
        image = self._get_preview_image(frame_index)
        canvas_width = max(1, self.preview_canvas.winfo_width())
        canvas_height = max(1, self.preview_canvas.winfo_height())
        scale = max(1, min(canvas_width // image.width, canvas_height // image.height))
        scaled = image.resize((image.width * scale, image.height * scale), resample=PREVIEW_RESAMPLE)
        self.tk_preview_image = ImageTk.PhotoImage(scaled)

        self.preview_canvas.delete("all")
        x = canvas_width // 2
        y = canvas_height // 2
        self.preview_canvas.create_rectangle(0, 0, canvas_width, canvas_height, fill="#101a23", outline="")
        self.preview_canvas.create_image(x, y, image=self.tk_preview_image)
        self.preview_canvas.create_text(
            14,
            14,
            anchor="nw",
            text=f"{image.width}x{image.height}  scale x{scale}",
            fill="#dce7ef",
            font=("Segoe UI", 10),
        )
        self.frame_summary_var.set(f"Frame {frame_index + 1} / {frame_count}")
        if self.stats_tree.exists(str(frame_index)):
            current_selection = self.stats_tree.selection()
            if current_selection != (str(frame_index),):
                self._syncing_tree_selection = True
                try:
                    self.stats_tree.selection_set(str(frame_index))
                    self.stats_tree.see(str(frame_index))
                finally:
                    self._syncing_tree_selection = False

    def toggle_playback(self):
        if self.play_job is not None:
            self._cancel_playback()
            self.status_var.set("Preview playback paused.")
            return
        if self._frame_count() == 0 or self.prepared is None:
            self.status_var.set("Generate a preview before playback.")
            return
        self.status_var.set("Preview playback running.")
        self._play_next_frame()

    def _play_next_frame(self):
        frame_count = self._frame_count()
        if frame_count == 0 or self.prepared is None:
            self.play_job = None
            return
        next_index = (int(self.frame_index_var.get()) + 1) % frame_count
        self.frame_index_var.set(next_index)
        self._render_current_frame()
        interval_ms = max(1, int(round(1000.0 / max(1, self.prepared.fps))))
        self.play_job = self.root.after(interval_ms, self._play_next_frame)

    def export_rlea(self):
        source_path = self.source_path_var.get().strip()
        if not source_path:
            messagebox.showerror("Missing source", "Choose a source file or folder before exporting.")
            return

        output_path = self.output_path_var.get().strip()
        if not output_path:
            messagebox.showerror("Missing output path", "Choose an output .rlea file path before exporting.")
            return

        try:
            options = self._build_options()
            verify_roundtrip = bool(self.verify_roundtrip_var.get())
        except Exception as exc:
            self.status_var.set(f"Export failed: {exc}")
            messagebox.showerror("Export failed", str(exc))
            return

        self._start_background_job(
            job_label="export",
            status_message="Encoding and writing RLEA animation in the background...",
            error_title="Export failed",
            worker=lambda report_progress: self._process_source(
                source_path,
                options,
                verify_roundtrip,
                output_path,
                progress_callback=report_progress,
            ),
            on_success=self._on_export_ready,
        )


def main():
    root = tk.Tk()
    app = RLEAEncoderApp(root)
    root.mainloop()
    return app


if __name__ == "__main__":
    main()
