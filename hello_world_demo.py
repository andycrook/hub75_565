from time import sleep_ms

from hub75_565 import Hub75FrameBuffer, rgb565


def main():
    display = Hub75FrameBuffer(width=128, height=64)
    surface = display.framebuf

    bg = rgb565(8, 28, 48)
    accent = rgb565(255, 180, 32)
    text = rgb565(255, 255, 255)

    surface.fill(bg)
    surface.rect(0, 0, display.width, display.height, accent)
    surface.fill_rect(4, 4, display.width - 8, 18, rgb565(0, 0, 0))
    surface.text("Hello World", 20, 10, text)
    surface.text("framebuf->HUB75", 4, 26, accent)
    surface.line(8, 42, display.width - 9, 42, accent)
    surface.text("RGB565 ", 40, 48, text)

    display.show()

    try:
        while True:
            sleep_ms(1000)
    finally:
        display.deinit()


main()