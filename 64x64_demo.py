# pyright: reportMissingImports=false, reportAttributeAccessIssue=false

from array import array
import framebuf
import time

from hub75_565 import Hub75FrameBuffer, rgb565


WIDTH = 64
HEIGHT = 64
HEADER_HEIGHT = 10

BLACK = 0
INK = rgb565(5, 10, 18)
NAVY = rgb565(8, 22, 44)
RED = rgb565(255, 0, 0)
GREEN = rgb565(0, 255, 0)
BLUE = rgb565(0, 0, 255)
CYAN = rgb565(0, 255, 255)
MAGENTA = rgb565(255, 0, 255)
YELLOW = rgb565(255, 255, 0)
WHITE = rgb565(255, 255, 255)
ORANGE = rgb565(255, 165, 24)
MINT = rgb565(80, 255, 180)
AMBER = rgb565(255, 220, 32)

PALETTE = (RED, GREEN, BLUE, YELLOW, MAGENTA, CYAN, WHITE, ORANGE, MINT)

TRIANGLE = array("h", [0, -8, 8, 6, -8, 6])
DIAMOND = array("h", [0, -9, 7, 0, 0, 9, -7, 0])
CHEVRON = array("h", [-8, -3, 0, -9, 8, -3, 0, 7])

COLOR_SWATCHES = (
    ("R", RED, WHITE),
    ("G", GREEN, BLACK),
    ("B", BLUE, WHITE),
    ("C", CYAN, BLACK),
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


def draw_header(surface, label, fps):
    surface.fill_rect(0, 0, WIDTH, HEADER_HEIGHT, NAVY)
    surface.text(label, 2, 1, WHITE)
    fps_text = str(fps)
    surface.text(fps_text, WIDTH - (len(fps_text) * 8) - 2, 1, AMBER)


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
    buf = bytearray(12 * 12 * 2)
    sprite = framebuf.FrameBuffer(buf, 12, 12, framebuf.RGB565)
    sprite.fill(key)
    sprite.fill_rect(2, 2, 8, 8, ORANGE)
    sprite.rect(1, 1, 10, 10, WHITE)
    sprite.line(0, 6, 11, 6, BLUE)
    sprite.line(6, 0, 6, 11, BLUE)
    sprite.fill_rect(4, 4, 4, 4, MINT)
    return sprite, key


def draw_scroll_phase(surface, frame, fps):
    surface.scroll(-1, 0)
    stripe = PALETTE[(frame >> 2) % len(PALETTE)]
    surface.fill_rect(WIDTH - 1, 0, 1, HEIGHT, stripe)
    draw_header(surface, "SCRL", fps)
    surface.fill_rect(0, HEIGHT - 10, WIDTH, 10, INK)

    message_x = WIDTH - ((frame * 2) % (WIDTH + 56))
    surface.text("64x64", message_x, HEIGHT - 8, YELLOW)

    for i in range(8):
        bar_height = 4 + ((frame + i * 3) % 18)
        x = i * 8
        y = HEIGHT - 11 - bar_height
        color = PALETTE[(i + (frame >> 3)) % len(PALETTE)]
        surface.fill_rect(x + 1, y, 6, bar_height, color)

    marker_y = HEADER_HEIGHT + 4 + ping_pong(frame, HEIGHT - HEADER_HEIGHT - 22, 2, 7)
    surface.rect(6, marker_y, 16, 12, WHITE)
    surface.fill_rect(10, marker_y + 3, 8, 6, BLUE)
    surface.line(0, HEADER_HEIGHT + 2, WIDTH - 1, marker_y + 6, RED)


def draw_shapes_phase(surface, frame, fps, sprite, sprite_key):
    surface.fill(INK)
    draw_header(surface, "SHAP", fps)

    point_x = ping_pong(frame, WIDTH - 1, 3)
    point_y = HEADER_HEIGHT + ping_pong(frame, HEIGHT - HEADER_HEIGHT - 1, 2, 11)
    surface.line(0, HEADER_HEIGHT, point_x, point_y, WHITE)
    surface.line(WIDTH - 1, HEADER_HEIGHT, point_x, point_y, RED)
    surface.line(0, HEIGHT - 1, point_x, point_y, GREEN)
    surface.line(WIDTH - 1, HEIGHT - 1, point_x, point_y, BLUE)

    if hasattr(surface, "ellipse"):
        ex = 16 + ping_pong(frame, WIDTH - 32, 2, 3)
        ey = 20 + ping_pong(frame, HEIGHT - 32, 1, 9)
        surface.ellipse(ex, ey, 10, 6, BLUE, True)
        surface.ellipse(WIDTH - ex, ey + 8, 8, 5, YELLOW)

    draw_poly(
        surface,
        16 + ping_pong(frame, WIDTH - 32, 2, 5),
        24 + ping_pong(frame, HEIGHT - 36, 3, 7),
        TRIANGLE,
        RED,
        False,
    )
    draw_poly(
        surface,
        32 + ping_pong(frame, WIDTH - 44, 2, 13),
        30 + ping_pong(frame, HEIGHT - 34, 2, 17),
        DIAMOND,
        GREEN,
        True,
    )
    draw_poly(
        surface,
        20 + ping_pong(frame, WIDTH - 34, 4, 19),
        42 + ping_pong(frame, HEIGHT - 26, 1, 23),
        CHEVRON,
        MAGENTA,
        False,
    )

    sprite_x = ping_pong(frame, WIDTH - 12, 4, 7)
    sprite_y = HEADER_HEIGHT + 2 + ping_pong(frame, HEIGHT - HEADER_HEIGHT - 14, 3, 9)
    surface.blit(sprite, sprite_x, sprite_y, sprite_key)
    surface.text("BLIT", 2, HEIGHT - 8, CYAN)


def draw_grid_phase(surface, frame, fps):
    surface.fill(BLACK)
    draw_header(surface, "GRID", fps)

    cell_w = WIDTH // 2
    cell_h = (HEIGHT - HEADER_HEIGHT) // 3
    highlight = (frame >> 4) % len(COLOR_SWATCHES)

    for index, (label, color, text_color) in enumerate(COLOR_SWATCHES):
        col = index % 2
        row = index // 2
        x = col * cell_w
        y = HEADER_HEIGHT + row * cell_h
        w = cell_w if col == 0 else WIDTH - x
        h = cell_h if row < 2 else HEIGHT - y
        border = WHITE if index == highlight else INK

        surface.fill_rect(x, y, w, h, color)
        surface.rect(x, y, w, h, border)
        surface.text(label, x + ((w - 8) >> 1), y + ((h - 8) >> 1), text_color)

    scan_x = ping_pong(frame, WIDTH - 1, 1)
    surface.vline(scan_x, HEADER_HEIGHT, HEIGHT - HEADER_HEIGHT, WHITE)


def main():
    display = Hub75FrameBuffer(width=WIDTH, height=HEIGHT)
    surface = display.framebuf
    sprite, sprite_key = make_sprite()

    frame = 0
    fps = 0
    last_phase = -1
    fps_start = time.ticks_ms()
    fps_frame_mark = 0

    try:
        while True:
            phase = (frame // 180) % 3
            if phase != last_phase:
                surface.fill(BLACK)
                last_phase = phase

            if phase == 0:
                draw_scroll_phase(surface, frame, fps)
            elif phase == 1:
                draw_shapes_phase(surface, frame, fps, sprite, sprite_key)
            else:
                draw_grid_phase(surface, frame, fps)

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


main()