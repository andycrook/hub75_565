# pyright: reportMissingImports=false, reportAttributeAccessIssue=false

import math
import struct


try:
    import h3d_fb as _h3d_fb
except ImportError:
    _h3d_fb = None


H3DM_MAGIC = b"H3DM"
H3DT_MAGIC = b"H3DT"
H3D_VERSION = 1
H3DM_FLAG_TEXTURED = 0x0001
H3DM_FLAG_WIREFRAME = 0x0002

MODE_POINTS = 0
MODE_WIREFRAME = 1
MODE_SOLID = 2
MODE_TEXTURED = 3
MODE_SOLID_WIREFRAME = 4

QUAT_SHIFT = 14
QUAT_ONE = 1 << QUAT_SHIFT
LIGHT_SHIFT = 14
LIGHT_ONE = 1 << LIGHT_SHIFT


def _require_h3d_fb():
    module = _h3d_fb
    if module is None:
        raise ImportError("h3d_fb.mpy is required; build the native module from src/lib/h3d_fb")
    return module


def _zero_bytes(count):
    if count <= 0:
        return bytearray()
    return bytearray(count)


def _ensure_work_buffer(mesh, name, required_size):
    current = getattr(mesh, name, None)
    if current is None or len(current) < required_size:
        setattr(mesh, name, _zero_bytes(required_size))


def _ensure_mesh_work_buffers(mesh):
    vertex_count = max(0, int(getattr(mesh, "vertex_count", 0)))
    face_count = max(0, int(getattr(mesh, "face_count", 0)))
    wire_prim_count = max(0, int(getattr(mesh, "wire_prim_count", 0)))
    sort_item_capacity = face_count + wire_prim_count

    _ensure_work_buffer(mesh, "work_xyz", vertex_count * 3 * 4)
    _ensure_work_buffer(mesh, "work_screen", vertex_count * 2 * 2)
    _ensure_work_buffer(mesh, "work_depths", sort_item_capacity * 4)
    _ensure_work_buffer(mesh, "work_faces", sort_item_capacity * 4)


def _mode_to_int(mode):
    if isinstance(mode, str):
        key = mode.lower()
        if key in ("points", "vertices"):
            return MODE_POINTS
        if key in ("wire", "wireframe"):
            return MODE_WIREFRAME
        if key in ("solid", "flat"):
            return MODE_SOLID
        if key in ("textured", "texture"):
            return MODE_TEXTURED
        if key in (
            "solidwire",
            "solid+wire",
            "solidwireframe",
            "solid wireframe",
            "solid-wireframe",
            "solid_wireframe",
        ):
            return MODE_SOLID_WIREFRAME
        raise ValueError("mode must be points/wireframe/solid/textured/solid-wireframe")

    mode_val = int(mode)
    if mode_val not in (MODE_POINTS, MODE_WIREFRAME, MODE_SOLID, MODE_TEXTURED, MODE_SOLID_WIREFRAME):
        raise ValueError("mode must be points/wireframe/solid/textured/solid-wireframe")
    return mode_val


def quat_identity():
    return (0, 0, 0, QUAT_ONE)


def quat_normalize(quaternion):
    qx, qy, qz, qw = quaternion
    length = math.sqrt(float(qx * qx + qy * qy + qz * qz + qw * qw))
    if length <= 0.0:
        return quat_identity()
    scale = QUAT_ONE / length
    return (
        int(round(qx * scale)),
        int(round(qy * scale)),
        int(round(qz * scale)),
        int(round(qw * scale)),
    )


def _quat_multiply_raw(lhs, rhs):
    lx, ly, lz, lw = lhs
    rx, ry, rz, rw = rhs
    return (
        ((lw * rx + lx * rw + ly * rz - lz * ry) >> QUAT_SHIFT),
        ((lw * ry - lx * rz + ly * rw + lz * rx) >> QUAT_SHIFT),
        ((lw * rz + lx * ry - ly * rx + lz * rw) >> QUAT_SHIFT),
        ((lw * rw - lx * rx - ly * ry - lz * rz) >> QUAT_SHIFT),
    )


def quat_multiply(lhs, rhs):
    return quat_normalize(_quat_multiply_raw(lhs, rhs))


