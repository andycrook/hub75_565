# pyright: reportMissingImports=false, reportUndefinedVariable=false, reportArgumentType=false, reportCallIssue=false

import _thread
import os
import time
from array import array
import framebuf as _framebuf
import machine
import micropython
import rp2
from machine import Pin
from micropython import const
from rp2 import PIO, DMA, asm_pio

try:
    import hub75_fbconv as _hub75_fbconv
except ImportError:
    _hub75_fbconv = None

try:
    import hfm_fbtext as _hfm_fbtext
except ImportError:
    _hfm_fbtext = None

try:
    import bmp565_ops as _bmp565_ops
except ImportError:
    _bmp565_ops = None


DEFAULT_MATRIX_SIZE_X = const(64)
MAX_MATRIX_SIZE_X = const(128)
MATRIX_SIZE_Y = const(64)

DEFAULT_BITPLANES = const(6)
BYTES_PER_BITPLANE = const(4096)

TEXT_ALIGN_LEFT = const(0)
TEXT_ALIGN_CENTER = const(1)
TEXT_ALIGN_RIGHT = const(2)

TEXT_MARQUEE_SPEED_MODE_FRAME = const(0)
TEXT_MARQUEE_SPEED_MODE_TIME = const(1)

TEXT_MARQUEE_SPEED_SHIFT = const(8)
TEXT_MARQUEE_SPEED_UNIT = const(1 << TEXT_MARQUEE_SPEED_SHIFT)

PIO_FREQ_LED = const(124_000_000)
PIO_FREQ_ROW = const(124_000_000)
PIO_FREQ_BRIGHT = const(25_000_000)
MACHINE_FREQ = const(250_000_000)
SHOW_WAIT_TIMEOUT_MS = const(250)


_TIME_TICKS_MS = getattr(time, "ticks_ms", None)
_TIME_TICKS_DIFF = getattr(time, "ticks_diff", None)


def rgb565(red, green, blue):
    return ((red & 0xF8) << 8) | ((green & 0xFC) << 3) | (blue >> 3)


def _ticks_ms():
    if _TIME_TICKS_MS is not None:
        return int(_TIME_TICKS_MS())
    return int(time.monotonic() * 1000)


def _ticks_diff(now_ms, last_ms):
    if _TIME_TICKS_DIFF is not None:
        return int(_TIME_TICKS_DIFF(now_ms, last_ms))
    return int(now_ms - last_ms)


def _to_rgb565(color):
    if isinstance(color, int):
        return color & 0xFFFF
    if isinstance(color, (tuple, list)) and len(color) >= 3:
        return rgb565(int(color[0]), int(color[1]), int(color[2]))
    raise TypeError("color must be RGB565 int or (r, g, b) tuple")


def _normalize_rotation(rotation):
    rotation_val = int(rotation)
    rotation_val %= 360
    if rotation_val < 0:
        rotation_val += 360
    if rotation_val not in (0, 90, 180, 270):
        raise ValueError("rotation must be 0/90/180/270 degrees")
    return rotation_val


def _align_to_int(align):
    if isinstance(align, str):
        align_key = align.lower()
        if align_key == "centre":
            align_key = "center"
        if align_key == "left":
            return TEXT_ALIGN_LEFT
        if align_key == "center":
            return TEXT_ALIGN_CENTER
        if align_key == "right":
            return TEXT_ALIGN_RIGHT
        raise ValueError("align must be left/center/right")

    align_val = int(align)
    if align_val not in (TEXT_ALIGN_LEFT, TEXT_ALIGN_CENTER, TEXT_ALIGN_RIGHT):
        raise ValueError("align must be left/center/right")
    return align_val


def _marquee_speed_mode_to_int(speed_mode):
    if isinstance(speed_mode, str):
        speed_mode_key = speed_mode.lower()
        if speed_mode_key in ("frame", "per_frame"):
            return TEXT_MARQUEE_SPEED_MODE_FRAME
        if speed_mode_key in ("time", "per_second", "pps"):
            return TEXT_MARQUEE_SPEED_MODE_TIME
        raise ValueError("speed_mode must be frame/time")

    speed_mode_val = int(speed_mode)
    if speed_mode_val not in (
        TEXT_MARQUEE_SPEED_MODE_FRAME,
        TEXT_MARQUEE_SPEED_MODE_TIME,
    ):
        raise ValueError("speed_mode must be frame/time")
    return speed_mode_val


