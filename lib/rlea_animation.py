try:
    import ustruct as struct
except ImportError:
    import struct

import os

try:
    import time
    ticks_ms = time.ticks_ms
    ticks_add = time.ticks_add
    ticks_diff = time.ticks_diff
except (AttributeError, ImportError):
    import time

    def ticks_ms():
        return int(time.time() * 1000)

    def ticks_add(value, delta):
        return value + delta

    def ticks_diff(left, right):
        return left - right

try:
    import rlea_decode as _native_rlea_decode
except ImportError:
    _native_rlea_decode = None


RLEA_MAGIC = b"RLEA"
RLEA_VERSION = 1


class _StructCompat:
    def __init__(self, fmt):
        self.fmt = fmt
        self.size = struct.calcsize(fmt)

    def unpack(self, data):
        return struct.unpack(self.fmt, data)


def _make_struct(fmt):
    struct_type = getattr(struct, "Struct", None)
    if struct_type is not None:
        return struct_type(fmt)
    return _StructCompat(fmt)


HEADER_STRUCT = _make_struct("<4sBHHBHBI")
FRAME_HEADER_STRUCT = _make_struct("<BI")
FRAME_TABLE_ENTRY_STRUCT = _make_struct("<I")

FRAME_FLAG_KEYFRAME = 1 << 0
FRAME_FLAG_DELTA = 1 << 1


class RLEAHeader:
    def __init__(self, version, width, height, fps, frame_count, flags, frame_table_offset):
        self.version = int(version)
        self.width = int(width)
        self.height = int(height)
        self.fps = int(fps)
        self.frame_count = int(frame_count)
        self.flags = int(flags)
        self.frame_table_offset = int(frame_table_offset)


_desktop_core = None


def _load_desktop_core():
    global _desktop_core
    if _desktop_core is not None:
        return _desktop_core

    try:
        import sys
        import importlib.util
    except ImportError:
        return None

    try:
        module_dir = os.path.dirname(__file__)
    except NameError:
        return None

    core_path = os.path.join(module_dir, "tools", "rlea_core.py")
    if not os.path.exists(core_path):
        return None

    spec = importlib.util.spec_from_file_location("_rlea_core_fallback", core_path)
    if spec is None or spec.loader is None:
        return None

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    _desktop_core = module
    return module


def _read_header(fp):
    header_data = fp.read(HEADER_STRUCT.size)
    if len(header_data) != HEADER_STRUCT.size:
        raise ValueError("RLEA header truncated")
    magic, version, width, height, fps, frame_count, flags, frame_table_offset = HEADER_STRUCT.unpack(header_data)
    if magic != RLEA_MAGIC:
        raise ValueError("RLEA magic mismatch")
    if version != RLEA_VERSION:
        raise ValueError("Unsupported RLEA version")
    if height != 64 or width not in (64, 128):
        raise ValueError("Unsupported RLEA geometry")
    if fps <= 0:
        raise ValueError("RLEA FPS must be positive")
    return RLEAHeader(version, width, height, fps, frame_count, flags, frame_table_offset)


def read_rlea_header(filename):
    with open(filename, "rb") as fp:
        return _read_header(fp)


def _buffer_to_words(buffer_obj, total_pixels):
    out = [0] * total_pixels
    for index in range(total_pixels):
        byte_index = index * 2
        out[index] = buffer_obj[byte_index] | (buffer_obj[byte_index + 1] << 8)
    return tuple(out)


def _write_words_to_buffer(buffer_obj, words):
    for index, pixel in enumerate(words):
        value = int(pixel) & 0xFFFF
        byte_index = index * 2
        buffer_obj[byte_index] = value & 0xFF
        buffer_obj[byte_index + 1] = (value >> 8) & 0xFF


def _decode_payload(payload, framebuffer, frame_flags, width, height):
    if _native_rlea_decode is not None:
        _native_rlea_decode.decode(payload, framebuffer, frame_flags, width, height)
        return

    desktop_core = _load_desktop_core()
    if desktop_core is None:
        raise ImportError("rlea_decode.mpy is required on-device; desktop fallback is unavailable")

    total_pixels = int(width) * int(height)
    payload_bytes = bytes(payload)
    if frame_flags & FRAME_FLAG_DELTA:
        previous = _buffer_to_words(framebuffer, total_pixels)
        decoded = desktop_core.decode_delta_payload(payload_bytes, previous, total_pixels)
    else:
        decoded = desktop_core.decode_keyframe_payload(payload_bytes, total_pixels)
    _write_words_to_buffer(framebuffer, decoded)


