# pyright: reportMissingImports=false, reportAttributeAccessIssue=false

from array import array
import framebuf
import time

from hub75_565 import Hub75FrameBuffer, rgb565


BLACK = 0
INK = rgb565(5, 10, 18)
NAVY = rgb565(8, 22, 44)
RED = rgb565(255, 0, 0)
GREEN = rgb565(0, 255, 0)
BLUE = rgb565(0, 0, 255)
CYAN_FULL = rgb565(0, 255, 255)
MAGENTA = rgb565(255, 0, 255)
YELLOW = rgb565(255, 255, 0)
CYAN = rgb565(40, 210, 255)
MINT = rgb565(80, 255, 180)
ORANGE = rgb565(255, 165, 24)
PINK = rgb565(255, 90, 140)
WHITE = rgb565(255, 255, 255)
AMBER = rgb565(255, 220, 32)

PALETTE = (RED, GREEN, BLUE, YELLOW, MAGENTA, CYAN_FULL, WHITE)

TRIANGLE = array("h", [0, -10, 10, 8, -10, 8])
HEXAGON = array("h", [0, -10, 8, -5, 8, 5, 0, 10, -8, 5, -8, -5])
CHEVRON = array("h", [-10, 0, 0, -8, 10, 0, 0, 8])

COLOR_SWATCHES = (
    ("R", RED, WHITE),
    ("G", GREEN, BLACK),
    ("B", BLUE, WHITE),
    ("C", CYAN_FULL, BLACK),
    ("M", MAGENTA, WHITE),
    ("Y", YELLOW, BLACK),
)


def ping_pong(step, span, speed=1, offset=0):
    if span <= 0:
        return 0
    cycle = span << 1
    pos = (step * speed + offset) % cycle
    if pos >= span:
        pos = cycle - pos
    return pos


def draw_header(surface, width, label, fps):
    surface.fill_rect(0, 0, width, 10, NAVY)
    surface.text(label, 2, 1, WHITE)
    surface.fill_rect(width - 40, 0, 40, 10, NAVY)
    surface.text(str(fps), width - 38, 1, YELLOW)
    surface.text("fps", width - 16, 1, GREEN)


def draw_poly_outline(surface, x, y, points, color):
    count = len(points) >> 1
    if count < 2:
        return

    for i in range(count):
        start = i << 1
        end = ((i + 1) % count) << 1
        surface.line(
            x + points[start],
            y + points[start + 1],
            x + points[end],
            y + points[end + 1],
            color,
        )


def draw_poly(surface, x, y, points, color, filled=False):
    if hasattr(surface, "poly"):
        if filled:
            surface.poly(x, y, points, color, True)
        else:
            surface.poly(x, y, points, color)
        return
    draw_poly_outline(surface, x, y, points, color)


def make_sprite():
    key = BLACK
    buf = bytearray(16 * 16 * 2)
    sprite = framebuf.FrameBuffer(buf, 16, 16, framebuf.RGB565)
    sprite.fill(key)
    sprite.fill_rect(3, 3, 10, 10, RED)
    sprite.rect(2, 2, 12, 12, WHITE)
    sprite.line(0, 8, 15, 8, BLUE)
    sprite.line(8, 0, 8, 15, BLUE)
    sprite.fill_rect(6, 6, 4, 4, GREEN)
    return sprite, key