def _normalize_geometry(width, height):
    width_val = int(width)
    height_val = int(height)

    if height_val != MATRIX_SIZE_Y:
        raise ValueError("height must be 64")
    if width_val not in (DEFAULT_MATRIX_SIZE_X, MAX_MATRIX_SIZE_X):
        raise ValueError("width must be 64 or 128")

    return width_val, height_val


def _bytes_per_bitplane(width, height):
    return (int(width) * int(height)) >> 1

def _read_bmp_dimensions(path):
    with open(path, "rb") as fp:
        header = fp.read(26)

    if len(header) < 26 or header[0:2] != b"BM":
        raise ValueError("Not a valid BMP file")

    dib_header_size = int.from_bytes(header[14:18], "little")
    if dib_header_size < 40:
        raise ValueError("unsupported BMP header")

    width = int.from_bytes(header[18:22], "little", signed=True)
    height = int.from_bytes(header[22:26], "little", signed=True)
    if width <= 0 or height == 0:
        raise ValueError("invalid BMP dimensions")

    return width, abs(height)


def _rgb565_to_rgb888(color):
    color_val = _to_rgb565(color)
    red5 = (color_val >> 11) & 0x1F
    green6 = (color_val >> 5) & 0x3F
    blue5 = color_val & 0x1F
    return (
        (red5 << 3) | (red5 >> 2),
        (green6 << 2) | (green6 >> 4),
        (blue5 << 3) | (blue5 >> 2),
    )


def _resolve_sprite_transparency(transparency_color=None, transparent_key=None):
    source_color = transparency_color
    key565 = None

    if transparent_key is not None:
        key565 = _to_rgb565(transparent_key)
        if source_color is None:
            source_color = transparent_key
    elif source_color is not None:
        key565 = _to_rgb565(source_color)

    if source_color is None:
        return None, key565

    if isinstance(source_color, int):
        return _rgb565_to_rgb888(source_color), key565

    if isinstance(source_color, (tuple, list)) and len(source_color) >= 3:
        return (
            int(source_color[0]) & 0xFF,
            int(source_color[1]) & 0xFF,
            int(source_color[2]) & 0xFF,
        ), key565

    raise TypeError(
        "transparency_color/transparent_key must be RGB565 int or (r, g, b) tuple"
    )


class RGB565Sprite:
    def __init__(
        self,
        width,
        height,
        buffer=None,
        fill=None,
        transparent_key=None,
    ):
        width_val = int(width)
        height_val = int(height)
        if width_val <= 0 or height_val <= 0:
            raise ValueError("sprite width and height must be > 0")

        expected_len = width_val * height_val * 2
        if buffer is None:
            buffer = bytearray(expected_len)
        elif len(buffer) < expected_len:
            raise ValueError("sprite buffer too small")

        self.width = width_val
        self.height = height_val
        self.buffer = buffer
        self.rgb565_buffer = buffer
        self.framebuf = _framebuf.FrameBuffer(
            self.buffer,
            self.width,
            self.height,
            _framebuf.RGB565,
        )
        self.fb = self.framebuf
        self.transparent_key = (
            None if transparent_key is None else _to_rgb565(transparent_key)
        )

        if fill is not None:
            self.framebuf.fill(_to_rgb565(fill))

    def fill(self, color):
        self.framebuf.fill(_to_rgb565(color))

    def pixel(self, x, y, color=None):
        if color is None:
            return self.framebuf.pixel(x, y)
        self.framebuf.pixel(x, y, _to_rgb565(color))

    def save_rgb565(self, filename):
        buffer_len = len(self.buffer)
        with open(filename, "wb") as fp:
            fp.write(self.buffer)
        return buffer_len


@asm_pio(
    out_init=(rp2.PIO.OUT_LOW,) * 6,
    sideset_init=(rp2.PIO.OUT_LOW,) * 1,
    set_init=(rp2.PIO.OUT_HIGH,) * 2,
    out_shiftdir=PIO.SHIFT_RIGHT,
)
def led_data_128():
    set(x, 31)
    in_(x, 5)
    in_(x, 2)
    wrap_target()

    mov(x, isr)

    label("Byte Counter")
    pull().side(0)[1]
    nop()[1].side(0)
    out(pins, 6).side(1)
    nop()[1].side(1)
    jmp(x_dec, "Byte Counter")

    irq(block, 4)
    irq(block, 5)
    wrap()


