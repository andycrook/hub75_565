try:
    import time
    sleep_ms = time.sleep_ms
except (AttributeError, ImportError):
    import time

    def sleep_ms(value):
        time.sleep(value / 1000.0)

import os

from hub75_565 import Hub75FrameBuffer, rgb565


WIDTH = 128
HEIGHT = 64
HEADER_HEIGHT = 10

BLACK = 0
BG = rgb565(4, 10, 20)
NAVY = rgb565(8, 22, 48)
CYAN = rgb565(60, 220, 255)
RED = rgb565(255, 80, 90)
GREEN = rgb565(100, 255, 140)
YELLOW = rgb565(255, 220, 64)
ORANGE = rgb565(255, 165, 32)
WHITE = rgb565(255, 255, 255)
LILAC = rgb565(200, 180, 255)
KEY = rgb565(255, 0, 255)

SPRITE_DIR = "/rgb565"
SHIP_RGB565_PATH = SPRITE_DIR + "/ship_12x12.rgb565"
ORB_RGB565_PATH = SPRITE_DIR + "/orb_10x10.rgb565"
GEM_RGB565_PATH = SPRITE_DIR + "/gem_8x8.rgb565"


_TIME_TICKS_MS = getattr(time, "ticks_ms", None)
_TIME_TICKS_DIFF = getattr(time, "ticks_diff", None)


def _ticks_ms():
    if _TIME_TICKS_MS is not None:
        return int(_TIME_TICKS_MS())
    return int(time.time() * 1000)


def _ticks_diff(now_ms, last_ms):
    if _TIME_TICKS_DIFF is not None:
        return int(_TIME_TICKS_DIFF(now_ms, last_ms))
    return int(now_ms - last_ms)


def ping_pong(step, span, speed=1, offset=0):
    if span <= 0:
        return 0
    cycle = span << 1
    pos = (step * speed + offset) % cycle
    if pos >= span:
        pos = cycle - pos
    return pos


def _paint_sprite(sprite, rows, palette):
    sprite.fill(KEY)
    for y, row in enumerate(rows):
        for x, token in enumerate(row):
            color = palette.get(token)
            if color is not None:
                sprite.pixel(x, y, color)


def _ensure_dir(path):
    try:
        os.stat(path)
    except OSError:
        os.mkdir(path)


def _save_sprite_if_changed(sprite, path):
    sprite_bytes = bytes(sprite.buffer)
    expected_size = len(sprite_bytes)

    try:
        if os.stat(path)[6] == expected_size:
            with open(path, "rb") as fp:
                if fp.read(expected_size) == sprite_bytes:
                    return False
    except OSError:
        pass

    sprite.save_rgb565(path)
    return True


def export_sprites(ship, orb, gem):
    _ensure_dir(SPRITE_DIR)
    _save_sprite_if_changed(ship, SHIP_RGB565_PATH)
    _save_sprite_if_changed(orb, ORB_RGB565_PATH)
    _save_sprite_if_changed(gem, GEM_RGB565_PATH)


def make_ship_sprite(display):
    sprite = display.create_sprite(12, 12, fill=KEY, transparent_key=KEY)
    _paint_sprite(
        sprite,
        (
            ".....W......",
            "....WWW.....",
            "...WCCCW....",
            "..WCCCCCW...",
            ".WCCYGYCCW..",
            "WCCCGGGCCCW.",
            ".WCCYGYCCW..",
            "..WCCCCCW...",
            "...WCCCW....",
            "...O...O....",
            "..O.....O...",
            "............",
        ),
        {
            "W": WHITE,
            "C": CYAN,
            "Y": YELLOW,
            "G": GREEN,
            "O": ORANGE,
        },
    )
    return sprite


def make_orb_sprite(display):
    sprite = display.create_sprite(10, 10, fill=KEY, transparent_key=KEY)
    _paint_sprite(
        sprite,
        (
            "....RR....",
            "..RRYYRR..",
            ".RYYYYYYR.",
            ".RYYWWYYR.",
            "RYYWWWWYYR",
            "RYYWWWWYYR",
            ".RYYWWYYR.",
            ".RYYYYYYR.",
            "..RRYYRR..",
            "....RR....",
        ),
        {
            "R": RED,
            "Y": YELLOW,
            "W": WHITE,
        },
    )
    return sprite


def make_gem_sprite(display):
    sprite = display.create_sprite(8, 8, fill=KEY, transparent_key=KEY)
    _paint_sprite(
        sprite,
        (
            "...WW...",
            "..WLLW..",
            ".WLLLLW.",
            "WLLWWLLW",
            ".WLLLLW.",
            "..WLLW..",
            "...WW...",
            "........",
        ),
        {
            "W": WHITE,
            "L": LILAC,
        },
    )
    return sprite


def draw_background(surface, frame, fps):
    surface.fill(BG)
    surface.fill_rect(0, 0, WIDTH, HEADER_HEIGHT, NAVY)
    surface.text("SPRITES", 2, 1, WHITE)
    fps_text = str(fps)
    surface.text(fps_text, WIDTH - (len(fps_text) * 8) - 2, 1, YELLOW)

    for stripe in range(0, HEIGHT - HEADER_HEIGHT, 8):
        y = HEADER_HEIGHT + stripe
        surface.hline(0, y, WIDTH, NAVY if (stripe >> 3) & 1 else BLACK)

    for star in range(8):
        sx = (star * 9 +frame * (1 + (star & 1))) % WIDTH
        sy = HEADER_HEIGHT + ((star * 6  * (2 + (star % 3))) % (HEIGHT - HEADER_HEIGHT))
        surface.pixel(sx, sy, WHITE)


def main():
    display = Hub75FrameBuffer(width=WIDTH, height=HEIGHT)
    surface = display.framebuf

    ship = make_ship_sprite(display)
    orb = make_orb_sprite(display)
    gem = make_gem_sprite(display)
    export_sprites(ship, orb, gem)

    frame = 0
    fps = 0
    fps_mark = 0
    fps_start = _ticks_ms()

    try:
        while True:
            draw_background(surface, frame, fps)

            ship_x = ping_pong(frame, WIDTH - ship.width, 2, 5)
            ship_y = HEADER_HEIGHT + 4 + ping_pong(frame, HEIGHT - HEADER_HEIGHT - ship.height - 4, 1, 7)
            orb_x = ping_pong(frame, WIDTH - orb.width, 3, 17)
            orb_y = HEADER_HEIGHT + 3 + ping_pong(frame, HEIGHT - HEADER_HEIGHT - orb.height - 3, 2, 19)
            gem_x = ping_pong(frame, WIDTH - gem.width, 4, 9)
            gem_y = HEADER_HEIGHT + 10 + ping_pong(frame, HEIGHT - HEADER_HEIGHT - gem.height - 10, 3, 13)

            display.blit(ship, ship_x, ship_y)
            display.blit(orb, orb_x, orb_y)
            display.blit(gem, gem_x, gem_y)

            surface.text("RGB565 blit", 2, HEIGHT - 8, CYAN)
            display.show()

            frame += 1
            now = _ticks_ms()
            if _ticks_diff(now, fps_start) >= 1000:
                fps = frame - fps_mark
                fps_mark = frame
                fps_start = now

            #sleep_ms(16)
    except KeyboardInterrupt:
        pass
    finally:
        display.deinit()


if __name__ == "__main__":
    main()