def quat_compose(*quaternions):
    if not quaternions:
        return quat_identity()

    result = quaternions[0]
    for quaternion in quaternions[1:]:
        result = _quat_multiply_raw(result, quaternion)
    return quat_normalize(result)


def quat_from_axis_angle(axis, degrees):
    ax = float(axis[0])
    ay = float(axis[1])
    az = float(axis[2])
    length = math.sqrt(ax * ax + ay * ay + az * az)
    if length <= 0.0:
        return quat_identity()

    ax /= length
    ay /= length
    az /= length
    half_angle = math.radians(float(degrees)) * 0.5
    sin_half = math.sin(half_angle)
    cos_half = math.cos(half_angle)
    return quat_normalize(
        (
            int(round(ax * sin_half * QUAT_ONE)),
            int(round(ay * sin_half * QUAT_ONE)),
            int(round(az * sin_half * QUAT_ONE)),
            int(round(cos_half * QUAT_ONE)),
        )
    )


def light_dir_q14(direction):
    dx = float(direction[0])
    dy = float(direction[1])
    dz = float(direction[2])
    length = math.sqrt(dx * dx + dy * dy + dz * dz)
    if length <= 0.0:
        return (0, 0, LIGHT_ONE)
    return (
        int(round(dx * LIGHT_ONE / length)),
        int(round(dy * LIGHT_ONE / length)),
        int(round(dz * LIGHT_ONE / length)),
    )


def _clamp_u8(value):
    value_int = int(value)
    if value_int < 0:
        return 0
    if value_int > 255:
        return 255
    return value_int


def _rgb565_to_rgb888(color):
    color_int = int(color) & 0xFFFF
    red = ((color_int >> 11) & 0x1F) * 255 // 31
    green = ((color_int >> 5) & 0x3F) * 255 // 63
    blue = (color_int & 0x1F) * 255 // 31
    return red, green, blue


def _pack_light_rgb(color):
    if isinstance(color, int):
        color_int = int(color)
        if 0 <= color_int <= 0xFFFF:
            red, green, blue = _rgb565_to_rgb888(color_int)
            return (red << 16) | (green << 8) | blue
        if 0 <= color_int <= 0xFFFFFF:
            return color_int
        raise ValueError("light color int must be RGB565 or 0xRRGGBB")

    if isinstance(color, (tuple, list)) and len(color) >= 3:
        return (
            (_clamp_u8(color[0]) << 16)
            | (_clamp_u8(color[1]) << 8)
            | _clamp_u8(color[2])
        )

    raise TypeError("light color must be RGB565 int, 0xRRGGBB int, or (r, g, b) tuple")


def _light_strength_int(value, default):
    if value is None:
        strength = int(default)
    else:
        strength = int(round(float(value) * 255.0))

    if strength < 0:
        return 0
    if strength > 4095:
        return 4095
    return strength


def _optional_light_dir_q14(direction):
    if direction is None:
        return (0, 0, 0)
    return light_dir_q14(direction)


def _read_u16_le(buf, offset):
    return int(buf[offset]) | (int(buf[offset + 1]) << 8)


def _read_s16_le(buf, offset):
    value = _read_u16_le(buf, offset)
    if value & 0x8000:
        value -= 0x10000
    return value


class Texture3D:
    def __init__(self, width, height, pixels):
        self.width = int(width)
        self.height = int(height)
        self.pixels = pixels

    @classmethod
    def load(cls, path):
        with open(path, "rb") as handle:
            header = handle.read(16)
            if len(header) != 16:
                raise ValueError("H3DT file too short")

            magic, version, flags, width, height, _reserved = struct.unpack(
                "<4s4HI", header
            )
            if magic != H3DT_MAGIC:
                raise ValueError("Not a valid H3DT texture")
            if version != H3D_VERSION:
                raise ValueError("Unsupported H3DT version")
            if flags != 0:
                raise ValueError("Unsupported H3DT flags")

            pixel_bytes = handle.read()

        expected = int(width) * int(height) * 2
        if len(pixel_bytes) != expected:
            raise ValueError("H3DT pixel data truncated")
        return cls(width, height, bytearray(pixel_bytes))


