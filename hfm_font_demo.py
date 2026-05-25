# pyright: reportMissingImports=false, reportAttributeAccessIssue=false

import time

from hub75_565 import Hub75FrameBuffer, rgb565


BLACK = 0
NAVY = rgb565(8, 18, 40)
INK = rgb565(8, 12, 18)
RED = rgb565(255, 0, 0)
GREEN = rgb565(0, 255, 0)
BLUE = rgb565(0, 0, 255)
CYAN = rgb565(0, 255, 255)
MAGENTA = rgb565(255, 0, 255)
YELLOW = rgb565(255, 255, 0)
WHITE = rgb565(255, 255, 255)
ORANGE = rgb565(255, 160, 24)


def draw_banner(display, font, text, y, fg, bg=None):
    display.draw_text(
        0,
        y,
        text,
        fg,
        bg=bg,
        font=font,
        align="center",
        box_width=display.width,
    )


def scene_frontier(display, fonts):
    frontier_16 = fonts["frontier_16"]
    frontier_10 = fonts["frontier_10"]

    display.fill(BLACK)
    draw_banner(display, frontier_10, "HFM FRAMEBUF TEXT", 2, WHITE, bg=rgb565(12, 36, 64))
    display.draw_text(4, 16, "Frontier", YELLOW, font=frontier_16)
    display.draw_text(6, 34, "x2", RED, font=frontier_10, scale=2)
    display.draw_text(52, 34, "RGB565", GREEN, font=frontier_10, scale=2)


def scene_transform(display, fonts):
    tos = fonts["tos"]
    spleen = fonts["spleen"]

    display.fill(BLACK)
    draw_banner(display, spleen, "ROTATE / FLIP", 2, CYAN)
    display.draw_text(4, 16, "0", WHITE, font=tos)
    display.draw_text(28, 14, "90", RED, font=tos, rotation=90)
    display.draw_text(68, 28, "180", GREEN, font=tos, rotation=180)
    display.draw_text(104, 14, "270", BLUE, font=tos, rotation=270)
    display.draw_text(6, 48, "flip_h", MAGENTA, font=spleen, flip_h=True)
    display.draw_text(68, 48, "flip_v", YELLOW, font=spleen, flip_v=True)


def scene_layout(display, fonts):
    tos = fonts["tos"]
    frontier_10 = fonts["frontier_10"]

    display.fill(BLACK)
    draw_banner(display, frontier_10, "JUSTIFY / BG", 2, WHITE, bg=rgb565(0,0,0))
    display.draw_text(0, 14, "LEFT", RED, bg=rgb565(32, 0, 0), font=tos, align="left", box_width=display.width)
    display.draw_text(0, 28, "CENTER", GREEN, bg=rgb565(0, 32, 0), font=tos, align="center", box_width=display.width)
    display.draw_text(0, 42, "RIGHT", BLUE, bg=rgb565(0, 0, 32), font=tos, align="right", box_width=display.width)


def scene_scale(display, fonts):
    tos = fonts["tos"]
    frontier_10 = fonts["frontier_10"]

    display.fill(rgb565(0,0,0))
    draw_banner(display, frontier_10, "INTEGER SCALE", 2, ORANGE)
    display.draw_text(4, 16, "scale 1", CYAN, font=tos, scale=1)
    display.draw_text(4, 28, "scale 2", MAGENTA, font=tos, scale=2)
    display.draw_text(76, 16, "x3", YELLOW, font=frontier_10, scale=3)


def main():
    display = Hub75FrameBuffer(width=128, height=64)
    fonts = {
        "frontier_10": display.font_load("/fonts/Frontier-10-r.hfm"),
        "frontier_16": display.font_load("/fonts/Frontier-16-r.hfm", set_default=False),
        "tos": display.font_load("/fonts/TOS.hfm", set_default=False),
        "spleen": display.font_load("/fonts/spleen.hfm", set_default=False),
    }

    display.marquee_configure(
        "custom HFM fonts  |  rgb565 framebuf  |  native rotation flip scale justify marquee",
        x=0,
        y=53,
        width=display.width,
        speed=30.0,
        speed_mode="time",
        gap=50,
        fg=YELLOW,
        bg=rgb565(0, 0, 24),
        font=fonts["frontier_10"],
    )

    scenes = (
        scene_frontier,
        scene_transform,
        scene_layout,
        scene_scale,
    )

    frame = 0
    try:
        while True:
            scene = scenes[(frame // 360) % len(scenes)]
            scene(display, fonts)
            display.marquee_step(draw=True)
            display.show()
            frame += 1
            
    except KeyboardInterrupt:
        pass
    finally:
        display.deinit()


main()