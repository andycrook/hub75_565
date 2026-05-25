try:
    import life_sim as _life_sim
except ImportError:
    _life_sim = None

try:
    from random import getrandbits
except ImportError:
    from urandom import getrandbits

try:
    import time
    _ticks_ms = time.ticks_ms
except (AttributeError, ImportError):
    import time

    def _ticks_ms():
        return int(time.time() * 1000)


_sleep_ms = getattr(time, "sleep_ms", None)


LIFE_FILE_MAGIC = b"LIFE"
LIFE_FILE_VERSION = 1
LIFE_FILE_HEADER_SIZE = 10


def rgb565(red, green, blue):
    return ((int(red) & 0xF8) << 8) | ((int(green) & 0xFC) << 3) | (int(blue) >> 3)


def _to_rgb565(color):
    if isinstance(color, int):
        return int(color) & 0xFFFF
    if isinstance(color, tuple) or isinstance(color, list):
        return rgb565(color[0], color[1], color[2])
    raise TypeError("color must be an RGB565 int or an (r, g, b) tuple")


def _default_seed():
    ticks = int(_ticks_ms()) & 0xFFFFFFFF
    return ((ticks * 1103515245) ^ (ticks >> 7) ^ 0xA5A5F17C) & 0xFFFFFFFF


def _signed_u32(value):
    value = int(value) & 0xFFFFFFFF
    if value >= 0x80000000:
        value -= 0x100000000
    return value


def _yield_runtime(delay_ms=0):
    delay_val = max(0, int(delay_ms))
    if _sleep_ms is not None:
        _sleep_ms(delay_val)
    else:
        time.sleep(delay_val / 1000.0)


PATTERN_RANDOM = getattr(_life_sim, "PATTERN_RANDOM", 0)
PATTERN_GLIDER = getattr(_life_sim, "PATTERN_GLIDER", 1)
PATTERN_GOSPER_GUN = getattr(_life_sim, "PATTERN_GOSPER_GUN", 2)
PATTERN_PULSAR = getattr(_life_sim, "PATTERN_PULSAR", 3)
PATTERN_RPENTO = getattr(_life_sim, "PATTERN_RPENTO", 4)
PATTERN_ACORN = getattr(_life_sim, "PATTERN_ACORN", 5)
PATTERN_GLIDER_SWARM = getattr(_life_sim, "PATTERN_GLIDER_SWARM", 6)

PATTERN_DEFINITIONS = {
    "glider": {
        "id": PATTERN_GLIDER,
        "size": (3, 3),
        "cells": ((1, 0), (2, 1), (0, 2), (1, 2), (2, 2)),
    },
    "gosper_gun": {
        "id": PATTERN_GOSPER_GUN,
        "size": (36, 9),
        "cells": (
            (0, 4), (0, 5), (1, 4), (1, 5),
            (10, 4), (10, 5), (10, 6),
            (11, 3), (11, 7),
            (12, 2), (12, 8), (13, 2), (13, 8),
            (14, 5),
            (15, 3), (15, 7),
            (16, 4), (16, 5), (16, 6),
            (17, 5),
            (20, 2), (20, 3), (20, 4),
            (21, 2), (21, 3), (21, 4),
            (22, 1), (22, 5),
            (24, 0), (24, 1), (24, 5), (24, 6),
            (34, 2), (34, 3), (35, 2), (35, 3),
        ),
    },
    "pulsar": {
        "id": PATTERN_PULSAR,
        "size": (13, 13),
        "cells": (
            (-6, -4), (-5, -4), (-4, -4), (-1, -4), (0, -4), (1, -4),
            (-6, -2), (-5, -2), (-4, -2), (-1, -2), (0, -2), (1, -2),
            (-6, 4), (-5, 4), (-4, 4), (-1, 4), (0, 4), (1, 4),
            (-6, 2), (-5, 2), (-4, 2), (-1, 2), (0, 2), (1, 2),
            (-4, -6), (-2, -6), (-1, -6), (0, -6), (1, -6), (3, -6),
            (-4, -5), (-2, -5), (-1, -5), (0, -5), (1, -5), (3, -5),
            (-4, 6), (-2, 6), (-1, 6), (0, 6), (1, 6), (3, 6),
            (-4, 5), (-2, 5), (-1, 5), (0, 5), (1, 5), (3, 5),
        ),
    },
    "rpento": {
        "id": PATTERN_RPENTO,
        "size": (3, 3),
        "cells": ((1, 0), (2, 0), (0, 1), (1, 1), (1, 2)),
    },
    "acorn": {
        "id": PATTERN_ACORN,
        "size": (7, 3),
        "cells": ((1, 0), (3, 1), (0, 2), (1, 2), (4, 2), (5, 2), (6, 2)),
    },
    "glider_swarm": {
        "id": PATTERN_GLIDER_SWARM,
        "size": (0, 0),
        "cells": (),
    },
    "random": {
        "id": PATTERN_RANDOM,
        "size": (0, 0),
        "cells": (),
    },
}

