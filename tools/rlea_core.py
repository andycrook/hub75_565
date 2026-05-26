from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import io
import os
import shutil
import struct
import subprocess
import tempfile
from typing import Iterable, Sequence

try:
    from PIL import Image, ImageEnhance, ImageOps, ImageSequence
except ImportError:  # pragma: no cover - optional at import time, required at runtime
    Image = None
    ImageEnhance = None
    ImageOps = None
    ImageSequence = None


RLEA_MAGIC = b"RLEA"
RLEA_VERSION = 1

HEADER_STRUCT = struct.Struct("<4sBHHBHBI")
FRAME_HEADER_STRUCT = struct.Struct("<BI")
SPAN_HEADER_STRUCT = struct.Struct("<HH")
FRAME_TABLE_ENTRY_STRUCT = struct.Struct("<I")

FORMAT_FLAG_KEYFRAME_ONLY = 1 << 0
FORMAT_FLAG_HAS_DELTAS = 1 << 1
FORMAT_FLAG_COMPRESSION_SHIFT = 4
FORMAT_FLAG_COMPRESSION_MASK = 0xF0
FORMAT_COMPRESSION_MIXED_RLE = 0

FRAME_FLAG_KEYFRAME = 1 << 0
FRAME_FLAG_DELTA = 1 << 1

MAX_GEOMETRY_VALUE = 0xFFFF
MAX_FRAME_PIXELS = 0xFFFF

RESAMPLE_NAMES = (
    "nearest",
    "box",
    "bilinear",
    "hamming",
    "bicubic",
    "lanczos",
)

SOURCE_EXTENSIONS = {
    ".bmp",
    ".gif",
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
}

VIDEO_EXTENSIONS = {
    ".avi",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp4",
    ".webm",
}


def _require_pillow():
    if Image is None or ImageEnhance is None or ImageOps is None or ImageSequence is None:
        raise RuntimeError("Pillow is required for the RLEA desktop encoder tool")


def _require_ffmpeg():
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("ffmpeg is required for video inputs and must be on PATH")
    return ffmpeg


def _emit_progress(progress_callback, *, mode=None, value=None, message=None):
    if progress_callback is None:
        return

    payload = {}
    if mode is not None:
        payload["mode"] = str(mode)
    if value is not None:
        payload["value"] = float(value)
    if message is not None:
        payload["message"] = str(message)
    progress_callback(payload)


def _progress_value(start: float, end: float, completed: int, total: int) -> float:
    if total <= 0:
        return float(end)
    clamped = min(max(0, int(completed)), int(total))
    return float(start) + ((float(end) - float(start)) * (clamped / float(total)))


def _ffmpeg_scale_flags(name: str) -> str:
    lookup = {
        "nearest": "neighbor",
        "box": "area",
        "bilinear": "bilinear",
        "hamming": "bicubic",
        "bicubic": "bicubic",
        "lanczos": "lanczos",
    }
    return lookup.get(str(name).lower(), "bicubic")


def _video_filter_graph(target_fps: int, width: int, height: int, resize_mode: str, resample_filter: str, background_rgb: tuple[int, int, int]) -> str:
    fps = max(1, int(target_fps))
    resize_mode_key = str(resize_mode).lower()
    scale_flags = _ffmpeg_scale_flags(resample_filter)
    background_hex = "%02x%02x%02x" % tuple(int(value) & 0xFF for value in background_rgb)

    filters = [f"fps={fps}"]
    if resize_mode_key == "stretch":
        filters.append(f"scale={width}:{height}:flags={scale_flags}")
    elif resize_mode_key == "cover":
        filters.append(
            f"scale={width}:{height}:flags={scale_flags}:force_original_aspect_ratio=increase"
        )
        filters.append(f"crop={width}:{height}")
    else:
        filters.append(
            f"scale={width}:{height}:flags={scale_flags}:force_original_aspect_ratio=decrease"
        )
        filters.append(
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=0x{background_hex}"
        )
    return ",".join(filters)


