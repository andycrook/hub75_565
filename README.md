# hub75_565

# HUB75 RGB565 Driver for Raspberry Pi Pico 2W.

https://youtu.be/6Kgu8jMMThA

[![Hub75 Driver](https://img.youtube.com/vi/6Kgu8jMMThA/0.jpg)](https://www.youtube.com/watch?v=6Kgu8jMMThA "Hub75 Driver")

A MicroPython project for driving 64-pixel-high HUB75 LED matrix panels from a Raspberry Pi Pico 2 or Pico 2W.

This repo includes:

- A fast RGB565 framebuffer driver built on RP2350 PIO + DMA
- Support for 64x64 and 128x64 (two 64x64 chained) panels
- Raw RGB565 image loading and saving
- BMP loading with optional color adjustment
- RGB565 sprite creation, loading, blitting, and rotation
- Custom HFM bitmap font loading and rendering
- Text rotation, flipping, scaling, alignment, and marquee support
- Demos for framebuf drawing, clocks, sprites, BMP caching, 3D, animations, Game of Life, and a Wi-Fi crypto ticker
- Tools for conversion of bitmaps, fonts, 3D meshes to more efficient types for the driver.

Supported hardware

This driver is written for:

RP2350 boards
Raspberry Pi Pico 2
Raspberry Pi Pico 2W
HUB75E style LED matrix panels
Panel sizes:
64x64
128x64
At the moment the driver only supports panels with:

width = 64 or 128
height = 64
So this is aimed at 64-pixel-high panels. It is not a generic all-panel-size HUB75 driver, but it could be made to work with minor adjustments.


How it works

Hub75FrameBuffer keeps a full RGB565 framebuffer in RAM, exposes it as a standard MicroPython framebuf.FrameBuffer, and then converts that RGB565 image into panel bitplanes using a native helper module.

Under the hood it uses:

RP2350 PIO state machines
DMA
a background refresh thread
a native conversion helper: hub75_fbconv.mpy

Typical flow:

Draw into display.framebuf or use the convenience methods on display
Call display.show()
The driver converts the RGB565 framebuffer into panel bitplanes
The refresh thread continuously scans the panel
The driver automatically sets the RP2350 clock to 250 MHz when initialized.

Wiring
Default GPIO mapping
The default constructor is:

Hub75FrameBuffer(
    data_pin_start=2,
    clock_pin=13,
    latch_pin_start=14,
    row_pin_start=8,
    output_enable_pin=15,
    width=64,
    height=64,
)

That means the default panel wiring is:

HUB75 signal	Pico GPIO	Driver setting
R1	GPIO 2	data_pin_start + 0
G1	GPIO 3	data_pin_start + 1
B1	GPIO 4	data_pin_start + 2
R2	GPIO 5	data_pin_start + 3
G2	GPIO 6	data_pin_start + 4
B2	GPIO 7	data_pin_start + 5
A	GPIO 8	row_pin_start + 0
B	GPIO 9	row_pin_start + 1
C	GPIO 10	row_pin_start + 2
D	GPIO 11	row_pin_start + 3
E	GPIO 12	row_pin_start + 4
CLK	GPIO 13	clock_pin
LAT / STB	GPIO 14	latch_pin_start
OE	GPIO 15	output_enable_pin
Power connections
You also need:

Panel 5V connected to a suitable external 5V supply
Panel GND connected to the external supply ground
Pico GND connected to the same common ground
Important:

Do not try to power a full-size HUB75 panel from the Pico itself
Use a properly sized 5V power supply for the panel
Many panels are happier with proper 5V level shifting on the control/data lines
Some panels will accept 3.3V logic directly, but that is not guaranteed
Notes on pin assumptions
The driver assumes:

the 6 color data pins are consecutive
the 5 row address pins are consecutive
If your wiring is different, override the constructor parameters accordingly.

Which panel connector to use
If your panel has IN and OUT connectors:

connect the Pico to the panel IN connector



Quick start

from hub75_565 import Hub75FrameBuffer, rgb565

BLACK = 0
WHITE = rgb565(255, 255, 255)
CYAN = rgb565(0, 255, 255)

display = Hub75FrameBuffer(width=128, height=64)

try:
    fb = display.framebuf
    fb.fill(BLACK)
    fb.text("Hello HUB75", 2, 2, WHITE)
    fb.hline(0, 12, display.width, CYAN)
    display.show()

    while True:
        pass
finally:
    display.deinit()


Core display methods
show()
Converts the current RGB565 framebuffer into panel bitplanes and swaps it into the live refresh path.

This is the method you usually call after drawing a frame.

refresh()
Alias for show().

deinit()
Stops the refresh thread, shuts down DMA, and disables the PIO state machines.

Call this before exiting a script or resetting your display logic.

fill(color)
Fills the entire framebuffer with a solid RGB565 color.

pixel(x, y, color=None)
Get or set a pixel.

If color is omitted, returns the current pixel value
If color is provided, writes the pixel
clear()
Fills the framebuffer with black.

BMP and RGB565 file methods
load_bmp(filename, x=0, y=0, gamma=1.0, brightness=1.0, contrast=1.0, hue=0.0, raw=False, transparency_color=None)
Loads a BMP file directly into the display framebuffer.

Useful for full-screen images or overlays placed at an offset.

Notes:

requires bmp565_ops.mpy
supports 24-bit / 32-bit BMP decoding through the native helper
gamma, brightness, and contrast are applied through a LUT
raw=True disables LUT-based adjustment
transparency_color can skip a source color
non-zero hue currently raises an error on this target
Returns:

(width, height) of the decoded BMP
save_rgb565(filename)
Writes the current display framebuffer to a raw .rgb565 file.

Returns:

number of bytes written
load_rgb565(filename)
Loads a raw .rgb565 file into the display framebuffer.

The file must match the display geometry exactly.

Returns:

(width, height)
Raw RGB565 file format
This project uses headerless raw RGB565 files:

16 bits per pixel
little-endian
no metadata
file size must equal width * height * 2
Examples:

64x64 image = 8192 bytes
128x64 image = 16384 bytes
32x32 sprite = 2048 bytes
Sprite support
Sprites use the RGB565Sprite helper class and can be blitted into the main framebuffer.

create_sprite(width, height, buffer=None, fill=None, transparent_key=None)
Creates a blank RGB565 sprite.

load_bmp_sprite(filename, gamma=1.0, brightness=1.0, contrast=1.0, hue=0.0, raw=False, transparency_color=None, transparent_key=None)
Loads a BMP into a sprite buffer.

Useful when you want to decode a sprite once and blit it many times.

load_rgb565_sprite(filename, width, height, transparent_key=None)
Loads a raw RGB565 sprite from disk.

rotate_sprite(source, rotation)
Returns a new sprite rotated by:

0
90
180
270
This is intended for sprite use and keeps the sprite centered logically for those quarter-turn rotations.

blit(source, x, y, key=None)
Blits a sprite or framebuffer-like source into the display framebuffer.

If key is omitted, the driver will use the sprite’s transparent_key if available.

RGB565Sprite
Sprite helper object returned by create_sprite() and the sprite load methods.

Attributes
width
height
buffer
rgb565_buffer
framebuf
fb
transparent_key
Methods
fill(color)
pixel(x, y, color=None)
save_rgb565(filename)
The sprite exposes its own framebuf.FrameBuffer, so you can draw into it using normal framebuf operations before blitting.

HFM font support
The driver can load and render bitmap fonts stored in the project’s HFM format.

font_load(path, set_default=True)
Loads an .hfm font file.

Returns a font dictionary and optionally sets it as the default font for the display.

Requires:

hfm_fbtext.mpy
measure_text(...)
Measures text layout before drawing. Useful for alignment, spacing, or marquee setup.

draw_text(...)
Draws text using an HFM font.

Supported options include:

rotation = 0, 90, 180, 270
flip_h
flip_v
integer scale
align = left, center, right
optional background color
letter spacing
line spacing
marquee offsets
The hfm_font_demo.py example shows rotation, flips, scaling, alignment, and background rendering.

Marquee support
The driver has a built-in marquee state machine for scrolling text.

marquee_configure(...)
Sets up a marquee state.

Notable options:

window position
window width
speed
speed_mode
"frame"
"time"
gap between repeats
font, scale, rotation, flips, colors
marquee_step(advance_px=None, draw=True)
Advances the marquee state.

if advance_px is omitted, the configured speed is used
if draw=True, it also redraws the marquee
marquee_draw()
Draws the current marquee state without stepping it.

marquee_clear()
Clears the marquee state.




Using the underlying FrameBuffer directly
A major design goal of the project is that you can work with the standard MicroPython framebuffer API.

Most demos do something like this:

display = Hub75FrameBuffer(width=128, height=64)
surface = display.framebuf

surface.fill(0)
surface.text("Hello", 0, 0, rgb565(255, 255, 255))
surface.line(0, 0, 127, 63, rgb565(255, 0, 0))
display.show()

That means you can keep using familiar framebuf methods like:

fill
pixel
text
line
rect
fill_rect
hline
vline
scroll



Optional native modules
Some features depend on compiled .mpy helpers in lib/.

Required for the core driver
hub75_fbconv.mpy
converts the RGB565 framebuffer into HUB75 scan bitplanes
Required for BMP loading
bmp565_ops.mpy
used by load_bmp() and load_bmp_sprite()
Required for HFM text rendering
hfm_fbtext.mpy
used by font_load(), measure_text(), draw_text(), and marquee functions
Optional, feature-specific helpers
h3d_fb.mpy
accelerated 3D rendering support
life_sim.mpy
accelerated Conway’s Life simulation
rlea_decode.mpy
accelerated RLE animation decode
Project layout
Typical important folders in this repo:

lib/
driver and runtime helpers
fonts/
HFM font files
rgb565/
raw RGB565 images and sprite assets
bmp/
BMP source art
anim/
RLEA animation files
3D/
H3DM/H3DT 3D assets
tools/
desktop conversion/viewer/export tools
Included demos
Examples currently in the repo include:

hello_world_demo.py
minimal “hello world” use of the panel
framebuf_demo.py
standard framebuffer drawing primitives, scroll, and blit
64x64_demo.py
a 64x64-specific demo
bmp_framebuf_demo.py
compares BMP decode vs cached .rgb565 reload
sprite_demo.py
builds sprites in code, exports them as .rgb565, and blits them
hfm_font_demo.py
HFM font loading, scaling, rotation, alignment, and marquee
clock_demo.py
DS1307 RTC clock display
crypto_ticker_demo.py
Wi-Fi + NTP + HTTP demo with a sprite icon
rlea_demo.py
plays RLEA animations
rlea_clock.py
animation playback with a clock overlay
3D_demo.py
3D mesh rendering using H3DM/H3DT assets
life_demo.py
Conway’s Game of Life renderer
Network and RTC extras
Some demos use additional peripherals or configuration files.

Wi-Fi credentials
The network demo uses:

lib/wifi_credentials.py
and expects a file at:

/WIFI/WIFI.txt
Example content:

DS1307 RTC
Clock demos use a DS1307 RTC over I2C.

The demo files currently use:

I2C0
SDA on GPIO 0
SCL on GPIO 1
That RTC wiring is separate from the HUB75 panel wiring.

Desktop tools
The tools/ folder contains desktop-side utilities, including:

BMP to RGB565 converters
sprite conversion tools
a GUI RGB565 viewer/converter/editor
an RLE animation encoder
an OBJ to H3D exporter
a BDF to HFM font tool
These are intended to run on desktop Python, not on the Pico.

Some of them use:

tkinter
Pillow
pygame for certain preview features
Tips and gotchas
Always call display.deinit() when your script exits
show() is the point where framebuffer changes become visible
.rgb565 files are raw and must match the expected dimensions
BMP loading and HFM text rendering require their optional native helpers
The driver only supports 64x64 and 128x64
If you change pin assignments, remember that the color bus and row bus are consecutive pin groups
Use a real 5V supply for the panel and share ground with the Pico
If a panel behaves unreliably with direct 3.3V logic, use proper level shifting