class Mesh3D:
    def __init__(
        self,
        vertices,
        uvs,
        faces,
        wire_prims,
        vertex_count,
        uv_count,
        face_count,
        wire_prim_count,
        bounds_min,
        bounds_max,
        radius,
        textured,
    ):
        self.vertices = vertices
        self.uvs = uvs
        self.faces = faces
        self.wire_prims = wire_prims
        self.vertex_count = int(vertex_count)
        self.uv_count = int(uv_count)
        self.face_count = int(face_count)
        self.wire_prim_count = int(wire_prim_count)
        self.bounds_min = bounds_min
        self.bounds_max = bounds_max
        self.radius = int(radius)
        self.textured = bool(textured)

        sort_item_capacity = self.face_count + self.wire_prim_count
        self.work_xyz = _zero_bytes(self.vertex_count * 3 * 4)
        self.work_screen = _zero_bytes(self.vertex_count * 2 * 2)
        self.work_depths = _zero_bytes(sort_item_capacity * 4)
        self.work_faces = _zero_bytes(sort_item_capacity * 4)

    @classmethod
    def load(cls, path):
        with open(path, "rb") as handle:
            header = handle.read(32)
            if len(header) != 32:
                raise ValueError("H3DM file too short")

            unpacked = struct.unpack("<4s6H6h2H", header)
            magic = unpacked[0]
            version = unpacked[1]
            flags = unpacked[2]
            vertex_count = unpacked[3]
            uv_count = unpacked[4]
            face_count = unpacked[5]
            wire_prim_count = unpacked[6]
            bounds_min = (unpacked[7], unpacked[8], unpacked[9])
            bounds_max = (unpacked[10], unpacked[11], unpacked[12])
            radius = unpacked[13]

            if magic != H3DM_MAGIC:
                raise ValueError("Not a valid H3DM mesh")
            if version != H3D_VERSION:
                raise ValueError("Unsupported H3DM version")

            vertex_bytes = handle.read(vertex_count * 6)
            uv_bytes = handle.read(uv_count * 4)
            face_bytes = handle.read(face_count * 20)
            wire_bytes = handle.read()

        if len(vertex_bytes) != vertex_count * 6:
            raise ValueError("H3DM vertex block truncated")
        if len(uv_bytes) != uv_count * 4:
            raise ValueError("H3DM UV block truncated")
        if len(face_bytes) != face_count * 20:
            raise ValueError("H3DM face block truncated")

        if (flags & H3DM_FLAG_WIREFRAME) and wire_prim_count > 0 and not wire_bytes:
            raise ValueError("H3DM wire block truncated")

        return cls(
            bytearray(vertex_bytes),
            bytearray(uv_bytes),
            bytearray(face_bytes),
            bytearray(wire_bytes),
            vertex_count,
            uv_count,
            face_count,
            wire_prim_count,
            bounds_min,
            bounds_max,
            radius,
            bool(flags & H3DM_FLAG_TEXTURED),
        )


class Instance3D:
    def __init__(
        self,
        mesh,
        texture=None,
        position=(0, 0, 320),
        quaternion=None,
        scale=256,
        mode=MODE_TEXTURED,
        color=None,
        wire_color=None,
    ):
        self.mesh = mesh
        self.texture = texture
        self.position = [int(position[0]), int(position[1]), int(position[2])]
        self.quaternion = quat_identity() if quaternion is None else quat_normalize(quaternion)
        self.scale = int(scale)
        self.mode = _mode_to_int(mode)
        self.color = None if color is None else int(color) & 0xFFFF
        self.wire_color = None if wire_color is None else int(wire_color) & 0xFFFF

    def set_rotation_axis_angle(self, axis, degrees):
        self.quaternion = quat_from_axis_angle(axis, degrees)


