#!/usr/bin/env python3

import argparse
from pathlib import Path
import sys


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from life_world import LifeWorld, PATTERN_NAMES, pack_world_bits, parse_ascii_grid


def parse_origin(value):
    if value is None:
        return -1, -1
    raw = str(value).strip().lower()
    if raw in ("auto", "center"):
        return -1, -1
    parts = [part.strip() for part in raw.split(",")]
    if len(parts) != 2:
        raise ValueError("origin must be auto or x,y")
    return int(parts[0]), int(parts[1])


def build_world(args):
    if args.text_grid:
        text = Path(args.text_grid).read_text(encoding="utf-8")
        source_width, source_height, state = parse_ascii_grid(text)
        width = source_width if args.width is None else int(args.width)
        height = source_height if args.height is None else int(args.height)
        world = LifeWorld(width, height, wrap=args.wrap)
        origin_x, origin_y = parse_origin(args.origin)
        world.seed_bits(pack_world_bits(state, source_width, source_height), source_width, source_height, origin_x=origin_x, origin_y=origin_y, clear=True)
        return world

    if args.width is None or args.height is None:
        raise ValueError("--width and --height are required unless --text-grid supplies them")

    world = LifeWorld(int(args.width), int(args.height), wrap=args.wrap)
    if args.pattern:
        origin_x, origin_y = parse_origin(args.origin)
        world.seed_pattern(args.pattern, origin_x=origin_x, origin_y=origin_y, clear=True)
        return world

    density = float(args.random_density)
    world.seed_random(density=density, seed=args.seed)
    return world


def main():
    parser = argparse.ArgumentParser(description="Pack a Life world into a custom .lifebin seed file.")
    parser.add_argument("output", help="output .lifebin path")
    parser.add_argument("--width", type=int, default=None, help="world width")
    parser.add_argument("--height", type=int, default=None, help="world height")
    parser.add_argument("--wrap", action="store_true", help="wrap pattern placement when it would exceed the world")
    parser.add_argument("--origin", default="auto", help="placement origin as auto or x,y")
    parser.add_argument("--seed", type=int, default=None, help="random seed for --random-density")

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--pattern", choices=PATTERN_NAMES, help="built-in Life pattern to place into the world")
    group.add_argument("--text-grid", help="ASCII text file where O/X/#/*/@/1 means alive")
    group.add_argument("--random-density", type=float, help="random fill density from 0.0 to 1.0")

    args = parser.parse_args()
    world = build_world(args)
    payload_bytes = world.write_seed_file(args.output)
    print(
        "Wrote",
        args.output,
        "(%dx%d," % (world.width, world.height),
        payload_bytes,
        "payload bytes,",
        world.last_alive,
        "alive cells)",
    )


if __name__ == "__main__":
    main()