PATTERN_NAMES = tuple(PATTERN_DEFINITIONS.keys())
PATTERN_NAME_TO_ID = {name: definition["id"] for name, definition in PATTERN_DEFINITIONS.items()}
PATTERN_ID_TO_NAME = {definition["id"]: name for name, definition in PATTERN_DEFINITIONS.items()}


def pack_world_bits(state, width, height):
    width_val = int(width)
    height_val = int(height)
    total = width_val * height_val
    out = bytearray((total + 7) >> 3)
    for index in range(total):
        if state[index]:
            out[index >> 3] |= 1 << (index & 7)
    return bytes(out)


def unpack_world_bits(payload, width, height):
    width_val = int(width)
    height_val = int(height)
    total = width_val * height_val
    expected = (total + 7) >> 3
    if len(payload) < expected:
        raise ValueError("packed payload truncated")
    out = bytearray(total)
    for index in range(total):
        out[index] = (payload[index >> 3] >> (index & 7)) & 1
    return out


def read_life_seed_file(path):
    with open(path, "rb") as handle:
        header = handle.read(LIFE_FILE_HEADER_SIZE)
        if len(header) != LIFE_FILE_HEADER_SIZE:
            raise ValueError("life seed header truncated")
        if header[0:4] != LIFE_FILE_MAGIC:
            raise ValueError("life seed magic mismatch")
        version = header[4]
        if version != LIFE_FILE_VERSION:
            raise ValueError("unsupported life seed version")
        width = header[5] | (header[6] << 8)
        height = header[7] | (header[8] << 8)
        flags = header[9]
        if width <= 0 or height <= 0:
            raise ValueError("invalid life seed dimensions")
        payload = handle.read()

    expected = ((width * height) + 7) >> 3
    if len(payload) != expected:
        raise ValueError("life seed payload size mismatch")
    return width, height, flags, payload


def write_life_seed_file(path, state, width, height, flags=0):
    header = bytearray(LIFE_FILE_HEADER_SIZE)
    header[0:4] = LIFE_FILE_MAGIC
    header[4] = LIFE_FILE_VERSION
    header[5] = int(width) & 0xFF
    header[6] = (int(width) >> 8) & 0xFF
    header[7] = int(height) & 0xFF
    header[8] = (int(height) >> 8) & 0xFF
    header[9] = int(flags) & 0xFF

    payload = pack_world_bits(state, width, height)
    with open(path, "wb") as handle:
        handle.write(header)
        handle.write(payload)
    return len(payload)


def parse_ascii_grid(text):
    alive_tokens = set(("O", "o", "1", "X", "x", "#", "*", "@"))
    rows = []
    for raw_line in str(text).splitlines():
        line = raw_line.rstrip("\n\r")
        if not line:
            continue
        rows.append(line)
    if not rows:
        raise ValueError("no grid rows found")

    width = max(len(row) for row in rows)
    height = len(rows)
    state = bytearray(width * height)
    for y, row in enumerate(rows):
        for x, token in enumerate(row):
            if token in alive_tokens:
                state[y * width + x] = 1
    return width, height, state


def _hash_state_py(state):
    hash_value = 2166136261
    alive_total = 0
    for value in state:
        alive_total += 1 if value else 0
        hash_value ^= int(value) & 0xFF
        hash_value = (hash_value * 16777619) & 0xFFFFFFFF
    return alive_total, hash_value


def _pattern_name_to_id(pattern):
    if isinstance(pattern, int):
        return int(pattern)
    key = str(pattern).strip().lower()
    if key not in PATTERN_NAME_TO_ID:
        raise ValueError("unknown life pattern: %s" % pattern)
    return PATTERN_NAME_TO_ID[key]


def _pattern_definition(pattern_id):
    name = PATTERN_ID_TO_NAME.get(int(pattern_id))
    if name is None:
        raise ValueError("unknown life pattern id: %s" % pattern_id)
    return PATTERN_DEFINITIONS[name]


def _wrap_coord(value, limit):
    if limit <= 0:
        return 0
    while value < 0:
        value += limit
    while value >= limit:
        value -= limit
    return value