def draw_scroll_phase(surface, display, frame, fps):
    surface.scroll(-2, 0)
    stripe = PALETTE[(frame >> 2) % len(PALETTE)]
    surface.fill_rect(display.width - 2, 0, 2, display.height, stripe)
    surface.fill_rect(0, 0, display.width, 10, NAVY)
    surface.fill_rect(0, display.height - 10, display.width, 10, INK)
    draw_header(surface, display.width, "scroll/text", fps)

    message_x = display.width - ((frame * 4) % (display.width + 112))
    surface.text("framebuf speed", message_x, display.height - 8, YELLOW)

    y0 = 14 + ((frame * 3) % (display.height - 26))
    y1 = 14 + ((frame * 5 + 21) % (display.height - 26))
    surface.line(0, y0, display.width - 1, y1, RED)

    box_x = 8 + ((frame * 2) % 48)
    box_y = 16 + ((frame // 2) % 20)
    surface.rect(box_x, box_y, 34, 18, GREEN)
    surface.fill_rect(74, 18 + ((frame >> 1) % 18), 18, 10, BLUE)
    surface.text("scroll", 74, 42, MAGENTA)


def draw_rect_phase(surface, display, frame, fps):
    surface.fill(INK)
    draw_header(surface, display.width, "rect/line", fps)

    for i in range(14):
        x = (frame * ((i % 3) + 2) + i * 9) % display.width
        y = 12 + ((frame * ((i % 4) + 1) + i * 7) % (display.height - 20))
        w = 4 + ((frame + i * 5) & 15)
        h = 4 + ((frame * 2 + i * 3) & 11)
        if x + w > display.width:
            w = display.width - x
        if y + h > display.height:
            h = display.height - y
        surface.fill_rect(x, y, w, h, PALETTE[(i + (frame >> 2)) % len(PALETTE)])

    for i in range(7):
        inset = i * 4
        width = display.width - inset * 2
        height = display.height - 14 - i * 4
        if width <= 0 or height <= 0:
            break
        surface.rect(inset, 11 + i * 2, width, height, PALETTE[(i + frame) % len(PALETTE)])

    point_x = ping_pong(frame, display.width - 1, 3)
    point_y = 12 + ping_pong(frame, display.height - 13, 2, 13)
    surface.line(0, 11, point_x, point_y, WHITE)
    surface.line(display.width - 1, 11, point_x, point_y, RED)
    surface.line(0, display.height - 1, point_x, point_y, GREEN)
    surface.line(display.width - 1, display.height - 1, point_x, point_y, BLUE)
    surface.text("fill_rect spam", 4, display.height - 9, CYAN_FULL)


def draw_poly_phase(surface, display, frame, fps, sprite, sprite_key):
    surface.fill(INK)
    draw_header(surface, display.width, "poly/blit", fps)

    if hasattr(surface, "ellipse"):
        ex = 22 + ping_pong(frame, display.width - 44, 2, 9)
        ey = 24 + ping_pong(frame, display.height - 34, 1, 33)
        surface.ellipse(ex, ey, 18, 10, BLUE, True)
        surface.ellipse(display.width - ex, ey + 10, 12, 8, YELLOW)

    draw_poly(
        surface,
        18 + ping_pong(frame, display.width - 36, 2, 5),
        26 + ping_pong(frame, display.height - 34, 3, 11),
        TRIANGLE,
        RED,
        False,
    )
    draw_poly(
        surface,
        24 + ping_pong(frame, display.width - 48, 3, 27),
        24 + ping_pong(frame, display.height - 32, 2, 17),
        HEXAGON,
        GREEN,
        True,
    )
    draw_poly(
        surface,
        18 + ping_pong(frame, display.width - 36, 4, 41),
        22 + ping_pong(frame, display.height - 28, 2, 39),
        CHEVRON,
        MAGENTA,
        False,
    )

    sprite_x = ping_pong(frame, display.width - 16, 5, 7)
    sprite_y = 12 + ping_pong(frame, display.height - 28, 3, 29)
    surface.blit(sprite, sprite_x, sprite_y, sprite_key)
    surface.text("text poly blit", 4, display.height - 9, CYAN_FULL)


def draw_color_check_screen(surface, display):
    surface.fill(BLACK)
    surface.fill_rect(0, 0, display.width, 10, NAVY)
    surface.text("RGB / CMY", 2, 1, WHITE)
    surface.text("check", 84, 1, AMBER)

    cell_w = display.width // 3
    cell_h = (display.height - 12) // 2

    for index, (label, color, text_color) in enumerate(COLOR_SWATCHES):
        col = index % 3
        row = index // 3
        x = col * cell_w
        y = 12 + row * cell_h
        w = cell_w if col < 2 else display.width - x
        h = cell_h if row == 0 else display.height - y
        border = WHITE if text_color == BLACK else BLACK

        surface.fill_rect(x, y, w, h, color)
        surface.rect(x, y, w, h, border)
        surface.text(label, x + ((w - 8) >> 1), y + ((h - 8) >> 1), text_color)


def color_check(hold_ms=0):
    display = Hub75FrameBuffer(width=128, height=64)
    surface = display.framebuf

    try:
        draw_color_check_screen(surface, display)
        display.show()
        if hold_ms and hold_ms > 0:
            time.sleep_ms(hold_ms)
            return
        while True:
            time.sleep_ms(1000)
    except KeyboardInterrupt:
        pass
    finally:
        display.deinit()


def main():
    display = Hub75FrameBuffer(width=128, height=64)
    surface = display.framebuf
    sprite, sprite_key = make_sprite()

    frame = 0
    fps = 0
    last_phase = -1
    fps_start = time.ticks_ms()
    fps_frame_mark = 0

    try:
        while True:
            phase = (frame // 160) % 3
            if phase != last_phase:
                surface.fill(BLACK)
                last_phase = phase

            if phase == 0:
                draw_scroll_phase(surface, display, frame, fps)
            elif phase == 1:
                draw_rect_phase(surface, display, frame, fps)
            else:
                draw_poly_phase(surface, display, frame, fps, sprite, sprite_key)

            display.show()
            frame += 1

            now = time.ticks_ms()
            if time.ticks_diff(now, fps_start) >= 1000:
                fps = frame - fps_frame_mark
                fps_frame_mark = frame
                fps_start = now
    except KeyboardInterrupt:
        pass
    finally:
        display.deinit()


def run(mode="demo"):
    if mode == "demo":
        main()
        return
    if mode == "color_check":
        color_check()
        return
    raise ValueError("mode must be 'demo' or 'color_check'")


RUN_MODE = "demo"

run(RUN_MODE)