def validate_geometry(width: int, height: int) -> tuple[int, int]:
    width_val = int(width)
    height_val = int(height)
    if width_val <= 0 or height_val <= 0:
        raise ValueError("width and height must be > 0")
    if width_val > MAX_GEOMETRY_VALUE or height_val > MAX_GEOMETRY_VALUE:
        raise ValueError("width and height must be <= 65535")
    if width_val * height_val > MAX_FRAME_PIXELS:
        raise ValueError("frame area must be <= 65535 pixels")
    return width_val, height_val


def pack_rgb565(red: int, green: int, blue: int) -> int:
    return ((red & 0xF8) << 8) | ((green & 0xFC) << 3) | (blue >> 3)


def unpack_rgb565(pixel: int) -> tuple[int, int, int]:
    red = (pixel >> 8) & 0xF8
    green = (pixel >> 3) & 0xFC
    blue = (pixel << 3) & 0xF8
    red |= red >> 5
    green |= green >> 6
    blue |= blue >> 5
    return red & 0xFF, green & 0xFF, blue & 0xFF


def _resample_filter(name: str):
    _require_pillow()
    if not hasattr(Image, "Resampling"):
        return Image.BICUBIC

    lookup = {
        "nearest": Image.Resampling.NEAREST,
        "box": Image.Resampling.BOX,
        "bilinear": Image.Resampling.BILINEAR,
        "hamming": Image.Resampling.HAMMING,
        "bicubic": Image.Resampling.BICUBIC,
        "lanczos": Image.Resampling.LANCZOS,
    }
    return lookup.get(str(name).lower(), Image.Resampling.BICUBIC)


@dataclass(slots=True)
class ImageAdjustments:
    gamma: float = 1.0
    brightness: float = 1.0
    exposure: float = 0.0
    contrast: float = 1.0
    saturation: float = 1.0


@dataclass(slots=True)
class EncoderOptions:
    width: int = 64
    height: int = 64
    fps: int = 12
    keyframe_interval: int = 12
    enable_deltas: bool = True
    resize_mode: str = "contain"
    resample_filter: str = "bicubic"
    background_rgb: tuple[int, int, int] = (0, 0, 0)
    zoom: float = 1.0
    pan_x: float = 0.0
    pan_y: float = 0.0
    adjustments: ImageAdjustments = field(default_factory=ImageAdjustments)
    delta_gap_tolerance: int = 0


@dataclass(slots=True)
class PreparedAnimation:
    source_path: Path
    source_kind: str
    width: int
    height: int
    fps: int
    preview_frames: list[object]
    rgb565_frames: list[tuple[int, ...]]
    source_frame_count: int


@dataclass(slots=True)
class EncodedFrameStats:
    index: int
    frame_flags: int
    payload_bytes: int
    total_bytes: int
    raw_bytes: int
    ratio: float

    @property
    def frame_kind(self) -> str:
        if self.frame_flags & FRAME_FLAG_DELTA:
            return "delta"
        return "keyframe"


@dataclass(slots=True)
class EncodedAnimation:
    width: int
    height: int
    fps: int
    flags: int
    frame_offsets: list[int]
    frame_records: list[tuple[int, bytes]]
    frame_stats: list[EncodedFrameStats]

    @property
    def frame_count(self) -> int:
        return len(self.frame_records)

    @property
    def raw_frame_bytes(self) -> int:
        return self.width * self.height * 2

    @property
    def payload_bytes(self) -> int:
        return sum(len(payload) for _flags, payload in self.frame_records)

    @property
    def file_bytes(self) -> int:
        return HEADER_STRUCT.size + len(self.frame_offsets) * FRAME_TABLE_ENTRY_STRUCT.size + sum(
            FRAME_HEADER_STRUCT.size + len(payload) for _flags, payload in self.frame_records
        )

    @property
    def estimated_bandwidth_bytes_per_second(self) -> float:
        if not self.frame_records:
            return 0.0
        return (self.file_bytes / max(1, self.frame_count)) * self.fps


@dataclass(slots=True)
class RLEAHeader:
    version: int
    width: int
    height: int
    fps: int
    frame_count: int
    flags: int
    frame_table_offset: int


@dataclass(slots=True)
class RLEAFrameRecord:
    offset: int
    flags: int
    payload: bytes