class LifeWorld:
    def __init__(
        self,
        width,
        height,
        wrap=True,
        use_native=True,
        use_native_helpers=None,
        use_native_step=None,
        native_step_rows=0,
        native_step_yield_ms=0,
        native_helper_yield_ms=0,
        use_native_render=None,
    ):
        self.width = int(width)
        self.height = int(height)
        if self.width <= 0 or self.height <= 0:
            raise ValueError("world width and height must be > 0")

        self.wrap = bool(wrap)
        self.use_native = bool(use_native)
        self.use_native_helpers = self.use_native if use_native_helpers is None else bool(use_native_helpers)
        self.use_native_step = self.use_native if use_native_step is None else bool(use_native_step)
        self.native_step_rows = max(0, int(native_step_rows))
        self.native_step_yield_ms = max(0, int(native_step_yield_ms))
        self.native_helper_yield_ms = max(0, int(native_helper_yield_ms))
        self.use_native_render = self.use_native if use_native_render is None else bool(use_native_render)
        self.size = self.width * self.height
        self.state = bytearray(self.size)
        self.next_state = bytearray(self.size)
        self._state_mv = memoryview(self.state)
        self._next_mv = memoryview(self.next_state)
        self._prev_row, self._next_row = self._neighbor_indices(self.height, self.wrap)
        self._prev_col, self._next_col = self._neighbor_indices(self.width, self.wrap)
        self.generation = 0
        self.last_alive = 0
        self.last_hash = 0

    def _has_native(self, name):
        return self.use_native_helpers and _life_sim is not None and hasattr(_life_sim, name)

    def _has_native_step(self, name):
        return self.use_native_step and _life_sim is not None and hasattr(_life_sim, name)

    def _has_native_render(self, name):
        return self.use_native_render and _life_sim is not None and hasattr(_life_sim, name)

    def _yield_after_native_helper(self):
        if self.native_helper_yield_ms > 0:
            _yield_runtime(self.native_helper_yield_ms)

    @staticmethod
    def _neighbor_indices(count, wrap):
        prev_vals = [0] * count
        next_vals = [0] * count
        last = count - 1
        for index in range(count):
            if wrap:
                prev_vals[index] = (index - 1) % count
                next_vals[index] = (index + 1) % count
            else:
                prev_vals[index] = index - 1 if index > 0 else 0
                next_vals[index] = index + 1 if index < last else last
        return prev_vals, next_vals

    def _clear_buffer(self, buffer_obj):
        if self._has_native("clear"):
            _life_sim.clear(memoryview(buffer_obj), self.width, self.height)
            self._yield_after_native_helper()
            return
        for index in range(self.size):
            buffer_obj[index] = 0

    def _reset_next(self):
        self._clear_buffer(self.next_state)

    def _swap_buffers(self):
        self.state, self.next_state = self.next_state, self.state
        self._state_mv, self._next_mv = self._next_mv, self._state_mv

    def clear(self):
        self._clear_buffer(self.state)
        self._reset_next()
        self.generation = 0
        self.last_alive = 0
        self.last_hash = 0

    def analyze(self):
        if self._has_native("analyze"):
            alive_total, hash_value = _life_sim.analyze(self._state_mv, self.width, self.height)
            self._yield_after_native_helper()
        else:
            alive_total, hash_value = _hash_state_py(self.state)
        self.last_alive = int(alive_total)
        self.last_hash = int(hash_value) & 0xFFFFFFFF
        return self.last_alive, self.last_hash

    def seed_random(self, density=0.30, seed=None):
        density_value = float(density)
        if density_value < 0.0:
            density_value = 0.0
        elif density_value > 1.0:
            density_value = 1.0
        density_permille = int(round(density_value * 1000.0))
        seed_value = _default_seed() if seed is None else int(seed) & 0xFFFFFFFF

        if self._has_native("seed_random"):
            _life_sim.seed_random(self._state_mv, self.width, self.height, seed_value, density_permille)
            self._yield_after_native_helper()
        else:
            self._clear_buffer(self.state)
            threshold = int(density_value * 1024.0)
            for index in range(self.size):
                self.state[index] = 1 if getrandbits(10) < threshold else 0

        self._reset_next()
        self.generation = 0
        return self.analyze()[0]

    def seed_pattern(self, pattern, origin_x=-1, origin_y=-1, clear=True):
        pattern_id = _pattern_name_to_id(pattern)
        if pattern_id == PATTERN_RANDOM:
            return self.seed_random()

        if clear:
            self.clear()
        else:
            self._reset_next()

        if self._has_native("seed_pattern"):
            _life_sim.seed_pattern(
                self._state_mv,
                self.width,
                self.height,
                pattern_id,
                int(origin_x),
                int(origin_y),
                self.wrap,
            )
            self._yield_after_native_helper()
        else:
            self._seed_pattern_py(pattern_id, int(origin_x), int(origin_y))

        self.generation = 0
        return self.analyze()[0]

    def _seed_pattern_py(self, pattern_id, origin_x, origin_y):
        if pattern_id == PATTERN_GLIDER_SWARM:
            step_x = 16 if self.width >= 32 else max(1, self.width // 2)
            step_y = 16 if self.height >= 32 else max(1, self.height // 2)
            glider_id = PATTERN_GLIDER
            for y in range(2, self.height, step_y):
                for x in range(2, self.width, step_x):
                    self._seed_pattern_py(glider_id, x, y)
            return

        definition = _pattern_definition(pattern_id)
        if origin_x < 0:
            origin_x = (self.width - definition["size"][0]) // 2
        if origin_y < 0:
            origin_y = (self.height - definition["size"][1]) // 2

        for offset_x, offset_y in definition["cells"]:
            x = origin_x + offset_x
            y = origin_y + offset_y
            if self.wrap:
                x = _wrap_coord(x, self.width)
                y = _wrap_coord(y, self.height)
            elif x < 0 or x >= self.width or y < 0 or y >= self.height:
                continue
            self.state[y * self.width + x] = 1

    def seed_bits(self, payload, source_width, source_height, origin_x=-1, origin_y=-1, clear=True):
        if self._has_native("load_bits"):
            _life_sim.load_bits(
                self._state_mv,
                self.width,
                self.height,
                payload,
                int(source_width),
                int(source_height),
                int(origin_x),
                int(origin_y),
                bool(clear),
                self.wrap,
            )
            self._yield_after_native_helper()
        else:
            if clear:
                self._clear_buffer(self.state)
            source_state = unpack_world_bits(payload, source_width, source_height)
            if origin_x < 0:
                origin_x = (self.width - int(source_width)) // 2
            if origin_y < 0:
                origin_y = (self.height - int(source_height)) // 2
            for index, alive in enumerate(source_state):
                if not alive:
                    continue
                x = origin_x + (index % int(source_width))
                y = origin_y + (index // int(source_width))
                if self.wrap:
                    x = _wrap_coord(x, self.width)
                    y = _wrap_coord(y, self.height)
                elif x < 0 or x >= self.width or y < 0 or y >= self.height:
                    continue
                self.state[y * self.width + x] = 1

        self._reset_next()
        self.generation = 0
        return self.analyze()[0]

    def seed_file(self, path, origin_x=-1, origin_y=-1, clear=True):
        source_width, source_height, _flags, payload = read_life_seed_file(path)
        return self.seed_bits(payload, source_width, source_height, origin_x=origin_x, origin_y=origin_y, clear=clear)

    def write_seed_file(self, path, flags=0):
        return write_life_seed_file(path, self.state, self.width, self.height, flags=flags)

    def perturb(self, flip_count=4, seed=None):
        flips = max(0, int(flip_count))
        seed_value = _default_seed() if seed is None else int(seed) & 0xFFFFFFFF
        if flips <= 0:
            return 0

        if self._has_native("perturb"):
            changed = int(_life_sim.perturb(self._state_mv, self.width, self.height, seed_value, flips))
            self._yield_after_native_helper()
        else:
            changed = 0
            for _ in range(flips):
                index = getrandbits(30) % self.size
                self.state[index] ^= 1
                changed += 1

        self.last_alive, self.last_hash = self.analyze()
        return changed

    def step(self):
        if self.native_step_rows > 0:
            if self._has_native_step("step_rows"):
                alive_total = 0
                hash_value = 2166136261
                hash_seed = _signed_u32(hash_value)
                rows_per_chunk = max(1, self.native_step_rows)
                for start_row in range(0, self.height, rows_per_chunk):
                    alive_chunk, hash_value = _life_sim.step_rows(
                        self._state_mv,
                        self._next_mv,
                        self.width,
                        self.height,
                        self.wrap,
                        start_row,
                        rows_per_chunk,
                        hash_seed,
                    )
                    alive_total += int(alive_chunk)
                    hash_seed = _signed_u32(hash_value)
                    _yield_runtime(self.native_step_yield_ms)

                self._swap_buffers()
                self.generation += 1
                self.last_alive = int(alive_total)
                self.last_hash = int(hash_value) & 0xFFFFFFFF
                return self.last_alive
        else:
            if self._has_native_step("step_stats"):
                alive_total, hash_value = _life_sim.step_stats(
                    self._state_mv,
                    self._next_mv,
                    self.width,
                    self.height,
                    self.wrap,
                )
                self._swap_buffers()
                self.generation += 1
                self.last_alive = int(alive_total)
                self.last_hash = int(hash_value) & 0xFFFFFFFF
                return self.last_alive

            if self._has_native_step("step"):
                alive_total = int(_life_sim.step(self._state_mv, self._next_mv, self.width, self.height, self.wrap))
                self._swap_buffers()
                self.generation += 1
                _alive, hash_value = self.analyze()
                self.last_alive = alive_total
                self.last_hash = hash_value
                return self.last_alive

        alive_total = self._step_py()
        self._swap_buffers()
        self.generation += 1
        self.last_alive = int(alive_total)
        self.last_hash = _hash_state_py(self.state)[1]
        return self.last_alive

    def _step_py(self):
        alive_total = 0
        width = self.width
        state = self.state
        next_state = self.next_state
        prev_row = self._prev_row
        next_row = self._next_row
        prev_col = self._prev_col
        next_col = self._next_col

        for y in range(self.height):
            row_idx = y * width
            row_above = prev_row[y] * width
            row_below = next_row[y] * width
            for x in range(width):
                idx = row_idx + x
                left = prev_col[x]
                right = next_col[x]

                neighbors = (
                    state[row_above + left]
                    + state[row_above + x]
                    + state[row_above + right]
                    + state[row_idx + left]
                    + state[row_idx + right]
                    + state[row_below + left]
                    + state[row_below + x]
                    + state[row_below + right]
                )

                if neighbors == 3 or (state[idx] and neighbors == 2):
                    next_state[idx] = 1
                    alive_total += 1
                else:
                    next_state[idx] = 0

        return alive_total

    def viewport(self, panel_width, panel_height, view_x=0, view_y=0, center_small=True):
        panel_width = int(panel_width)
        panel_height = int(panel_height)

        if self.width <= panel_width:
            src_x = 0
            dest_x = (panel_width - self.width) // 2 if center_small else 0
            draw_width = self.width
        else:
            max_x = self.width - panel_width
            src_x = int(view_x)
            if src_x < 0:
                src_x = 0
            elif src_x > max_x:
                src_x = max_x
            dest_x = 0
            draw_width = panel_width

        if self.height <= panel_height:
            src_y = 0
            dest_y = (panel_height - self.height) // 2 if center_small else 0
            draw_height = self.height
        else:
            max_y = self.height - panel_height
            src_y = int(view_y)
            if src_y < 0:
                src_y = 0
            elif src_y > max_y:
                src_y = max_y
            dest_y = 0
            draw_height = panel_height

        return src_x, src_y, dest_x, dest_y, draw_width, draw_height

    def render(self, display, alive_color, dead_color=None, view_x=0, view_y=0, center_small=True):
        panel_width = int(display.width)
        panel_height = int(display.height)
        src_x, src_y, dest_x, dest_y, draw_width, draw_height = self.viewport(
            panel_width,
            panel_height,
            view_x=view_x,
            view_y=view_y,
            center_small=center_small,
        )
        alive_color_val = _to_rgb565(alive_color)
        if dead_color is None:
            dead_color = 0
        dead_color_val = _to_rgb565(dead_color)

        if self._has_native_render("render_rgb565"):
            return int(
                _life_sim.render_rgb565(
                    display.rgb565_buffer,
                    panel_width,
                    panel_height,
                    self._state_mv,
                    self.width,
                    self.height,
                    src_x,
                    src_y,
                    dest_x,
                    dest_y,
                    draw_width,
                    draw_height,
                    alive_color_val,
                    dead_color_val,
                )
            )

        fb = display.framebuf
        fb.fill(dead_color_val)
        visible_alive = 0
        for offset_y in range(draw_height):
            row = (src_y + offset_y) * self.width
            dest_row = dest_y + offset_y
            for offset_x in range(draw_width):
                if self.state[row + src_x + offset_x]:
                    fb.pixel(dest_x + offset_x, dest_row, alive_color_val)
                    visible_alive += 1
        return visible_alive


__all__ = [
    "LIFE_FILE_MAGIC",
    "LIFE_FILE_VERSION",
    "PATTERN_NAMES",
    "PATTERN_NAME_TO_ID",
    "LifeWorld",
    "pack_world_bits",
    "parse_ascii_grid",
    "read_life_seed_file",
    "rgb565",
    "unpack_world_bits",
    "write_life_seed_file",
]