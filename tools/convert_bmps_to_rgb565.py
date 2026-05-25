import argparse
from pathlib import Path


DEFAULT_PANEL_WIDTH = 128
DEFAULT_PANEL_HEIGHT = 64
DEFAULT_GAMMA = 2.2

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DEFAULT_SOURCE_DIR = PROJECT_ROOT / "bmp"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "bin"


def pack_rgb565(red, green, blue):
    return ((red & 0xF8) << 8) | ((green & 0xFC) << 3) | (blue >> 3)


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


def decode_bmp_to_rgb565(path, color_lut=None):
    data = path.read_bytes()
    if len(data) < 54:
        raise ValueError("BMP file too short")
    if data[0:2] != b"BM":
        raise ValueError("BMP magic mismatch")

    data_offset = int.from_bytes(data[10:14], "little")
    dib_header_size = int.from_bytes(data[14:18], "little")
    width_signed = int.from_bytes(data[18:22], "little", signed=True)
    height_signed = int.from_bytes(data[22:26], "little", signed=True)
    planes = int.from_bytes(data[26:28], "little")
    bits_per_pixel = int.from_bytes(data[28:30], "little")
    compression = int.from_bytes(data[30:34], "little")

    if dib_header_size < 40:
        raise ValueError("unsupported BMP header")
    if planes != 1:
        raise ValueError("invalid BMP plane count")
    if width_signed <= 0 or height_signed == 0:
        raise ValueError("invalid BMP dimensions")
    if bits_per_pixel not in (24, 32):
        raise ValueError("only 24-bit and 32-bit BMPs are supported")
    if compression != 0:
        raise ValueError("compressed BMPs are not supported")

    width = width_signed
    top_down = height_signed < 0
    height = -height_signed if top_down else height_signed
    bytes_per_pixel = bits_per_pixel >> 3
    row_bytes = (width * bytes_per_pixel + 3) & ~3
    pixel_bytes = row_bytes * height

    if data_offset >= len(data) or pixel_bytes > len(data) - data_offset:
        raise ValueError("BMP pixel data truncated")

    out = bytearray(width * height * 2)
    pixel_data = memoryview(data)[data_offset:data_offset + pixel_bytes]

    for y in range(height):
        src_row_index = y if top_down else (height - 1 - y)
        row_base = src_row_index * row_bytes
        out_base = y * width * 2
        for x in range(width):
            src_base = row_base + x * bytes_per_pixel
            blue = pixel_data[src_base + 0]
            green = pixel_data[src_base + 1]
            red = pixel_data[src_base + 2]

            if color_lut is not None:
                blue = color_lut[blue]
                green = color_lut[green]
                red = color_lut[red]

            pixel = pack_rgb565(red, green, blue)
            dst_base = out_base + x * 2
            out[dst_base + 0] = pixel & 0xFF
            out[dst_base + 1] = (pixel >> 8) & 0xFF

    return width, height, out


def convert_directory(source_dir, output_dir, gamma, panel_width, panel_height):
    if not source_dir.is_dir():
        raise FileNotFoundError("source directory not found: %s" % source_dir)

    output_dir.mkdir(parents=True, exist_ok=True)
    color_lut = build_color_lut(gamma)

    bmp_paths = sorted(
        path for path in source_dir.iterdir() if path.is_file() and path.suffix.lower() == ".bmp"
    )
    if not bmp_paths:
        print("No BMP files found in", source_dir)
        return 0

    converted = 0
    for bmp_path in bmp_paths:
        width, height, rgb565 = decode_bmp_to_rgb565(bmp_path, color_lut=color_lut)
        output_path = output_dir / (bmp_path.stem + ".rgb565")
        output_path.write_bytes(rgb565)

        message = "%s -> %s (%dx%d, %d bytes)" % (
            bmp_path.name,
            output_path.name,
            width,
            height,
            len(rgb565),
        )
        if width != panel_width or height != panel_height:
            message += " [note: not %dx%d panel-sized]" % (panel_width, panel_height)
        print(message)
        converted += 1

    return converted


def main():
    parser = argparse.ArgumentParser(
        description="Convert every BMP in a directory to raw RGB565 files."
    )
    parser.add_argument(
        "source_dir",
        nargs="?",
        default=str(DEFAULT_SOURCE_DIR),
        help="directory containing .bmp files (default: %(default)s)",
    )
    parser.add_argument(
        "output_dir",
        nargs="?",
        default=str(DEFAULT_OUTPUT_DIR),
        help="directory to write .rgb565 files (default: %(default)s)",
    )
    parser.add_argument(
        "--gamma",
        type=float,
        default=DEFAULT_GAMMA,
        help="gamma correction to apply while converting (default: %(default)s)",
    )
    parser.add_argument(
        "--panel-width",
        type=int,
        default=DEFAULT_PANEL_WIDTH,
        help="expected panel width for size notes (default: %(default)s)",
    )
    parser.add_argument(
        "--panel-height",
        type=int,
        default=DEFAULT_PANEL_HEIGHT,
        help="expected panel height for size notes (default: %(default)s)",
    )
    args = parser.parse_args()

    source_dir = Path(args.source_dir)
    output_dir = Path(args.output_dir)
    converted = convert_directory(
        source_dir,
        output_dir,
        gamma=args.gamma,
        panel_width=int(args.panel_width),
        panel_height=int(args.panel_height),
    )
    print("Converted", converted, "BMP file(s).")


if __name__ == "__main__":
    main()