@dataclass(slots=True)
class RLEAFile:
    header: RLEAHeader
    frame_offsets: list[int]
    frames: list[RLEAFrameRecord]


def apply_adjustments(image, adjustments: ImageAdjustments):
    _require_pillow()
    working = image.convert("RGB")

    gamma_value = float(adjustments.gamma)
    if gamma_value <= 0.0:
        raise ValueError("gamma must be > 0")
    if gamma_value != 1.0:
        lut = [int(round(((value / 255.0) ** gamma_value) * 255.0)) for value in range(256)]
        working = working.point(lut * 3)

    exposure_stops = float(adjustments.exposure)
    if exposure_stops != 0.0:
        factor = 2.0 ** exposure_stops
        lut = [max(0, min(255, int(round(value * factor)))) for value in range(256)]
        working = working.point(lut * 3)

    brightness = float(adjustments.brightness)
    if brightness != 1.0:
        working = ImageEnhance.Brightness(working).enhance(brightness)

    contrast = float(adjustments.contrast)
    if contrast != 1.0:
        working = ImageEnhance.Contrast(working).enhance(contrast)

    saturation = float(adjustments.saturation)
    if saturation != 1.0:
        working = ImageEnhance.Color(working).enhance(saturation)

    return working


def _normalize_zoom(value: float) -> float:
    zoom_value = float(value)
    if zoom_value <= 0.0:
        raise ValueError("zoom must be > 0")
    return zoom_value


def _normalize_pan(value: float) -> float:
    pan_value = float(value)
    if pan_value < -1.0:
        return -1.0
    if pan_value > 1.0:
        return 1.0
    return pan_value


def _placement_offset(container_size: int, content_size: int, pan_value: float) -> int:
    span = int(container_size) - int(content_size)
    return int(round(span * ((float(pan_value) + 1.0) * 0.5)))


def _video_prescale_enabled(options: EncoderOptions) -> bool:
    return (
        abs(_normalize_zoom(options.zoom) - 1.0) < 1e-9
        and abs(_normalize_pan(options.pan_x)) < 1e-9
        and abs(_normalize_pan(options.pan_y)) < 1e-9
    )


def fit_frame(
    image,
    width: int,
    height: int,
    *,
    resize_mode: str,
    resample_filter: str,
    background_rgb: tuple[int, int, int],
    zoom: float = 1.0,
    pan_x: float = 0.0,
    pan_y: float = 0.0,
):
    _require_pillow()
    resample = _resample_filter(resample_filter)
    rgba = image.convert("RGBA")
    resize_mode_key = str(resize_mode).lower()

    zoom_value = _normalize_zoom(zoom)
    pan_x_value = _normalize_pan(pan_x)
    pan_y_value = _normalize_pan(pan_y)
    source_width, source_height = rgba.size

    if resize_mode_key == "stretch":
        scaled_width = max(1, int(round(width * zoom_value)))
        scaled_height = max(1, int(round(height * zoom_value)))
    else:
        if resize_mode_key == "cover":
            base_scale = max(width / float(source_width), height / float(source_height))
        else:
            base_scale = min(width / float(source_width), height / float(source_height))
        scaled_width = max(1, int(round(source_width * base_scale * zoom_value)))
        scaled_height = max(1, int(round(source_height * base_scale * zoom_value)))

    scaled = rgba.resize((scaled_width, scaled_height), resample=resample)
    fitted = Image.new("RGBA", (width, height), background_rgb + (255,))
    x = _placement_offset(width, scaled_width, pan_x_value)
    y = _placement_offset(height, scaled_height, pan_y_value)
    fitted.paste(scaled, (x, y), scaled)
    return fitted.convert("RGB")


def image_to_rgb565_words(image) -> tuple[int, ...]:
    rgb = image.convert("RGB")
    data = rgb.tobytes()
    pixels = []
    for index in range(0, len(data), 3):
        pixels.append(pack_rgb565(data[index], data[index + 1], data[index + 2]))
    return tuple(pixels)