@asm_pio(
    out_init=(rp2.PIO.OUT_LOW,) * 6,
    sideset_init=(rp2.PIO.OUT_LOW,) * 1,
    set_init=(rp2.PIO.OUT_HIGH,) * 2,
    out_shiftdir=PIO.SHIFT_RIGHT,
)
def led_data_64():
    set(x, 15)
    in_(x, 4)
    in_(x, 2)
    wrap_target()

    mov(x, isr)

    label("Byte Counter")
    pull().side(0)[1]
    nop()[1].side(0)
    out(pins, 6).side(1)
    nop()[1].side(1)
    jmp(x_dec, "Byte Counter")

    irq(block, 4)
    irq(block, 5)
    wrap()


@asm_pio(
    out_init=(rp2.PIO.OUT_LOW,) * 5,
    set_init=(rp2.PIO.OUT_HIGH,) * 1,
    out_shiftdir=PIO.SHIFT_RIGHT,
)
def address_counter():
    set(x, 31)
    label("Address Decrement")
    wait(1, irq, 4)
    mov(pins, x)
    set(pins, 1)
    set(pins, 0)
    irq(rel(0), 6)
    wait(1, irq, 7)
    irq(clear, 5)
    jmp(x_dec, "Address Decrement")


@asm_pio(set_init=(rp2.PIO.OUT_HIGH,) * 1, out_shiftdir=rp2.PIO.SHIFT_LEFT)
def output():
    wrap_target()
    wait(1, irq, 6)
    pull(noblock)
    mov(x, osr)
    mov(y, x)
    set(pins, 0)

    label("y Decrement")
    nop()[1]
    jmp(y_dec, "y Decrement")

    set(pins, 1)
    irq(rel(0), 7)
    wrap()


class _BitplaneDMA:
    def __init__(self, state_machine, sm_index, transfer_len):
        self._sm = state_machine
        self._transfer_len = transfer_len
        self._dma = DMA()
        pio_index = sm_index >> 2
        sm_local = sm_index & 0x03
        treq = (pio_index << 3) | sm_local
        self._ctrl = self._dma.pack_ctrl(size=0, inc_write=False, treq_sel=treq)

    @micropython.native
    def start(self, buf):
        self._dma.config(
            read=buf,
            write=self._sm,
            count=self._transfer_len,
            ctrl=self._ctrl,
            trigger=True,
        )

    @micropython.native
    def wait(self):
        while self._dma.active():
            pass

    def close(self):
        self.wait()
        self._dma.close()


