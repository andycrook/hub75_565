# pyright: reportMissingImports=false, reportAttributeAccessIssue=false

import math
import time

from h3d import (
    MODE_POINTS,
    MODE_SOLID,
    MODE_SOLID_WIREFRAME,
    MODE_TEXTURED,
    MODE_WIREFRAME,
    Instance3D,
    Mesh3D,
    Texture3D,
    quat_compose,
    quat_from_axis_angle,
    render_scene,
)
from hub75_565 import Hub75FrameBuffer, rgb565

#MESH_PATH = "/3D/recognizer.h3dm"
#TEXTURE_PATH = "/3D/recognizer.h3dt"
#MESH_PATH = "/3D/cube.h3dm"
#TEXTURE_PATH = "/3D/cube.h3dt"
MESH_PATH = "/3D/COBRA.h3dm"
TEXTURE_PATH = "/3D/COBRA.h3dt"
BACKDROP_PATH = "/rgb565/b5_128_CORRECT.rgb565"
#BACKDROP_PATH = "/rgb565/picard.rgb565"

AMBIENT = 0
AMBIENT_COLOR = (255, 255, 255)
KEY_LIGHT_STRENGTH = 2.0    # None
KEY_LIGHT_COLOR = (255,255,200)
FILL_LIGHT_DIRECTION = None#(-1,0,0)   # None
FILL_LIGHT_STRENGTH = 0.2
FILL_LIGHT_COLOR = (255, 0, 255)

BLACK = 0
NAVY = rgb565(8, 18, 40)
WHITE = rgb565(255, 255, 255)
YELLOW = rgb565(255, 255, 0)
CYAN = rgb565(0, 255, 255)
GREEN = rgb565(0, 255, 0)
RED = rgb565(255,0,0)

MODE_LABELS = (
   # (MODE_POINTS, "points"),
  #  (MODE_WIREFRAME, "wire"),
    (MODE_SOLID, "solid"),
  #  (MODE_SOLID_WIREFRAME, "solid+wire"),
    (MODE_TEXTURED, "tex"),
)

_TIME_TICKS_MS = getattr(time, "ticks_ms", None)
_TIME_TICKS_DIFF = getattr(time, "ticks_diff", None)


def _ticks_ms():
    if _TIME_TICKS_MS is not None:
        return int(_TIME_TICKS_MS())
    return int(time.monotonic() * 1000)


def _ticks_diff(now_ms, last_ms):
    if _TIME_TICKS_DIFF is not None:
        return int(_TIME_TICKS_DIFF(now_ms, last_ms))
    return int(now_ms - last_ms)


def draw_status(display, mode_label, fps, visible_faces):
    surface = display.framebuf
    surface.fill_rect(0, 0, display.width, 10, NAVY)
    surface.text(mode_label, 2, 1, WHITE)
    surface.text(str(int(fps)), 74, 1, YELLOW)
    surface.text("fps", 102, 1, WHITE)
    #surface.text(str(int(visible_faces)), 88, 1, CYAN)


def update_instances(instances, frame):
    base_angle = frame * 1.8
    for index, instance in enumerate(instances):
        phase_offset = index * 115.0
        yaw = quat_from_axis_angle((0, 1, 0), base_angle + phase_offset)
        pitch = quat_from_axis_angle((1, 0, 0), base_angle * 0.7 + phase_offset * 0.5)
        roll = quat_from_axis_angle((0, 0, 1), base_angle * 0.45 + phase_offset * 0.25)
        instance.quaternion = quat_compose(yaw, pitch, roll)


def main():
    display = Hub75FrameBuffer(width=128, height=64)
    try:
        display.load_rgb565(BACKDROP_PATH)
        backdrop = bytearray(display.rgb565_buffer)
        mesh = Mesh3D.load(MESH_PATH)
        texture = Texture3D.load(TEXTURE_PATH)
    except OSError:
        display.deinit()
        raise RuntimeError(
            "Missing /bin/b5_128_CORRECT.rgb565, /3d/demo_cube.h3dm, or /3d/demo_cube.h3dt."
        )
    except ValueError as exc:
        display.deinit()
        raise RuntimeError("Backdrop load failed: %s" % exc)

    instances = [
        Instance3D(mesh, texture=texture, position=(-166, 0, 420), scale=124, mode=MODE_TEXTURED),
        Instance3D(mesh, texture=texture, position=(0, -10, 320), scale=250, mode=MODE_TEXTURED),
        Instance3D(mesh, texture=texture, position=(166, 0, 420), scale=124, mode=MODE_TEXTURED),
    ]

    frame = 0
    fps = 0
    fps_mark = 0
    fps_start = _ticks_ms()

    try:
        while True:
            mode_value, mode_label = MODE_LABELS[(frame // 360) % len(MODE_LABELS)]
            for instance in instances:
                instance.mode = mode_value
                instance.color = WHITE
                instance.wire_color = RED
            display.rgb565_buffer[:] = backdrop
            update_instances(instances, frame)

            light_angle = math.radians(2.0)
            light_direction = (math.cos(light_angle) * 0.8, -0.35, -1.0)

            visible_faces = render_scene(
                display,
                instances,
                clear_color=None,
                light_direction=light_direction,
                ambient=AMBIENT,
                focal_length=96,
                near_plane=24,
                point_size=1,
                sort_instances=False,
                light_color=KEY_LIGHT_COLOR,
                light_strength=KEY_LIGHT_STRENGTH,
                ambient_color=AMBIENT_COLOR,
                fill_light_direction=FILL_LIGHT_DIRECTION,
                fill_light_color=FILL_LIGHT_COLOR,
                fill_light_strength=FILL_LIGHT_STRENGTH,
            )

            draw_status(display, mode_label, fps, visible_faces)
            display.show()
            frame += 1

            now = _ticks_ms()
            if _ticks_diff(now, fps_start) >= 1000:
                fps = frame - fps_mark
                fps_mark = frame
                fps_start = now
    except KeyboardInterrupt:
        pass
    finally:
        display.deinit()


main()