def rgb565_words_to_image(words: Sequence[int], width: int, height: int):
    _require_pillow()
    buf = bytearray(len(words) * 3)
    write_index = 0
    for pixel in words:
        red, green, blue = unpack_rgb565(int(pixel))
        buf[write_index] = red
        buf[write_index + 1] = green
        buf[write_index + 2] = blue
        write_index += 3
    return Image.frombytes("RGB", (int(width), int(height)), bytes(buf))


def _load_directory_frames(path: Path):
    _require_pillow()
    images = []
    for child in sorted(path.iterdir()):
        if not child.is_file() or child.suffix.lower() not in SOURCE_EXTENSIONS:
            continue
        with Image.open(child) as source_image:
            images.append(source_image.convert("RGBA"))
    if not images:
        raise FileNotFoundError("no supported frames found in directory")
    return images, [None] * len(images), "directory"


def _load_image_file(path: Path):
    _require_pillow()
    with Image.open(path) as source_image:
        if getattr(source_image, "is_animated", False) and getattr(source_image, "n_frames", 1) > 1:
            durations = []
            frames = []
            default_duration = max(1, int(round(1000.0 / 12.0)))
            for frame in ImageSequence.Iterator(source_image):
                durations.append(int(frame.info.get("duration", source_image.info.get("duration", default_duration))) or default_duration)
                frames.append(frame.convert("RGBA"))
            return frames, durations, "animated"
        return [source_image.convert("RGBA")], [None], "image"


def _load_video_file(
    path: Path,
    target_fps: int,
    width: int,
    height: int,
    resize_mode: str,
    resample_filter: str,
    background_rgb: tuple[int, int, int],
    pre_scale: bool = True,
    progress_callback=None,
):
    _require_pillow()
    ffmpeg = _require_ffmpeg()
    if pre_scale:
        filter_graph = _video_filter_graph(
            target_fps,
            width,
            height,
            resize_mode,
            resample_filter,
            background_rgb,
        )
        extract_message = "Extracting and pre-scaling video frames with ffmpeg..."
    else:
        filter_graph = f"fps={max(1, int(target_fps))}"
        extract_message = "Extracting video frames with ffmpeg..."

    with tempfile.TemporaryDirectory(prefix="rlea_video_") as temp_dir:
        output_pattern = str(Path(temp_dir) / "frame_%06d.png")
        _emit_progress(
            progress_callback,
            mode="indeterminate",
            message=extract_message,
        )
        result = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(path),
                "-vf",
                filter_graph,
                "-vsync",
                "0",
                output_pattern,
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            message = (result.stderr or "").strip() or "ffmpeg failed to decode video"
            raise RuntimeError(message)

        _emit_progress(
            progress_callback,
            mode="determinate",
            value=8.0,
            message="Loading extracted video frames...",
        )
        frames, _durations_ms, _kind = _load_directory_frames(Path(temp_dir))
        return frames, [None] * len(frames), "video"


def _normalize_animated_frames(frames: Sequence[object], durations_ms: Sequence[int | None], target_fps: int) -> list[object]:
    if len(frames) <= 1:
        return [frame.copy() for frame in frames]

    frame_durations = [max(1, int(value if value is not None else round(1000.0 / max(1, target_fps)))) for value in durations_ms]
    total_duration = sum(frame_durations)
    if total_duration <= 0:
        return [frame.copy() for frame in frames]

    output_interval = 1000.0 / max(1, target_fps)
    output_count = max(1, int(round(total_duration / output_interval)))
    normalized = []
    boundary = frame_durations[0]
    frame_index = 0
    for out_index in range(output_count):
        sample_t = min(total_duration - 0.0001, (out_index + 0.5) * output_interval)
        while frame_index < len(frames) - 1 and sample_t >= boundary:
            frame_index += 1
            boundary += frame_durations[frame_index]
        normalized.append(frames[frame_index].copy())
    return normalized


