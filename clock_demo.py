try:
    import time
    sleep_ms = time.sleep_ms
except (AttributeError, ImportError):
    import time

    def sleep_ms(value):
        time.sleep(value / 1000.0)

from machine import I2C, Pin

from ds1307 import DS1307
from hub75_565 import Hub75FrameBuffer, rgb565


DISPLAY_WIDTH = 128
DISPLAY_HEIGHT = 64

I2C_ID = 0
I2C_SDA_PIN = 0
I2C_SCL_PIN = 1
I2C_FREQ = 400000
RTC_ADDR = 0x68

BG_COLOR = rgb565(20, 0, 20)
FG_COLOR = rgb565(170, 220, 255)
DATE_COLOR = rgb565(120, 170, 255)
LABEL_COLOR = rgb565(80, 120, 220)
FRAME_COLOR = rgb565(30, 50, 120)
SHADOW_COLOR = rgb565(0, 0, 0)


def _format_clock_strings(rtc_datetime):
    year, month, day, hour, minute, second, _weekday, _yearday = rtc_datetime
    time_text = "%02d:%02d:%02d" % (hour, minute, second)
    date_text = "%02d/%02d/%02d" % (day, month, year % 100)
    return time_text, date_text


def _draw_centered_text(framebuffer, y, text, color, shadow=True):
    text_width = len(text) * 8
    x = (framebuffer.width - text_width) // 2
    if x < 0:
        x = 0

    fb = framebuffer.framebuf
    if shadow:
        fb.text(text, x + 1, y + 1, SHADOW_COLOR)
    fb.text(text, x, y, color)


def _draw_clock(framebuffer, time_text, date_text):
    fb = framebuffer.framebuf
    fb.fill(BG_COLOR)
    fb.rect(0, 0, framebuffer.width, framebuffer.height, FRAME_COLOR)
    fb.hline(3, 9, framebuffer.width - 6, FRAME_COLOR)
    fb.hline(3, framebuffer.height - 11, framebuffer.width - 6, FRAME_COLOR)

    _draw_centered_text(framebuffer, 19, time_text, FG_COLOR)
    _draw_centered_text(framebuffer, 35, date_text, DATE_COLOR)
    


def _init_rtc():
    i2c = I2C(
        I2C_ID,
        scl=Pin(I2C_SCL_PIN),
        sda=Pin(I2C_SDA_PIN),
        freq=I2C_FREQ,
    )
    return DS1307(addr=RTC_ADDR, i2c=i2c)


def main():
    rtc = _init_rtc()
    display = Hub75FrameBuffer(width=DISPLAY_WIDTH, height=DISPLAY_HEIGHT)
    last_second = None

    try:
        while True:
            rtc_datetime = rtc.datetime
            current_second = rtc_datetime[5]
            if current_second != last_second:
                last_second = current_second
                time_text, date_text = _format_clock_strings(rtc_datetime)
                _draw_clock(display, time_text, date_text)
                display.show()
            sleep_ms(50)
    except KeyboardInterrupt:
        print("Stopping clock demo...")
    finally:
        display.deinit()


if __name__ == "__main__":
    main()