def render_instance(
    display,
    instance,
    light_direction=(0, 0, -1),
    ambient=48,
    focal_length=96,
    near_plane=24,
    point_size=1,
    light_color=(255, 255, 255),
    light_strength=None,
    ambient_color=(255, 255, 255),
    fill_light_direction=None,
    fill_light_color=(255, 255, 255),
    fill_light_strength=0.0,
):
    module = _require_h3d_fb()
    mesh = instance.mesh
    texture = instance.texture

    _ensure_mesh_work_buffers(mesh)

    texture_pixels = None if texture is None else texture.pixels
    texture_width = 0 if texture is None else texture.width
    texture_height = 0 if texture is None else texture.height

    ambient_val = max(0, min(255, int(ambient)))
    light_q14 = light_dir_q14(light_direction)
    light_color_packed = _pack_light_rgb(light_color)
    ambient_color_packed = _pack_light_rgb(ambient_color)
    light_strength_val = _light_strength_int(light_strength, max(0, 255 - ambient_val))
    if light_strength_val <= 0:
        light_q14 = (0, 0, 0)

    fill_strength_val = _light_strength_int(fill_light_strength, 0)
    fill_light_q14 = _optional_light_dir_q14(fill_light_direction)
    fill_light_color_packed = _pack_light_rgb(fill_light_color)
    if fill_strength_val <= 0:
        fill_light_q14 = (0, 0, 0)

    color_override = -1 if instance.color is None else int(instance.color)
    wire_color_override = -1 if instance.wire_color is None else int(instance.wire_color)

    return module.render(
        display.rgb565_buffer,
        int(display.width),
        int(display.height),
        mesh.vertices,
        int(mesh.vertex_count),
        mesh.uvs,
        int(mesh.uv_count),
        mesh.faces,
        int(mesh.face_count),
        texture_pixels,
        int(texture_width),
        int(texture_height),
        mesh.work_xyz,
        mesh.work_screen,
        mesh.work_depths,
        mesh.work_faces,
        int(instance.position[0]),
        int(instance.position[1]),
        int(instance.position[2]),
        int(instance.scale),
        int(instance.quaternion[0]),
        int(instance.quaternion[1]),
        int(instance.quaternion[2]),
        int(instance.quaternion[3]),
        int(light_q14[0]),
        int(light_q14[1]),
        int(light_q14[2]),
        int(ambient_val),
        int(instance.mode),
        max(1, int(focal_length)),
        max(1, int(near_plane)),
        max(1, int(point_size)),
        mesh.wire_prims if mesh.wire_prim_count > 0 else None,
        int(mesh.wire_prim_count),
        int(color_override),
        int(wire_color_override),
        int(ambient_color_packed),
        int(light_strength_val),
        int(light_color_packed),
        int(fill_light_q14[0]),
        int(fill_light_q14[1]),
        int(fill_light_q14[2]),
        int(fill_strength_val),
        int(fill_light_color_packed),
    )


def render_scene(
    display,
    instances,
    clear_color=None,
    light_direction=(0, 0, -1),
    ambient=48,
    focal_length=96,
    near_plane=24,
    point_size=1,
    sort_instances=True,
    light_color=(255, 255, 255),
    light_strength=None,
    ambient_color=(255, 255, 255),
    fill_light_direction=None,
    fill_light_color=(255, 255, 255),
    fill_light_strength=0.0,
):
    if clear_color is not None:
        display.fill(clear_color)

    if sort_instances and len(instances) > 1:
        ordered = sorted(instances, key=lambda item: int(item.position[2]), reverse=True)
    else:
        ordered = instances
    visible_faces = 0
    for instance in ordered:
        visible_faces += int(
            render_instance(
                display,
                instance,
                light_direction=light_direction,
                ambient=ambient,
                focal_length=focal_length,
                near_plane=near_plane,
                point_size=point_size,
                light_color=light_color,
                light_strength=light_strength,
                ambient_color=ambient_color,
                fill_light_direction=fill_light_direction,
                fill_light_color=fill_light_color,
                fill_light_strength=fill_light_strength,
            )
        )
    return visible_faces


__all__ = (
    "H3DM_MAGIC",
    "H3DT_MAGIC",
    "MODE_POINTS",
    "MODE_WIREFRAME",
    "MODE_SOLID",
    "MODE_TEXTURED",
    "MODE_SOLID_WIREFRAME",
    "Mesh3D",
    "Texture3D",
    "Instance3D",
    "quat_identity",
    "quat_from_axis_angle",
    "quat_multiply",
    "quat_compose",
    "quat_normalize",
    "render_instance",
    "render_scene",
)