def prepare_animation_source(source_path: str | os.PathLike[str], options: EncoderOptions, progress_callback=None) -> PreparedAnimation:
    _require_pillow()
    width, height = validate_geometry(options.width, options.height)
    path = Path(source_path)
    if not path.exists():
        raise FileNotFoundError(path)

    pre_scaled = False
    _emit_progress(progress_callback, mode="indeterminate", message="Loading source frames...")
    if path.is_dir():
        frames, durations_ms, kind = _load_directory_frames(path)
        normalized_frames = frames
    elif path.suffix.lower() in VIDEO_EXTENSIONS:
        pre_scaled = _video_prescale_enabled(options)
        frames, durations_ms, kind = _load_video_file(
            path,
            options.fps,
            width,
            height,
            options.resize_mode,
            options.resample_filter,
            options.background_rgb,
            pre_scale=pre_scaled,
            progress_callback=progress_callback,
        )
        normalized_frames = frames
    else:
        frames, durations_ms, kind = _load_image_file(path)
        normalized_frames = _normalize_animated_frames(frames, durations_ms, options.fps)

    preview_frames = []
    rgb565_frames = []
    total_frames = max(1, len(normalized_frames))
    for index, frame in enumerate(normalized_frames):
        if pre_scaled:
            fitted = frame
        else:
            fitted = fit_frame(
                frame,
                width,
                height,
                resize_mode=options.resize_mode,
                resample_filter=options.resample_filter,
                background_rgb=options.background_rgb,
                zoom=options.zoom,
                pan_x=options.pan_x,
                pan_y=options.pan_y,
            )
        adjusted = apply_adjustments(fitted, options.adjustments)
        preview_frames.append(adjusted)
        rgb565_frames.append(image_to_rgb565_words(adjusted))
        _emit_progress(
            progress_callback,
            mode="determinate",
            value=_progress_value(10.0, 55.0, index + 1, total_frames),
            message="Preparing frame %d/%d..." % (index + 1, total_frames),
        )

    return PreparedAnimation(
        source_path=path,
        source_kind=kind,
        width=width,
        height=height,
        fps=max(1, int(options.fps)),
        preview_frames=preview_frames,
        rgb565_frames=rgb565_frames,
        source_frame_count=len(frames),
    )


def encode_mixed_rle(pixels: Sequence[int]) -> bytes:
    if not pixels:
        return b""

    out = bytearray()
    length = len(pixels)
    index = 0
    while index < length:
        repeat_count = 1
        pixel = int(pixels[index]) & 0xFFFF
        while index + repeat_count < length and repeat_count < 128 and int(pixels[index + repeat_count]) == pixel:
            repeat_count += 1
        if repeat_count >= 2:
            out.append(repeat_count - 1)
            out.extend(int(pixel).to_bytes(2, "little"))
            index += repeat_count
            continue

        raw_start = index
        raw_values = [pixel]
        index += 1
        while index < length and len(raw_values) < 128:
            current = int(pixels[index]) & 0xFFFF
            lookahead = 1
            while index + lookahead < length and lookahead < 128 and int(pixels[index + lookahead]) == current:
                lookahead += 1
            if lookahead >= 2:
                break
            raw_values.append(current)
            index += 1

        out.append(0x80 | (len(raw_values) - 1))
        for value in raw_values:
            out.extend(int(value).to_bytes(2, "little"))

        if index == raw_start:
            index += 1
    return bytes(out)


def decode_mixed_rle(payload: bytes, expected_pixels: int) -> list[int]:
    out: list[int] = []
    view = memoryview(payload)
    index = 0
    total_expected = int(expected_pixels)
    while len(out) < total_expected and index < len(view):
        command = view[index]
        index += 1
        count = (command & 0x7F) + 1
        if command & 0x80:
            needed = count * 2
            if index + needed > len(view):
                raise ValueError("raw packet truncated")
            for _ in range(count):
                out.append(int.from_bytes(view[index:index + 2], "little"))
                index += 2
        else:
            if index + 2 > len(view):
                raise ValueError("repeat packet truncated")
            pixel = int.from_bytes(view[index:index + 2], "little")
            index += 2
            out.extend([pixel] * count)

    if len(out) != total_expected:
        raise ValueError(f"packet stream emitted {len(out)} pixels, expected {total_expected}")
    return out


