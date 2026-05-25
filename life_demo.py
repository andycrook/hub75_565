import time

try:
    from random import getrandbits
except ImportError:
    from urandom import getrandbits  # type: ignore

from hub75_565 import Hub75FrameBuffer
from life_world import LifeWorld


PANEL_WIDTH = 128
PANEL_HEIGHT = 64

WORLD_WIDTH = 128
WORLD_HEIGHT = 64
WRAP = True
USE_NATIVE_LIFE_SIM = True
USE_NATIVE_HELPERS = False # Don't set to True
USE_NATIVE_STEP = True
USE_NATIVE_RENDER = True
NATIVE_STEP_ROWS = 8
NATIVE_STEP_YIELD_MS = 1
NATIVE_HELPER_YIELD_MS = 1

SEED_MODE = "showcase"  # random | pattern | file | showcase
PATTERN_NAME = "glider_swarm"
SEED_FILE = "/seeds/demo.lifebin"
RANDOM_DENSITY = 0.24
SHOWCASE_SEQUENCE = (
    "random",
    "rpento",
    "acorn",
    "glider_swarm",
    "pulsar",
    "gosper_gun",
)
SHOWCASE_RANDOM_DENSITY_MIN = 0.18
SHOWCASE_RANDOM_DENSITY_MAX = 0.34

ALIVE_COLOR = (100,255,100)
DEAD_COLOR = (0, 0, 0)
COLOR_CYCLE_STEP = 0

TARGET_FPS = 20
SIM_STEPS_PER_FRAME = 1

VIEW_X = 0
VIEW_Y = 0
CENTER_SMALL_WORLD = True
AUTO_PAN = True
AUTO_PAN_STEP_X = 1
AUTO_PAN_STEP_Y = 1

LOOP_HISTORY = 48
LOOP_REPEAT_TRIGGER = 2
PERTURB_FLIPS = 8
LOW_POPULATION_RESEED = 18
HIGH_POPULATION_RESEED_RATIO = 0.92
STAGNANT_LIMIT = 36

STATUS_PRINT_INTERVAL_MS = 1000


_SLEEP_MS = getattr(time, "sleep_ms", None)
_TICKS_MS = getattr(time, "ticks_ms", None)
_TICKS_ADD = getattr(time, "ticks_add", None)
_TICKS_DIFF = getattr(time, "ticks_diff", None)


def sleep_ms(value):
    if _SLEEP_MS is not None:
        _SLEEP_MS(value)
    else:
        time.sleep(value / 1000.0)


def ticks_ms():
    if _TICKS_MS is not None:
        return int(_TICKS_MS())
    return int(time.time() * 1000)


def ticks_add(value, delta):
    if _TICKS_ADD is not None:
        return int(_TICKS_ADD(value, delta))
    return int(value + delta)


def ticks_diff(left, right):
    if _TICKS_DIFF is not None:
        return int(_TICKS_DIFF(left, right))
    return int(left - right)


def next_color_hsv(color, step=1):
    red, green, blue = color
    red = red / 255.0
    green = green / 255.0
    blue = blue / 255.0

    maximum = max(red, green, blue)
    minimum = min(red, green, blue)
    diff = maximum - minimum

    if diff == 0:
        hue = 0.0
    elif maximum == red:
        hue = (60.0 * ((green - blue) / diff) + 360.0) % 360.0
    elif maximum == green:
        hue = (60.0 * ((blue - red) / diff) + 120.0) % 360.0
    else:
        hue = (60.0 * ((red - green) / diff) + 240.0) % 360.0

    saturation = 0.0 if maximum == 0 else diff / maximum
    value = maximum

    hue = (hue + float(step)) % 360.0
    chroma = value * saturation
    x_val = chroma * (1.0 - abs((hue / 60.0) % 2.0 - 1.0))
    match = value - chroma

    if hue < 60.0:
        red_prime, green_prime, blue_prime = chroma, x_val, 0.0
    elif hue < 120.0:
        red_prime, green_prime, blue_prime = x_val, chroma, 0.0
    elif hue < 180.0:
        red_prime, green_prime, blue_prime = 0.0, chroma, x_val
    elif hue < 240.0:
        red_prime, green_prime, blue_prime = 0.0, x_val, chroma
    elif hue < 300.0:
        red_prime, green_prime, blue_prime = x_val, 0.0, chroma
    else:
        red_prime, green_prime, blue_prime = chroma, 0.0, x_val

    return (
        int((red_prime + match) * 255.0),
        int((green_prime + match) * 255.0),
        int((blue_prime + match) * 255.0),
    )


def random_density(minimum, maximum):
    if maximum <= minimum:
        return minimum
    return minimum + (getrandbits(10) / 1023.0) * (maximum - minimum)


def initial_seed_seed():
    return ((ticks_ms() * 1664525) ^ 0xC0DEC0DE) & 0xFFFFFFFF


def seed_world(world, cycle_index):
    random_seed = initial_seed_seed() + cycle_index * 977

    if SEED_MODE == "random":
        world.seed_random(density=RANDOM_DENSITY, seed=random_seed)
        return "random"

    if SEED_MODE == "pattern":
        world.seed_pattern(PATTERN_NAME)
        return PATTERN_NAME

    if SEED_MODE == "file":
        world.seed_file(SEED_FILE)
        return "file"

    choice = SHOWCASE_SEQUENCE[cycle_index % len(SHOWCASE_SEQUENCE)]
    if choice == "random":
        density = random_density(SHOWCASE_RANDOM_DENSITY_MIN, SHOWCASE_RANDOM_DENSITY_MAX)
        world.seed_random(density=density, seed=random_seed)
        return "random %.2f" % density

    if choice.startswith("file:"):
        world.seed_file(choice[5:])
        return "file"

    world.seed_pattern(choice)
    return choice


