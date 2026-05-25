# pyright: reportMissingImports=false

import gc
import os

import bmp565_ops


SOURCE_DIR = "/bmp"
OUTPUT_DIR = "/bin"
PANEL_WIDTH = 128
PANEL_HEIGHT = 64
DEST_X = 0
DEST_Y = 0
DEFAULT_GAMMA = 2.2

RAW_MODE = False
TRANSPARENCY_ENABLED = False
TRANSPARENCY_R = 0
TRANSPARENCY_G = 0
TRANSPARENCY_B = 0


def build_color_lut(gamma):
    gamma_value = float(gamma)
    if gamma_value <= 0.0:
        raise ValueError("gamma must be > 0")
    if gamma_value == 1.0:
        return None

    lut = bytearray(256)
    for value in range(256):
        lut[value] = int(round(((value / 255.0) ** gamma_value) * 255.0))
    return lut


COLOR_LUT = build_color_lut(DEFAULT_GAMMA)
USE_COLOR_LUT = COLOR_LUT is not None


def ensure_dir(path):
    try:
        os.stat(path)
    except OSError:
        os.mkdir(path)


def iter_bmp_names(path):
    names = []
    for name in os.listdir(path):
        if name.lower().endswith(".bmp"):
            names.append(name)
    names.sort()
    return names


def output_name_for(bmp_name):
    dot = bmp_name.rfind(".")
    if dot < 0:
        return bmp_name + ".rgb565"
    return bmp_name[:dot] + ".rgb565"


def clear_buffer(buffer, blank):
    buffer[:] = blank


def convert_one(source_path, output_path, framebuffer, blank):
    clear_buffer(framebuffer, blank)

    with open(source_path, "rb") as handle:
        bmp_data = handle.read()

    width, height = bmp565_ops.load_bmp(
        bmp_data,
        framebuffer,
        PANEL_WIDTH,
        PANEL_HEIGHT,
        DEST_X,
        DEST_Y,
        RAW_MODE,
        TRANSPARENCY_ENABLED,
        TRANSPARENCY_R,
        TRANSPARENCY_G,
        TRANSPARENCY_B,
        COLOR_LUT,
        USE_COLOR_LUT,
        False,
        0.0,
    )

    with open(output_path, "wb") as handle:
        written = handle.write(framebuffer)

    del bmp_data
    gc.collect()
    return width, height, written


def main():
    ensure_dir(OUTPUT_DIR)

    bmp_names = iter_bmp_names(SOURCE_DIR)
    if not bmp_names:
        print("No BMP files found in", SOURCE_DIR)
        return

    framebuffer_bytes = PANEL_WIDTH * PANEL_HEIGHT * 2
    framebuffer = bytearray(framebuffer_bytes)
    blank = bytes(framebuffer_bytes)

    converted = 0
    for bmp_name in bmp_names:
        source_path = SOURCE_DIR + "/" + bmp_name
        output_path = OUTPUT_DIR + "/" + output_name_for(bmp_name)
        try:
            width, height, written = convert_one(source_path, output_path, framebuffer, blank)
            print(bmp_name, "->", output_path, "(%dx%d," % (width, height), str(written) + " bytes)")
            if width != PANEL_WIDTH or height != PANEL_HEIGHT:
                print("  note: source is not 128x64; output is panel-sized with black padding")
            converted += 1
        except Exception as exc:
            print("FAILED:", bmp_name, exc)

    print("Converted", converted, "BMP file(s).")


main()