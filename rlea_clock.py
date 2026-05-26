import time

from machine import I2C, Pin

from ds1307 import DS1307
from hub75_565 import Hub75FrameBuffer, RGB565Sprite, rgb565
from rlea_animation import RLEAnimation, read_rlea_header


_SLEEP_MS = getattr(time, "sleep_ms", None)


def sleep_ms(value):
    if _SLEEP_MS is not None:
        _SLEEP_MS(value)
    else:
        time.sleep(value / 1000.0)

DEFAULT_ANIMATION = "/anim/White_Ring.rlea"
#DEFAULT_ANIMATION = "/anim/WHO_Vortex.rlea"
#DEFAULT_ANIMATION = "/anim/kirkslap_128_BMP.rlea"
DEFAULT_LOOP = True

I2C_ID = 0
I2C_SDA_PIN = 0
I2C_SCL_PIN = 1
I2C_FREQ = 400000
RTC_ADDR = 0x68

BAR_BG = rgb565(0, 0, 0)
BAR_LINE = rgb565(24, 72, 144)
TIME_COLOR = rgb565(180, 255, 220)
DATE_COLOR = rgb565(255, 226, 120)
SHADOW_COLOR = rgb565(0, 0, 0)

TOP_BAR_HEIGHT = 10
BOTTOM_BAR_HEIGHT = 10


class _AnimationSurface(RGB565Sprite):
    def show(self):
        return None

    refresh = show


def _init_rtc():
    i2c = I2C(
        I2C_ID,
        scl=Pin(I2C_SCL_PIN),
        sda=Pin(I2C_SDA_PIN),
        freq=I2C_FREQ,
    )
    return DS1307(addr=RTC_ADDR, i2c=i2c)


def _format_clock_strings(rtc_datetime):
    year, month, day, hour, minute, second, _weekday, _yearday = rtc_datetime
    time_text = "%02d:%02d:%02d" % (hour, minute, second)
    date_text = "%02d/%02d/%02d" % (day, month, year % 100)
    return time_text, date_text


def _draw_centered_text(display, y, text, color):
    text_width = len(text) * 8
    x = (display.width - text_width) // 2
    if x < 0:
        x = 0

    fb = display.framebuf
    fb.text(text, x + 1, y + 1, SHADOW_COLOR)
    fb.text(text, x, y, color)


def _draw_clock_overlay(display, time_text, date_text):
    fb = display.framebuf
    #fb.fill_rect(0, 0, display.width, TOP_BAR_HEIGHT, BAR_BG)
    #fb.fill_rect(0, display.height - BOTTOM_BAR_HEIGHT, display.width, BOTTOM_BAR_HEIGHT, BAR_BG)
    #fb.hline(0, TOP_BAR_HEIGHT - 1, display.width, BAR_LINE)
    #fb.hline(0, display.height - BOTTOM_BAR_HEIGHT, display.width, BAR_LINE)
    _draw_centered_text(display, 1, time_text, TIME_COLOR)
    _draw_centered_text(display, display.height - BOTTOM_BAR_HEIGHT + 2, date_text, DATE_COLOR)


def run(filename=DEFAULT_ANIMATION, loop=DEFAULT_LOOP):
    rtc = _init_rtc()
    header = read_rlea_header(filename)
    display = Hub75FrameBuffer(width=header.width, height=header.height)
    animation_surface = _AnimationSurface(header.width, header.height)
    animation = RLEAnimation(filename, animation_surface)

    rtc_datetime = rtc.datetime
    time_text, date_text = _format_clock_strings(rtc_datetime)
    last_second = rtc_datetime[5]

    try:
        animation.play(loop=loop)
        animation.update(show=False)
        display.rgb565_buffer[:] = animation_surface.rgb565_buffer
        _draw_clock_overlay(display, time_text, date_text)
        display.show()

        while True:
            frame_changed = animation.update(show=False)

            rtc_datetime = rtc.datetime
            current_second = rtc_datetime[5]
            second_changed = current_second != last_second
            if second_changed:
                last_second = current_second
                time_text, date_text = _format_clock_strings(rtc_datetime)

            if frame_changed or second_changed:
                display.rgb565_buffer[:] = animation_surface.rgb565_buffer
                _draw_clock_overlay(display, time_text, date_text)
                display.show()
            else:
                sleep_ms(10)
    finally:
        animation.close()
        display.deinit()


if __name__ == "__main__":
    run()