def update_view(world, display, view_x, view_y, dir_x, dir_y):
    max_x = max(0, world.width - display.width)
    max_y = max(0, world.height - display.height)
    if max_x <= 0:
        view_x = 0
    else:
        view_x += dir_x * AUTO_PAN_STEP_X
        if view_x <= 0:
            view_x = 0
            dir_x = 1
        elif view_x >= max_x:
            view_x = max_x
            dir_x = -1

    if max_y <= 0:
        view_y = 0
    else:
        view_y += dir_y * AUTO_PAN_STEP_Y
        if view_y <= 0:
            view_y = 0
            dir_y = 1
        elif view_y >= max_y:
            view_y = max_y
            dir_y = -1

    return view_x, view_y, dir_x, dir_y


def main():
    display = Hub75FrameBuffer(width=PANEL_WIDTH, height=PANEL_HEIGHT)
    world = LifeWorld(
        WORLD_WIDTH,
        WORLD_HEIGHT,
        wrap=WRAP,
        use_native=USE_NATIVE_LIFE_SIM,
        use_native_helpers=USE_NATIVE_HELPERS,
        use_native_step=USE_NATIVE_STEP,
        native_step_rows=NATIVE_STEP_ROWS,
        native_step_yield_ms=NATIVE_STEP_YIELD_MS,
        native_helper_yield_ms=NATIVE_HELPER_YIELD_MS,
        use_native_render=USE_NATIVE_RENDER,
    )

    cycle_index = 0
    seed_label = seed_world(world, cycle_index)
    cycle_index += 1

    recent_signatures = []
    repeat_hits = 0
    stagnant_steps = 0
    previous_alive = world.last_alive
    alive_color = ALIVE_COLOR

    view_x = VIEW_X
    view_y = VIEW_Y
    dir_x = 1
    dir_y = 1

    frame = 0
    fps_counter = 0
    status_alive = world.last_alive
    status_hash = world.last_hash
    frame_interval_ms = max(1, int(round(1000.0 / max(1, TARGET_FPS))))
    next_frame_tick = ticks_ms()
    status_start = next_frame_tick

    try:
        while True:
            for _ in range(max(1, int(SIM_STEPS_PER_FRAME))):
                status_alive = world.step()
                status_hash = world.last_hash
                signature = (status_hash, status_alive)
                if signature in recent_signatures:
                    repeat_hits += 1
                else:
                    repeat_hits = 0
                recent_signatures.append(signature)
                if len(recent_signatures) > LOOP_HISTORY:
                    recent_signatures.pop(0)

                if status_alive == previous_alive:
                    stagnant_steps += 1
                else:
                    stagnant_steps = 0
                previous_alive = status_alive

                if SEED_MODE == "showcase":
                    reseed_population = status_alive <= LOW_POPULATION_RESEED or status_alive >= int(world.size * HIGH_POPULATION_RESEED_RATIO)
                    if reseed_population:
                        seed_label = seed_world(world, cycle_index)
                        cycle_index += 1
                        recent_signatures = []
                        repeat_hits = 0
                        stagnant_steps = 0
                        status_alive = world.last_alive
                        status_hash = world.last_hash
                        previous_alive = status_alive
                        continue

                    if repeat_hits >= LOOP_REPEAT_TRIGGER or stagnant_steps >= STAGNANT_LIMIT:
                        world.perturb(PERTURB_FLIPS)
                        status_alive = world.last_alive
                        status_hash = world.last_hash
                        previous_alive = status_alive
                        recent_signatures = [(status_hash, status_alive)]
                        repeat_hits = 0
                        stagnant_steps = 0

            if AUTO_PAN:
                view_x, view_y, dir_x, dir_y = update_view(world, display, view_x, view_y, dir_x, dir_y)

            world.render(
                display,
                alive_color,
                dead_color=DEAD_COLOR,
                view_x=view_x,
                view_y=view_y,
                center_small=CENTER_SMALL_WORLD,
            )
            display.show()

            if COLOR_CYCLE_STEP:
                alive_color = next_color_hsv(alive_color, COLOR_CYCLE_STEP)

            frame += 1
            fps_counter += 1
            now = ticks_ms()
            if ticks_diff(now, status_start) >= STATUS_PRINT_INTERVAL_MS:
#                 print(
#                     "life",
#                     fps_counter,
#                     "fps",
#                     "alive",
#                     status_alive,
#                     "gen",
#                     world.generation,
#                     "seed",
#                     seed_label,
#                     "view",
#                     view_x,
#                     view_y,
#                     "hash",
#                     status_hash,
#                 )
                fps_counter = 0
                status_start = now

            next_frame_tick = ticks_add(next_frame_tick, frame_interval_ms)
            delay = ticks_diff(next_frame_tick, ticks_ms())
            if delay > 0:
                sleep_ms(delay)
            elif ticks_diff(ticks_ms(), next_frame_tick) >= frame_interval_ms:
                next_frame_tick = ticks_add(ticks_ms(), frame_interval_ms)
    except KeyboardInterrupt:
        print("Stopping Life framebuffer demo")
    finally:
        display.deinit()


if __name__ == "__main__":
    main()