class Hub75FrameBuffer:
    def __init__(
        self,
        data_pin_start=2,
        clock_pin=13,
        latch_pin_start=14,
        row_pin_start=8,
        output_enable_pin=15,
        width=DEFAULT_MATRIX_SIZE_X,
        height=MATRIX_SIZE_Y,
    ):
        converter = _hub75_fbconv
        if converter is None:
            raise ImportError(
                "hub75_fbconv.mpy is required; build the native module from src/lib/hub75_fbconv"
            )

        machine.freq(MACHINE_FREQ)

        led_data_sm_id = 0
        address_counter_sm_id = 1
        output_sm_id = 2

        self.width, self.height = _normalize_geometry(width, height)
        self._converter = converter
        self.rgb565_buffer = bytearray(self.width * self.height * 2)
        self.framebuf = _framebuf.FrameBuffer(
            self.rgb565_buffer,
            self.width,
            self.height,
            _framebuf.RGB565,
        )
        self.fb = self.framebuf
        self.font = None
        self.font_letter_spacing = 1
        self._bmp_color_lut = None
        self._bmp_color_lut_key = None
        self._text_marquee_state = None
        self._sync_converter_layout()
        led_data_program = led_data_64 if self.width == DEFAULT_MATRIX_SIZE_X else led_data_128

        self.led_data_sm = rp2.StateMachine(
            led_data_sm_id,
            led_data_program,
            freq=PIO_FREQ_LED,
            out_base=Pin(data_pin_start),
            sideset_base=Pin(clock_pin),
        )
        self.address_counter_sm = rp2.StateMachine(
            address_counter_sm_id,
            address_counter,
            freq=PIO_FREQ_ROW,
            out_base=Pin(row_pin_start),
            set_base=Pin(latch_pin_start),
        )
        self.output_sm = rp2.StateMachine(
            output_sm_id,
            output,
            freq=PIO_FREQ_BRIGHT,
            set_base=Pin(output_enable_pin),
        )

        self.dma = _BitplaneDMA(self.led_data_sm, led_data_sm_id, self.buffer_size)

        self.buffer_ready = False
        self.running = True
        self._refresh_thread_alive = False
        self._refresh_error = None

        self.address_counter_sm.active(1)
        self.led_data_sm.active(1)
        self.output_sm.active(1)

        _thread.start_new_thread(self.send_frames, ())

    def _raise_refresh_error(self):
        message = self._refresh_error
        if message:
            raise RuntimeError("HUB75 refresh thread stopped: %s" % message)
        raise RuntimeError("HUB75 refresh thread stopped")

    def _allocate_framebuffers(self):
        self.frame_buffer = [bytearray(self.buffer_size) for _ in range(self.bitplanes)]
        self.frame_buffer_temp = [bytearray(self.buffer_size) for _ in range(self.bitplanes)]

    def _sync_converter_layout(self):
        converter = self._converter
        self.buffer_size = _bytes_per_bitplane(self.width, self.height)
        bitplanes = getattr(converter, "bitplanes", None)

        if bitplanes is None:
            self._probe_converter_layout()
            return

        self.bitplanes = int(bitplanes)
        self._allocate_framebuffers()

    def _convert_framebuffer(self, planes=None):
        converter = self._converter
        try:
            return converter.convert(self.rgb565_buffer, planes, self.width, self.height)
        except TypeError:
            if self.width != MAX_MATRIX_SIZE_X or self.height != MATRIX_SIZE_Y:
                raise RuntimeError(
                    "hub75_fbconv.mpy must be rebuilt for runtime 64x64/128x64 selection"
                )
            if planes is None:
                return converter.convert(self.rgb565_buffer)
            return converter.convert(self.rgb565_buffer, planes)

    def _probe_converter_layout(self):
        framebuffer = self._convert_framebuffer()
        framebuffer_bytes = len(framebuffer)
        if framebuffer_bytes % self.buffer_size != 0:
            raise ValueError("hub75_fbconv framebuffer geometry mismatch")
        self.bitplanes = framebuffer_bytes // self.buffer_size
        self._allocate_framebuffers()

    @micropython.native
    def send_frames(self):
        dma = self.dma
        dma_start = dma.start
        dma_wait = dma.wait
        self._refresh_thread_alive = True
        self._refresh_error = None
        try:
            sm_out = self.output_sm.put

            def generate_schedule(bitplanes):
                schedule = []
                for plane in range(bitplanes):
                    capped = plane if plane < 5 else 5
                    delay_val = (1 << capped) - 1
                    repeats = 1 << max(0, plane - 5)
                    schedule.append((repeats, delay_val, plane))
                return schedule

            bitplane_schedule = generate_schedule(self.bitplanes)

            while self.running:
                if self.buffer_ready:
                    self.frame_buffer, self.frame_buffer_temp = (
                        self.frame_buffer_temp,
                        self.frame_buffer,
                    )
                    self.buffer_ready = False

                fb = self.frame_buffer
                for repeats, delay_val, index in bitplane_schedule:
                    for _ in range(repeats):
                        sm_out(delay_val)
                        dma_start(fb[index])
                        dma_wait()
        except Exception as exc:
            self.running = False
            if exc.args:
                self._refresh_error = exc.args[0]
            else:
                self._refresh_error = "unknown refresh error"
        finally:
            self.buffer_ready = False
            try:
                dma_wait()
            except Exception:
                pass
            self._refresh_thread_alive = False

    @micropython.native
    def show(self):
        if not self._refresh_thread_alive:
            self._raise_refresh_error()

        try:
            self._convert_framebuffer(self.frame_buffer_temp)
        except ValueError as exc:
            message = exc.args[0] if exc.args else ""
            if message != "framebuffer plane count mismatch":
                raise exc
            self._sync_converter_layout()
            try:
                self._convert_framebuffer(self.frame_buffer_temp)
            except ValueError as retry_exc:
                retry_message = retry_exc.args[0] if retry_exc.args else ""
                if retry_message != "framebuffer plane count mismatch":
                    raise retry_exc
                self._probe_converter_layout()
                self._convert_framebuffer(self.frame_buffer_temp)

        wait_started_ms = _ticks_ms()
        self.buffer_ready = True
        while self.buffer_ready:
            if not self._refresh_thread_alive:
                self.buffer_ready = False
                self._raise_refresh_error()
            if _ticks_diff(_ticks_ms(), wait_started_ms) > SHOW_WAIT_TIMEOUT_MS:
                self.buffer_ready = False
                raise RuntimeError("HUB75 refresh stalled")
            machine.idle()

    refresh = show

    def fill(self, color):
        self.framebuf.fill(color)

    def pixel(self, x, y, color=None):
        if color is None:
            return self.framebuf.pixel(x, y)
        self.framebuf.pixel(x, y, color)

    def clear(self):
        self.framebuf.fill(0)

    def _require_bmp565_ops(self):
        module = _bmp565_ops
        if module is None:
            raise ImportError(
                "bmp565_ops.mpy is required; build the native module from src/lib/bmp565_ops"
            )
        return module

    def _get_bmp_color_lut(self, gamma, brightness, contrast, raw):
        if raw:
            return None

        gamma_value = float(gamma)
        brightness_value = float(brightness)
        contrast_value = float(contrast)
        if (
            gamma_value == 1.0
            and brightness_value == 1.0
            and contrast_value == 1.0
        ):
            return None

        lut_key = (gamma_value, brightness_value, contrast_value)
        lut = self._bmp_color_lut
        if lut is None:
            lut = bytearray(256)
            self._bmp_color_lut = lut

        if self._bmp_color_lut_key != lut_key:
            for value in range(256):
                adjusted = int(round(((value / 255.0) ** gamma_value) * 255.0))
                adjusted = int(round(adjusted * brightness_value))
                adjusted = int(round((adjusted - 128.0) * contrast_value + 128.0))
                if adjusted < 0:
                    adjusted = 0
                elif adjusted > 255:
                    adjusted = 255
                lut[value] = adjusted
            self._bmp_color_lut_key = lut_key

        return lut

    def load_bmp(
        self,
        filename,
        x=0,
        y=0,
        gamma=1.0,
        brightness=1.0,
        contrast=1.0,
        hue=0.0,
        raw=False,
        transparency_color=None,
    ):
        module = self._require_bmp565_ops()
        with open(filename, "rb") as fp:
            bmp_data = fp.read()

        raw_mode = bool(raw)
        hue_shift_value = float(hue)
        if hue_shift_value:
            raise ValueError(
                "hue adjustment is not supported by bmp565_ops on this target"
            )
        color_lut = self._get_bmp_color_lut(gamma, brightness, contrast, raw_mode)

        trans_r = trans_g = trans_b = 0
        transparency_enabled = transparency_color is not None
        if transparency_enabled:
            trans_r, trans_g, trans_b = transparency_color

        return module.load_bmp(
            bmp_data,
            self.rgb565_buffer,
            int(self.width),
            int(self.height),
            int(x),
            int(y),
            bool(raw_mode),
            bool(transparency_enabled),
            int(trans_r) & 0xFF,
            int(trans_g) & 0xFF,
            int(trans_b) & 0xFF,
            color_lut if color_lut is not None else None,
            bool(color_lut is not None),
            False,
            0.0,
        )

    @staticmethod
    def create_sprite(width, height, buffer=None, fill=None, transparent_key=None):
        return RGB565Sprite(
            width,
            height,
            buffer=buffer,
            fill=fill,
            transparent_key=transparent_key,
        )

    def load_bmp_sprite(
        self,
        filename,
        gamma=1.0,
        brightness=1.0,
        contrast=1.0,
        hue=0.0,
        raw=False,
        transparency_color=None,
        transparent_key=None,
    ):
        width, height = _read_bmp_dimensions(filename)
        source_transparency, sprite_transparent_key = _resolve_sprite_transparency(
            transparency_color=transparency_color,
            transparent_key=transparent_key,
        )
        sprite = RGB565Sprite(
            width,
            height,
            fill=sprite_transparent_key if sprite_transparent_key is not None else 0,
            transparent_key=sprite_transparent_key,
        )

        module = self._require_bmp565_ops()
        with open(filename, "rb") as fp:
            bmp_data = fp.read()

        raw_mode = bool(raw)
        hue_shift_value = float(hue)
        if hue_shift_value:
            raise ValueError(
                "hue adjustment is not supported by bmp565_ops on this target"
            )
        color_lut = self._get_bmp_color_lut(gamma, brightness, contrast, raw_mode)

        trans_r = trans_g = trans_b = 0
        transparency_enabled = source_transparency is not None
        if transparency_enabled:
            trans_r, trans_g, trans_b = source_transparency

        module.load_bmp(
            bmp_data,
            sprite.buffer,
            int(sprite.width),
            int(sprite.height),
            0,
            0,
            bool(raw_mode),
            bool(transparency_enabled),
            int(trans_r) & 0xFF,
            int(trans_g) & 0xFF,
            int(trans_b) & 0xFF,
            color_lut if color_lut is not None else None,
            bool(color_lut is not None),
            False,
            0.0,
        )
        return sprite

    def load_rgb565_sprite(self, filename, width, height, transparent_key=None):
        sprite = RGB565Sprite(
            width,
            height,
            transparent_key=transparent_key,
        )
        expected_size = len(sprite.buffer)
        file_size = os.stat(filename)[6]
        if file_size != expected_size:
            raise ValueError(
                "RGB565 sprite file size mismatch: expected %d bytes" % expected_size
            )

        with open(filename, "rb") as fp:
            read_bytes = fp.readinto(sprite.buffer)
        if read_bytes != expected_size:
            raise ValueError("RGB565 sprite file truncated")

        return sprite

    @staticmethod
    def rotate_sprite(source, rotation):
        source_fb = getattr(source, "framebuf", getattr(source, "fb", None))
        if source_fb is None:
            raise TypeError("source must expose framebuf or fb")

        source_width = getattr(source, "width", None)
        source_height = getattr(source, "height", None)
        if source_width is None or source_height is None:
            raise TypeError("source must expose width and height")

        rotation_val = _normalize_rotation(rotation)
        source_width = int(source_width)
        source_height = int(source_height)

        if rotation_val in (90, 270):
            rotated_width = source_height
            rotated_height = source_width
        else:
            rotated_width = source_width
            rotated_height = source_height

        rotated = RGB565Sprite(
            rotated_width,
            rotated_height,
            transparent_key=getattr(source, "transparent_key", None),
        )

        for src_y in range(source_height):
            for src_x in range(source_width):
                color = source_fb.pixel(src_x, src_y)
                if rotation_val == 0:
                    dst_x = src_x
                    dst_y = src_y
                elif rotation_val == 90:
                    dst_x = source_height - 1 - src_y
                    dst_y = src_x
                elif rotation_val == 180:
                    dst_x = source_width - 1 - src_x
                    dst_y = source_height - 1 - src_y
                else:
                    dst_x = src_y
                    dst_y = source_width - 1 - src_x
                rotated.pixel(dst_x, dst_y, color)

        return rotated

    def blit(self, source, x, y, key=None):
        source_fb = getattr(source, "framebuf", getattr(source, "fb", source))
        if key is None:
            key = getattr(source, "transparent_key", None)

        if key is None:
            self.framebuf.blit(source_fb, int(x), int(y))
        else:
            self.framebuf.blit(source_fb, int(x), int(y), _to_rgb565(key))

    def save_rgb565(self, filename):
        buffer_len = len(self.rgb565_buffer)
        with open(filename, "wb") as fp:
            fp.write(self.rgb565_buffer)
        return buffer_len

    def load_rgb565(self, filename):
        expected_size = len(self.rgb565_buffer)
        file_size = os.stat(filename)[6]
        if file_size != expected_size:
            raise ValueError(
                "RGB565 buffer file size mismatch: expected %d bytes" % expected_size
            )

        with open(filename, "rb") as fp:
            read_bytes = fp.readinto(self.rgb565_buffer)
        if read_bytes != expected_size:
            raise ValueError("RGB565 buffer file truncated")

        return self.width, self.height

    def _require_hfm_fbtext(self):
        module = _hfm_fbtext
        if module is None:
            raise ImportError(
                "hfm_fbtext.mpy is required; build the native module from src/lib/hfm_fbtext"
            )
        return module

    @staticmethod
    def _font_signed_byte(value):
        return value - 256 if value >= 128 else value

    def font_load(self, path, set_default=True):
        font_size = os.stat(path)[6]
        with open(path, "rb") as fp:
            header = fp.read(8)
            if len(header) != 8 or header[:4] != b"HFM1":
                raise ValueError("Not a valid HFM font")

            ascent = header[4]
            descent = header[5]
            glyph_count = int.from_bytes(header[6:8], "little")

            data = bytearray(font_size)
            data_mv = memoryview(data)
            write_pos = 0
            glyph_entries = []

            for _ in range(glyph_count):
                glyph_header = fp.read(7)
                if len(glyph_header) != 7:
                    raise ValueError("Unexpected EOF while reading glyph header")

                cp = int.from_bytes(glyph_header[0:2], "little")
                if cp > 0xFFFF:
                    data_len = glyph_header[6]
                    fp.seek(data_len, 1)
                    continue

                width = glyph_header[2]
                height = glyph_header[3]
                xoff = glyph_header[4]
                yoff = glyph_header[5]
                data_len = glyph_header[6]

                row_bytes = (width + 7) >> 3 if width else 0
                expected = row_bytes * height
                if expected != data_len:
                    raise ValueError(
                        "Glyph U+%04X has inconsistent bitmap length" % cp
                    )

                block_size = 5 + data_len
                if write_pos + block_size > 0x10000:
                    raise MemoryError(
                        "Font data exceeds 64 KiB; offsets array('H') cannot address it"
                    )

                block_start = write_pos
                data_mv[block_start:block_start + 5] = bytes(
                    (width, height, xoff, yoff, row_bytes)
                )
                write_pos += 5

                if data_len:
                    bitmap_view = data_mv[write_pos:write_pos + data_len]
                    read_bytes = fp.readinto(bitmap_view)
                    if read_bytes != data_len:
                        raise ValueError("Glyph U+%04X bitmap truncated" % cp)
                    write_pos += data_len

                glyph_entries.append((cp, block_start))

        del data_mv
        if write_pos < len(data):
            data = data[:write_pos]

        glyph_entries.sort(key=lambda item: item[0])
        codepoints = array("H")
        offsets = array("H")
        for cp, offset in glyph_entries:
            codepoints.append(cp)
            offsets.append(offset)

        font = {
            "ascent": ascent,
            "descent": descent,
            "line_height": ascent + descent,
            "codepoints": codepoints,
            "offsets": offsets,
            "data": data,
        }

        if set_default:
            self.font = font

        return font

    def measure_text(
        self,
        text,
        font=None,
        rotation=0,
        scale=1,
        box_width=None,
        letter_spacing=None,
        line_spacing=0,
        marquee_gap=16,
        marquee_width=None,
    ):
        if not text:
            return 0, 0, 0, 0

        active_font = self.font if font is None else font
        if not active_font:
            raise RuntimeError("No font loaded. Call font_load() first")

        module = self._require_hfm_fbtext()
        rotation_val = _normalize_rotation(rotation)
        scale_val = int(scale)
        if scale_val <= 0:
            raise ValueError("scale must be >= 1")

        box_width_val = 0 if box_width is None else max(0, int(box_width))
        letter_spacing_val = (
            self.font_letter_spacing if letter_spacing is None else int(letter_spacing)
        )
        line_spacing_val = int(line_spacing)
        marquee_gap_val = 0 if marquee_gap is None else max(0, int(marquee_gap))
        marquee_width_val = 0 if marquee_width is None else max(0, int(marquee_width))

        return module.measure(
            active_font["codepoints"],
            active_font["offsets"],
            active_font["data"],
            int(active_font["line_height"]),
            text,
            int(rotation_val),
            int(scale_val),
            int(box_width_val),
            int(letter_spacing_val),
            int(line_spacing_val),
            int(marquee_gap_val),
            int(marquee_width_val),
        )

    def draw_text(
        self,
        x,
        y,
        text,
        fg,
        bg=None,
        font=None,
        rotation=0,
        flip_h=False,
        flip_v=False,
        scale=1,
        align="left",
        box_width=None,
        letter_spacing=None,
        line_spacing=0,
        marquee_offset=0,
        marquee_gap=16,
        marquee_width=None,
    ):
        if not text:
            return

        active_font = self.font if font is None else font
        if not active_font:
            raise RuntimeError("No font loaded. Call font_load() first")

        module = self._require_hfm_fbtext()
        rotation_val = _normalize_rotation(rotation)
        scale_val = int(scale)
        if scale_val <= 0:
            raise ValueError("scale must be >= 1")

        align_val = _align_to_int(align)
        box_width_val = 0 if box_width is None else max(0, int(box_width))
        letter_spacing_val = (
            self.font_letter_spacing if letter_spacing is None else int(letter_spacing)
        )
        line_spacing_val = int(line_spacing)
        marquee_gap_val = 0 if marquee_gap is None else max(0, int(marquee_gap))
        marquee_width_val = 0 if marquee_width is None else max(0, int(marquee_width))

        fg565 = _to_rgb565(fg)
        if bg is None:
            bg565 = 0
            bg_enable = False
        else:
            bg565 = _to_rgb565(bg)
            bg_enable = True

        module.draw(
            self.rgb565_buffer,
            int(self.width),
            int(self.height),
            active_font["codepoints"],
            active_font["offsets"],
            active_font["data"],
            int(active_font["line_height"]),
            text,
            int(x),
            int(y),
            int(fg565),
            int(bg565),
            bg_enable,
            int(rotation_val),
            bool(flip_h),
            bool(flip_v),
            int(scale_val),
            int(align_val),
            int(box_width_val),
            int(letter_spacing_val),
            int(line_spacing_val),
            int(marquee_offset),
            int(marquee_gap_val),
            int(marquee_width_val),
        )

    def marquee_configure(
        self,
        text,
        x=0,
        y=0,
        width=None,
        speed=1.0,
        speed_mode="frame",
        gap=16,
        fg=(255, 255, 255),
        bg=None,
        font=None,
        rotation=0,
        flip_h=False,
        flip_v=False,
        scale=1,
        align="left",
        letter_spacing=None,
        line_spacing=0,
    ):
        if not text:
            self._text_marquee_state = None
            return None

        active_font = self.font if font is None else font
        if not active_font:
            raise RuntimeError("No font loaded. Call font_load() first")

        rotation_val = _normalize_rotation(rotation)
        scale_val = int(scale)
        if scale_val <= 0:
            raise ValueError("scale must be >= 1")

        letter_spacing_val = (
            self.font_letter_spacing if letter_spacing is None else int(letter_spacing)
        )
        line_spacing_val = int(line_spacing)
        gap_val = 0 if gap is None else max(0, int(gap))

        if width is None:
            if rotation_val in (90, 270):
                window_width = self.height - int(y)
            else:
                window_width = self.width - int(x)
        else:
            window_width = int(width)
        if window_width <= 0:
            window_width = self.width

        layout_width, _, _, _ = self.measure_text(
            text,
            font=active_font,
            rotation=0,
            scale=scale_val,
            box_width=0,
            letter_spacing=letter_spacing_val,
            line_spacing=line_spacing_val,
            marquee_gap=0,
            marquee_width=0,
        )

        cycle_width = layout_width + gap_val
        if cycle_width <= 0:
            cycle_width = window_width if window_width > 0 else 1

        speed_val = float(speed) if speed else 0.0
        if speed_val < 0:
            speed_val = 0.0

        speed_mode_val = _marquee_speed_mode_to_int(speed_mode)

        state = {
            "text": text,
            "font": active_font,
            "x": int(x),
            "y": int(y),
            "window_width": int(window_width),
            "gap": int(gap_val),
            "cycle_width": int(cycle_width),
            "offset_px": 0,
            "offset_fp": 0,
            "speed_fp": int(speed_val * TEXT_MARQUEE_SPEED_UNIT),
            "speed_mode": int(speed_mode_val),
            "last_tick_ms": int(_ticks_ms()),
            "fg": fg,
            "bg": bg,
            "rotation": int(rotation_val),
            "flip_h": bool(flip_h),
            "flip_v": bool(flip_v),
            "scale": int(scale_val),
            "align": align,
            "letter_spacing": int(letter_spacing_val),
            "line_spacing": int(line_spacing_val),
        }

        self._text_marquee_state = state
        return state

    def marquee_step(self, advance_px=None, draw=True):
        state = self._text_marquee_state
        if not state:
            return None

        module = self._require_hfm_fbtext()
        if advance_px is None:
            if int(state["speed_mode"]) == TEXT_MARQUEE_SPEED_MODE_TIME:
                now = int(_ticks_ms())
                delta_ms = int(_ticks_diff(now, int(state["last_tick_ms"])))
                if delta_ms < 0:
                    delta_ms = 0
                state["last_tick_ms"] = now
                advance_fp = (int(state["speed_fp"]) * delta_ms) // 1000
            else:
                advance_fp = int(state["speed_fp"])
        else:
            advance_fp = int(float(advance_px) * TEXT_MARQUEE_SPEED_UNIT)
            state["last_tick_ms"] = int(_ticks_ms())
        if advance_fp < 0:
            advance_fp = 0

        offset_px, offset_fp = module.step(
            int(state["offset_px"]),
            int(state["offset_fp"]),
            int(advance_fp),
            int(state["cycle_width"]),
        )
        state["offset_px"] = int(offset_px)
        state["offset_fp"] = int(offset_fp)

        if draw:
            self.marquee_draw()

        return state

    def marquee_draw(self):
        state = self._text_marquee_state
        if not state:
            return

        self.draw_text(
            state["x"],
            state["y"],
            state["text"],
            state["fg"],
            bg=state["bg"],
            font=state["font"],
            rotation=state["rotation"],
            flip_h=state["flip_h"],
            flip_v=state["flip_v"],
            scale=state["scale"],
            align=state["align"],
            box_width=0,
            letter_spacing=state["letter_spacing"],
            line_spacing=state["line_spacing"],
            marquee_offset=state["offset_px"],
            marquee_gap=state["gap"],
            marquee_width=state["window_width"],
        )

    def marquee_clear(self):
        self._text_marquee_state = None

    @micropython.native
    def deinit(self):
        if not self.running and not self._refresh_thread_alive:
            return
        self.running = False
        while self._refresh_thread_alive:
            pass
        self.dma.close()
        self.led_data_sm.active(0)
        self.address_counter_sm.active(0)
        self.output_sm.active(0)


__all__ = ("Hub75FrameBuffer", "RGB565Sprite", "rgb565")