def compute_delta_spans(previous_pixels: Sequence[int], current_pixels: Sequence[int], *, gap_tolerance: int = 0) -> list[tuple[int, tuple[int, ...]]]:
    if len(previous_pixels) != len(current_pixels):
        raise ValueError("frame sizes must match for delta generation")

    spans: list[tuple[int, tuple[int, ...]]] = []
    total_pixels = len(current_pixels)
    index = 0
    tolerance = max(0, int(gap_tolerance))
    while index < total_pixels:
        while index < total_pixels and int(previous_pixels[index]) == int(current_pixels[index]):
            index += 1
        if index >= total_pixels:
            break

        start = index
        end = index + 1
        gap_count = 0
        index = end
        while index < total_pixels:
            if int(previous_pixels[index]) != int(current_pixels[index]):
                end = index + 1
                gap_count = 0
            else:
                gap_count += 1
                if gap_count > tolerance:
                    break
            index += 1
        spans.append((start, tuple(int(value) & 0xFFFF for value in current_pixels[start:end])))
    return spans


def encode_keyframe_payload(pixels: Sequence[int]) -> bytes:
    return encode_mixed_rle(pixels)


def encode_delta_payload(previous_pixels: Sequence[int], current_pixels: Sequence[int], *, gap_tolerance: int = 0) -> bytes:
    spans = compute_delta_spans(previous_pixels, current_pixels, gap_tolerance=gap_tolerance)
    out = bytearray()
    out.extend(len(spans).to_bytes(2, "little"))
    for start_pixel, span_pixels in spans:
        out.extend(SPAN_HEADER_STRUCT.pack(int(start_pixel), len(span_pixels)))
        out.extend(encode_mixed_rle(span_pixels))
    return bytes(out)


def decode_keyframe_payload(payload: bytes, total_pixels: int) -> tuple[int, ...]:
    return tuple(decode_mixed_rle(payload, total_pixels))


def decode_delta_payload(payload: bytes, previous_pixels: Sequence[int], total_pixels: int) -> tuple[int, ...]:
    if len(previous_pixels) != total_pixels:
        raise ValueError("previous frame size mismatch")
    if len(payload) < 2:
        raise ValueError("delta payload truncated")

    output = list(int(value) & 0xFFFF for value in previous_pixels)
    view = memoryview(payload)
    span_count = int.from_bytes(view[0:2], "little")
    index = 2
    for _ in range(span_count):
        if index + SPAN_HEADER_STRUCT.size > len(view):
            raise ValueError("delta span header truncated")
        start_pixel, pixel_count = SPAN_HEADER_STRUCT.unpack(view[index:index + SPAN_HEADER_STRUCT.size])
        index += SPAN_HEADER_STRUCT.size
        if pixel_count == 0:
            continue
        packet_start = index
        emitted = 0
        span_pixels: list[int] = []
        while emitted < pixel_count:
            if index >= len(view):
                raise ValueError("delta packet stream truncated")
            command = view[index]
            index += 1
            count = (command & 0x7F) + 1
            if command & 0x80:
                needed = count * 2
                if index + needed > len(view):
                    raise ValueError("delta raw packet truncated")
                for _literal_index in range(count):
                    span_pixels.append(int.from_bytes(view[index:index + 2], "little"))
                    index += 2
            else:
                if index + 2 > len(view):
                    raise ValueError("delta repeat packet truncated")
                pixel = int.from_bytes(view[index:index + 2], "little")
                index += 2
                span_pixels.extend([pixel] * count)
            emitted = len(span_pixels)
        if emitted != pixel_count:
            raise ValueError("delta pixel count mismatch")
        end_pixel = start_pixel + pixel_count
        if end_pixel > total_pixels:
            raise ValueError("delta span exceeds framebuffer size")
        output[start_pixel:end_pixel] = span_pixels
    return tuple(output)


