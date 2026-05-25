try:
    import time
    sleep_ms = time.sleep_ms
except (AttributeError, ImportError):
    import time

    def sleep_ms(value):
        time.sleep(value / 1000.0)

from hub75_565 import Hub75FrameBuffer
from rlea_animation import RLEAnimation, read_rlea_header


DEFAULT_ANIMATION = "/anim/kirkslap_128_BMP.rlea"
#DEFAULT_ANIMATION = "BTLC.rlea"
DEFAULT_LOOP = True


def run(filename=DEFAULT_ANIMATION, loop=DEFAULT_LOOP):
    header = read_rlea_header(filename)
    display = Hub75FrameBuffer(width=header.width, height=header.height)
    animation = RLEAnimation(filename, display)
    try:
        animation.play(loop=loop)
        while True:
            if not animation.update():
                sleep_ms(1)
    finally:
        animation.close()
        display.deinit()


if __name__ == "__main__":
    run()