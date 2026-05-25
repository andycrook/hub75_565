# pyright: reportMissingImports=false, reportAttributeAccessIssue=false

import os
import time

from hub75_565 import Hub75FrameBuffer, rgb565


#BMP_PATH = "/bmp/b5_128_CORRECT.bmp"
#RAW_DIR = "/bin"
#RAW_PATH = "/bin/b5_128_CORRECT.rgb565"

BMP_PATH = "/bmp/enterprise64.bmp"
RAW_DIR = "/rgb565"
RAW_PATH = "/rgb565/enterprise64.rgb565"

BMP_GAMMA = 1.0
BMP_BRIGHTNESS = 1.0
BMP_CONTRAST = 1.0
BMP_HUE = 0.0

BLACK = 0
WHITE = rgb565(255, 255, 255)
YELLOW = rgb565(255, 255, 0)
NAVY = rgb565(8, 22, 44)

_TIME_TICKS_MS = getattr(time, "ticks_ms", None)
_TIME_TICKS_DIFF = getattr(time, "ticks_diff", None)
_TIME_SLEEP_MS = getattr(time, "sleep_ms", None)


def _ticks_ms():
    if _TIME_TICKS_MS is not None:
        return int(_TIME_TICKS_MS())
    return int(time.monotonic() * 1000)


def _ticks_diff(now_ms, last_ms):
    if _TIME_TICKS_DIFF is not None:
        return int(_TIME_TICKS_DIFF(now_ms, last_ms))
    return int(now_ms - last_ms)


def _sleep_ms(duration_ms):
    if _TIME_SLEEP_MS is not None:
        _TIME_SLEEP_MS(duration_ms)
        return
    time.sleep(duration_ms / 1000.0)


def ensure_dir(path):
    try:
        os.stat(path)
    except OSError:
        os.mkdir(path)


def stamp_status(surface, label, elapsed_ms):
    surface.fill_rect(0, 0, 82, 10, NAVY)
    surface.text(label, 2, 1, WHITE)
    surface.text(str(int(elapsed_ms)), 62, 1, YELLOW)


def decode_bmp(display):
    start = _ticks_ms()
    result = display.load_bmp(
        BMP_PATH,
        gamma=BMP_GAMMA,
        brightness=BMP_BRIGHTNESS,
        contrast=BMP_CONTRAST,
        hue=BMP_HUE,
    )
    elapsed_ms = _ticks_diff(_ticks_ms(), start)
    return result, elapsed_ms


def reload_raw(display):
    start = _ticks_ms()
    result = display.load_rgb565(RAW_PATH)
    elapsed_ms = _ticks_diff(_ticks_ms(), start)
    return result, elapsed_ms


def main():
    display = Hub75FrameBuffer(width=128, height=64)
    ensure_dir(RAW_DIR)

    try:
        bmp_size, bmp_ms = decode_bmp(display)
        start = _ticks_ms()
        raw_bytes = display.save_rgb565(RAW_PATH)
        save_ms = _ticks_diff(_ticks_ms(), start)

        stamp_status(display.framebuf, "BMP", bmp_ms)
        print("BMP decode:", bmp_size[0], "x", bmp_size[1], bmp_ms, "ms")
        print("RGB565 cache saved:", raw_bytes, "bytes in", save_ms, "ms")
        display.show()
        _sleep_ms(1500)

        display.framebuf.fill(BLACK)
        display.show()
        _sleep_ms(250)

        raw_size, raw_ms = reload_raw(display)
        stamp_status(display.framebuf, "RGB565", raw_ms)
        print("RGB565 reload:", raw_size[0], "x", raw_size[1], raw_ms, "ms")
        display.show()
        _sleep_ms(1500)

        phase = 0
        while True:
            if phase & 1:
                _, elapsed_ms = reload_raw(display)
                stamp_status(display.framebuf, "RGB565", elapsed_ms)
                print("RGB565 reload:", elapsed_ms, "ms")
            else:
                _, elapsed_ms = decode_bmp(display)
                stamp_status(display.framebuf, "BMP", elapsed_ms)
                print("BMP decode:", elapsed_ms, "ms")
            display.show()
            phase += 1
            _sleep_ms(2000)
    except KeyboardInterrupt:
        pass
    finally:
        display.deinit()


main()