def encode_animation(prepared: PreparedAnimation, options: EncoderOptions, progress_callback=None) -> EncodedAnimation:
    width, height = validate_geometry(prepared.width, prepared.height)
    total_pixels = width * height
    raw_frame_bytes = total_pixels * 2
    frame_records: list[tuple[int, bytes]] = []
    frame_stats: list[EncodedFrameStats] = []

    previous_pixels: tuple[int, ...] | None = None
    keyframe_interval = max(1, int(options.keyframe_interval))
    use_any_delta = False
    total_frames = max(1, len(prepared.rgb565_frames))
    for index, pixels in enumerate(prepared.rgb565_frames):
        keyframe_payload = encode_keyframe_payload(pixels)
        frame_flags = FRAME_FLAG_KEYFRAME
        payload = keyframe_payload

        should_force_keyframe = (
            index == 0
            or not options.enable_deltas
            or (keyframe_interval > 0 and index % keyframe_interval == 0)
            or previous_pixels is None
        )

        if not should_force_keyframe and previous_pixels is not None:
            delta_payload = encode_delta_payload(
                previous_pixels,
                pixels,
                gap_tolerance=options.delta_gap_tolerance,
            )
            if len(delta_payload) < len(keyframe_payload):
                frame_flags = FRAME_FLAG_DELTA
                payload = delta_payload
                use_any_delta = True

        frame_records.append((frame_flags, payload))
        frame_stats.append(
            EncodedFrameStats(
                index=index,
                frame_flags=frame_flags,
                payload_bytes=len(payload),
                total_bytes=FRAME_HEADER_STRUCT.size + len(payload),
                raw_bytes=raw_frame_bytes,
                ratio=(FRAME_HEADER_STRUCT.size + len(payload)) / raw_frame_bytes,
            )
        )
        previous_pixels = tuple(int(value) & 0xFFFF for value in pixels)
        _emit_progress(
            progress_callback,
            mode="determinate",
            value=_progress_value(55.0, 95.0, index + 1, total_frames),
            message="Encoding frame %d/%d..." % (index + 1, total_frames),
        )

    flags = FORMAT_COMPRESSION_MIXED_RLE << FORMAT_FLAG_COMPRESSION_SHIFT
    if use_any_delta:
        flags |= FORMAT_FLAG_HAS_DELTAS
    else:
        flags |= FORMAT_FLAG_KEYFRAME_ONLY

    _emit_progress(progress_callback, mode="determinate", value=97.0, message="Finalizing frame table...")
    frame_table_offset = HEADER_STRUCT.size
    offset = frame_table_offset + len(frame_records) * FRAME_TABLE_ENTRY_STRUCT.size
    frame_offsets = []
    for _frame_flags, payload in frame_records:
        frame_offsets.append(offset)
        offset += FRAME_HEADER_STRUCT.size + len(payload)

    return EncodedAnimation(
        width=width,
        height=height,
        fps=prepared.fps,
        flags=flags,
        frame_offsets=frame_offsets,
        frame_records=frame_records,
        frame_stats=frame_stats,
    )


def write_rlea(path: str | os.PathLike[str], encoded: EncodedAnimation) -> Path:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    header = HEADER_STRUCT.pack(
        RLEA_MAGIC,
        RLEA_VERSION,
        encoded.width,
        encoded.height,
        encoded.fps,
        encoded.frame_count,
        encoded.flags,
        HEADER_STRUCT.size,
    )

    with output_path.open("wb") as handle:
        handle.write(header)
        for offset in encoded.frame_offsets:
            handle.write(FRAME_TABLE_ENTRY_STRUCT.pack(offset))
        for frame_flags, payload in encoded.frame_records:
            handle.write(FRAME_HEADER_STRUCT.pack(frame_flags, len(payload)))
            handle.write(payload)
    return output_path


def encode_animation_source(source_path: str | os.PathLike[str], output_path: str | os.PathLike[str], options: EncoderOptions) -> tuple[PreparedAnimation, EncodedAnimation, Path]:
    prepared = prepare_animation_source(source_path, options)
    encoded = encode_animation(prepared, options)
    written_path = write_rlea(output_path, encoded)
    return prepared, encoded, written_path