class RLEAnimation:
    def __init__(self, filename, framebuffer):
        if not hasattr(framebuffer, "rgb565_buffer"):
            raise TypeError("framebuffer must expose rgb565_buffer")

        show_method = getattr(framebuffer, "show", None)
        if show_method is None:
            show_method = getattr(framebuffer, "refresh", None)
        if show_method is None:
            raise TypeError("framebuffer must expose show() or refresh()")

        self.filename = filename
        self.framebuffer = framebuffer
        self._show_method = show_method
        self._fp = open(filename, "rb")
        try:
            self.header = _read_header(self._fp)

            if int(framebuffer.width) != self.header.width or int(framebuffer.height) != self.header.height:
                raise ValueError(
                    "Animation geometry does not match framebuffer: expected %dx%d" % (self.header.width, self.header.height)
                )

            self.width = self.header.width
            self.height = self.header.height
            self.fps = self.header.fps
            self.frame_count = self.header.frame_count
            self.flags = self.header.flags
            self.frame_interval_ms = max(1, int(round(1000.0 / self.fps)))

            self._frame_offsets = []
            self._frame_flags = []
            self._frame_payload_lengths = []
            self._keyframe_anchors = []
            self._payload_buffer = bytearray(1)
            self._payload_view = memoryview(self._payload_buffer)
            self._load_frame_index()

            self.current_frame = -1
            self.playing = False
            self.loop = False
            self._next_tick = 0
            self._pending_frame = 0
        except Exception:
            self._fp.close()
            self._fp = None
            raise

    def _load_frame_index(self):
        self._fp.seek(self.header.frame_table_offset)
        for _ in range(self.frame_count):
            offset_data = self._fp.read(FRAME_TABLE_ENTRY_STRUCT.size)
            if len(offset_data) != FRAME_TABLE_ENTRY_STRUCT.size:
                raise ValueError("RLEA frame table truncated")
            self._frame_offsets.append(FRAME_TABLE_ENTRY_STRUCT.unpack(offset_data)[0])

        last_keyframe = None
        max_payload_length = 0
        for frame_index, offset in enumerate(self._frame_offsets):
            self._fp.seek(offset)
            header_data = self._fp.read(FRAME_HEADER_STRUCT.size)
            if len(header_data) != FRAME_HEADER_STRUCT.size:
                raise ValueError("RLEA frame header truncated")
            frame_flags, payload_length = FRAME_HEADER_STRUCT.unpack(header_data)
            frame_flags = int(frame_flags)
            payload_length = int(payload_length)
            self._frame_flags.append(frame_flags)
            self._frame_payload_lengths.append(payload_length)
            if payload_length > max_payload_length:
                max_payload_length = payload_length

            if frame_flags & FRAME_FLAG_DELTA:
                if last_keyframe is None:
                    raise ValueError("First RLEA frame cannot be a delta frame")
                self._keyframe_anchors.append(last_keyframe)
            else:
                last_keyframe = frame_index
                self._keyframe_anchors.append(frame_index)

        self._payload_buffer = bytearray(max(1, max_payload_length))
        self._payload_view = memoryview(self._payload_buffer)

    def close(self):
        self.stop()
        if self._fp is not None:
            self._fp.close()
            self._fp = None

    deinit = close

    def stop(self):
        self.playing = False

    def play(self, loop=True):
        self.loop = bool(loop)
        self.playing = True
        if self.current_frame < 0:
            self._pending_frame = 0
        elif self.current_frame >= self.frame_count - 1:
            self._pending_frame = 0 if self.loop else self.current_frame
        else:
            self._pending_frame = self.current_frame + 1
        self._next_tick = ticks_ms()
        return self

    def seek(self, frame_index, show=True):
        index = int(frame_index)
        if index < 0 or index >= self.frame_count:
            raise IndexError("frame index out of range")
        self.playing = False
        self._decode_to_frame(index)
        if show:
            self._show_method()
        self._pending_frame = index + 1
        if self._pending_frame >= self.frame_count:
            self._pending_frame = 0
        return index

    def step(self, show=True):
        if self.current_frame < 0:
            next_frame = 0
        else:
            next_frame = self.current_frame + 1
            if next_frame >= self.frame_count:
                next_frame = 0
        self._decode_to_frame(next_frame)
        if show:
            self._show_method()
        self._pending_frame = next_frame + 1
        if self._pending_frame >= self.frame_count:
            self._pending_frame = 0
        return next_frame

    def update(self, show=True):
        if not self.playing:
            return False

        now = ticks_ms()
        if ticks_diff(now, self._next_tick) < 0:
            return False

        target_frame = self._pending_frame
        if target_frame >= self.frame_count:
            if not self.loop:
                self.stop()
                return False
            target_frame = 0

        self._decode_to_frame(target_frame)
        if show:
            self._show_method()
        self._pending_frame = target_frame + 1
        self._next_tick = ticks_add(self._next_tick, self.frame_interval_ms)
        if ticks_diff(now, self._next_tick) >= self.frame_interval_ms:
            self._next_tick = ticks_add(now, self.frame_interval_ms)
        return True

    def _decode_to_frame(self, frame_index):
        if frame_index == self.current_frame:
            return

        if (
            self.current_frame >= 0
            and frame_index == self.current_frame + 1
            and (self._frame_flags[frame_index] & FRAME_FLAG_DELTA)
        ):
            self._apply_frame(frame_index)
            return

        anchor = self._keyframe_anchors[frame_index]
        if anchor is None:
            raise ValueError("No keyframe anchor available")

        if self.current_frame != anchor:
            self._apply_frame(anchor)

        next_index = anchor + 1
        while next_index <= frame_index:
            self._apply_frame(next_index)
            next_index += 1

    def _apply_frame(self, frame_index):
        payload_length = self._frame_payload_lengths[frame_index]
        payload = self._read_frame_payload(frame_index, payload_length)
        _decode_payload(
            payload,
            self.framebuffer.rgb565_buffer,
            self._frame_flags[frame_index],
            self.width,
            self.height,
        )
        self.current_frame = frame_index

    def _read_frame_payload(self, frame_index, payload_length):
        offset = self._frame_offsets[frame_index] + FRAME_HEADER_STRUCT.size
        self._fp.seek(offset)
        payload_view = self._payload_view[:payload_length]
        read_bytes = self._fp.readinto(payload_view)
        if read_bytes != payload_length:
            raise ValueError("RLEA frame payload truncated")
        return payload_view


__all__ = [
    "FRAME_FLAG_DELTA",
    "FRAME_FLAG_KEYFRAME",
    "RLEAnimation",
    "RLEAHeader",
    "read_rlea_header",
]