def read_rlea(path: str | os.PathLike[str]) -> RLEAFile:
    file_path = Path(path)
    with file_path.open("rb") as handle:
        header_data = handle.read(HEADER_STRUCT.size)
        if len(header_data) != HEADER_STRUCT.size:
            raise ValueError("RLEA header truncated")
        magic, version, width, height, fps, frame_count, flags, frame_table_offset = HEADER_STRUCT.unpack(header_data)
        if magic != RLEA_MAGIC:
            raise ValueError("RLEA magic mismatch")

        header = RLEAHeader(
            version=int(version),
            width=int(width),
            height=int(height),
            fps=int(fps),
            frame_count=int(frame_count),
            flags=int(flags),
            frame_table_offset=int(frame_table_offset),
        )
        validate_geometry(header.width, header.height)

        handle.seek(header.frame_table_offset)
        frame_offsets = [
            FRAME_TABLE_ENTRY_STRUCT.unpack(handle.read(FRAME_TABLE_ENTRY_STRUCT.size))[0]
            for _ in range(header.frame_count)
        ]

        frames = []
        for offset in frame_offsets:
            handle.seek(offset)
            frame_header = handle.read(FRAME_HEADER_STRUCT.size)
            if len(frame_header) != FRAME_HEADER_STRUCT.size:
                raise ValueError("frame header truncated")
            frame_flags, payload_len = FRAME_HEADER_STRUCT.unpack(frame_header)
            payload = handle.read(payload_len)
            if len(payload) != payload_len:
                raise ValueError("frame payload truncated")
            frames.append(RLEAFrameRecord(offset=int(offset), flags=int(frame_flags), payload=payload))

    return RLEAFile(header=header, frame_offsets=frame_offsets, frames=frames)


def decode_rlea_frames(rlea_file: RLEAFile) -> list[tuple[int, ...]]:
    total_pixels = rlea_file.header.width * rlea_file.header.height
    decoded_frames: list[tuple[int, ...]] = []
    previous_pixels: tuple[int, ...] | None = None
    for record in rlea_file.frames:
        if record.flags & FRAME_FLAG_DELTA:
            if previous_pixels is None:
                raise ValueError("delta frame encountered before keyframe")
            current = decode_delta_payload(record.payload, previous_pixels, total_pixels)
        else:
            current = decode_keyframe_payload(record.payload, total_pixels)
        decoded_frames.append(current)
        previous_pixels = current
    return decoded_frames


def validate_round_trip(encoded: EncodedAnimation, original_frames: Sequence[Sequence[int]]) -> None:
    buffer = io.BytesIO()
    header = HEADER_STRUCT.pack(
        RLEA_MAGIC,
        RLEA_VERSION,
        encoded.width,
        encoded.height,
        encoded.fps,
        encoded.frame_count,
        encoded.flags,
        HEADER_STRUCT.size,
    )
    buffer.write(header)
    for offset in encoded.frame_offsets:
        buffer.write(FRAME_TABLE_ENTRY_STRUCT.pack(offset))
    for frame_flags, payload in encoded.frame_records:
        buffer.write(FRAME_HEADER_STRUCT.pack(frame_flags, len(payload)))
        buffer.write(payload)

    buffer.seek(0)
    temp_path = None
    # Parse directly from the in-memory image using the same structs.
    header_data = buffer.read(HEADER_STRUCT.size)
    magic, version, width, height, fps, frame_count, flags, frame_table_offset = HEADER_STRUCT.unpack(header_data)
    if magic != RLEA_MAGIC or version != RLEA_VERSION:
        raise ValueError("round-trip header mismatch")
    buffer.seek(frame_table_offset)
    frame_offsets = [FRAME_TABLE_ENTRY_STRUCT.unpack(buffer.read(FRAME_TABLE_ENTRY_STRUCT.size))[0] for _ in range(frame_count)]
    frames = []
    for offset in frame_offsets:
        buffer.seek(offset)
        frame_flags, payload_len = FRAME_HEADER_STRUCT.unpack(buffer.read(FRAME_HEADER_STRUCT.size))
        frames.append(RLEAFrameRecord(offset=offset, flags=frame_flags, payload=buffer.read(payload_len)))
    decoded = decode_rlea_frames(
        RLEAFile(
            header=RLEAHeader(version, width, height, fps, frame_count, flags, frame_table_offset),
            frame_offsets=frame_offsets,
            frames=frames,
        )
    )

    if len(decoded) != len(original_frames):
        raise ValueError("round-trip frame count mismatch")
    for index, frame in enumerate(decoded):
        expected = tuple(int(value) & 0xFFFF for value in original_frames[index])
        if frame != expected:
            raise ValueError(f"round-trip mismatch at frame {index}")
