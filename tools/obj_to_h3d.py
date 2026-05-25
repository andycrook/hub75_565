#!/usr/bin/env python3
"""Convert a textured OBJ plus BMP texture into Pico-friendly H3DM/H3DT assets.

H3DM v1 layout (little-endian):
    0x00  4  magic b"H3DM"
    0x04  2  version
    0x06  2  flags
    0x08  2  vertex_count
    0x0A  2  uv_count
    0x0C  2  face_count
    0x0E  2  reserved
    0x10  6  bbox min xyz (int16)
    0x16  6  bbox max xyz (int16)
    0x1C  2  bounding radius (uint16)
    0x1E  2  reserved
    0x20  ... vertices: int16 x,y,z
           ... uvs: uint16 u,v in texel space
           ... faces: 20-byte records:
               uint16 vi0,vi1,vi2
               uint16 ti0,ti1,ti2
               int8   nx,ny,nz
               uint8  flags
               uint16 color565
               uint16 reserved

H3DT v1 layout (little-endian):
    0x00  4  magic b"H3DT"
    0x04  2  version
    0x06  2  flags
    0x08  2  width
    0x0A  2  height
    0x0C  4  reserved
    0x10  ... row-major RGB565 texels
"""

from __future__ import annotations

import argparse
import math
import os
import struct
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path


H3DM_MAGIC = b"H3DM"
H3DT_MAGIC = b"H3DT"
H3D_VERSION = 1
H3DM_FLAG_TEXTURED = 0x0001
H3DM_FLAG_WIREFRAME = 0x0002
FACE_FLAG_TEXTURED = 0x01
WIRE_FLAG_CLOSED = 0x01
WIRE_FLAG_HAS_NORMAL = 0x02
DEFAULT_TEXTURE_GAMMA = 2.2
TRIANGULATION_EPSILON = 1e-9
PLANARITY_WARN_RATIO = 0.05
PLANARITY_FALLBACK_RATIO = 0.10
BUY_ME_A_COFFEE_URL = "https://www.buymeacoffee.com/andycrook"
BUY_ME_A_COFFEE_LABEL = "Buy me a coffee"


@dataclass
class Material:
    name: str
    color: tuple[float, float, float] = (1.0, 1.0, 1.0)
    texture_path: Path | None = None


@dataclass
class Triangle:
    vertex_indices: tuple[int, int, int]
    texcoord_indices: tuple[int, int, int]
    material_name: str | None


@dataclass
class WirePrimitive:
    vertex_indices: tuple[int, ...]
    closed: bool
    material_name: str | None


@dataclass
class ExportOptions:
    obj_path: Path | None = None
    mesh_out: Path | None = None
    texture_out: Path | None = None
    texture: Path | None = None
    fit: int = 96
    texture_max_width: int = 64
    texture_max_height: int = 64
    gamma_correct_texture: bool = False
    center: bool = True
    preview: bool = False
    preview_mode: str = "solid"


@dataclass
class ExportResult:
    obj_path: Path
    mesh_path: Path
    texture_path: Path
    vertex_count: int
    uv_count: int
    face_count: int
    wire_count: int
    texture_width: int
    texture_height: int
    has_texture: bool
    gamma_corrected_texture: bool
    triangulation_report: "TriangulationReport"


@dataclass
class TriangulationReport:
    source_face_count: int = 0
    convex_face_count: int = 0
    concave_face_count: int = 0
    projected_face_count: int = 0
    fallback_face_count: int = 0
    degenerate_face_count: int = 0
    notes: list[str] = field(default_factory=list)

    def add_note(self, message: str) -> None:
        if len(self.notes) < 5:
            self.notes.append(message)

    def summary_text(self) -> str:
        return (
            f"convex {self.convex_face_count}, "
            f"concave {self.concave_face_count}, "
            f"projected {self.projected_face_count}, "
            f"fallback {self.fallback_face_count}, "
            f"degenerate {self.degenerate_face_count}"
        )


def rgb565(red: int, green: int, blue: int) -> int:
    return ((red & 0xF8) << 8) | ((green & 0xFC) << 3) | (blue >> 3)


def clamp_byte(value: int) -> int:
    if value < 0:
        return 0
    if value > 255:
        return 255
    return value


def build_color_lut(gamma: float):
    gamma_value = float(gamma)
    if gamma_value <= 0.0:
        raise ValueError("gamma must be > 0")
    if gamma_value == 1.0:
        return None

    lut = bytearray(256)
    for value in range(256):
        lut[value] = int(round(((value / 255.0) ** gamma_value) * 255.0))
    return lut


def resolve_obj_index(raw_index: int, count: int) -> int:
    if raw_index > 0:
        return raw_index - 1
    if raw_index < 0:
        return count + raw_index
    raise ValueError("OBJ indices are 1-based; zero is invalid")


def parse_face_vertex(token: str, vertex_count: int, texcoord_count: int) -> tuple[int, int]:
    parts = token.split("/")
    if not parts[0]:
        raise ValueError(f"Malformed face vertex: {token}")

    vertex_index = resolve_obj_index(int(parts[0]), vertex_count)

    texcoord_index = 0xFFFF
    if len(parts) >= 2 and parts[1]:
        texcoord_index = resolve_obj_index(int(parts[1]), texcoord_count)

    return vertex_index, texcoord_index


def parse_vertex_index(token: str, vertex_count: int) -> int:
    head = token.split("/", 1)[0]
    if not head:
        raise ValueError(f"Malformed vertex reference: {token}")
    return resolve_obj_index(int(head), vertex_count)


def parse_mtl_file(path: Path) -> dict[str, Material]:
    materials: dict[str, Material] = {}
    current: Material | None = None

    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            parts = line.split(None, 1)
            key = parts[0]
            value = parts[1] if len(parts) > 1 else ""

            if key == "newmtl":
                current = Material(name=value)
                materials[current.name] = current
            elif current is None:
                continue
            elif key == "Kd":
                channels = value.split()
                if len(channels) >= 3:
                    current.color = (
                        float(channels[0]),
                        float(channels[1]),
                        float(channels[2]),
                    )
            elif key == "map_Kd":
                texture_path = (path.parent / value).resolve()
                current.texture_path = texture_path

    return materials


def _vector_sub(lhs: tuple[float, float, float], rhs: tuple[float, float, float]) -> tuple[float, float, float]:
    return (lhs[0] - rhs[0], lhs[1] - rhs[1], lhs[2] - rhs[2])


def _dot3(lhs: tuple[float, float, float], rhs: tuple[float, float, float]) -> float:
    return lhs[0] * rhs[0] + lhs[1] * rhs[1] + lhs[2] * rhs[2]


def _cross3(lhs: tuple[float, float, float], rhs: tuple[float, float, float]) -> tuple[float, float, float]:
    return (
        lhs[1] * rhs[2] - lhs[2] * rhs[1],
        lhs[2] * rhs[0] - lhs[0] * rhs[2],
        lhs[0] * rhs[1] - lhs[1] * rhs[0],
    )


def _length3(vector: tuple[float, float, float]) -> float:
    return math.sqrt(_dot3(vector, vector))


def _sanitize_face_items(face_items: list[tuple[int, int]]) -> list[tuple[int, int]]:
    cleaned: list[tuple[int, int]] = []
    for item in face_items:
        if cleaned and item == cleaned[-1]:
            continue
        cleaned.append(item)

    if len(cleaned) > 1 and cleaned[0] == cleaned[-1]:
        cleaned.pop()

    return cleaned


def _fan_triangulation_indices(count: int) -> list[tuple[int, int, int]]:
    return [(0, index, index + 1) for index in range(1, count - 1)]


def _make_triangle(
    face_items: list[tuple[int, int]],
    triangle_indices: tuple[int, int, int],
    material_name: str | None,
) -> Triangle:
    a_index, b_index, c_index = triangle_indices
    a = face_items[a_index]
    b = face_items[b_index]
    c = face_items[c_index]
    return Triangle(
        vertex_indices=(a[0], b[0], c[0]),
        texcoord_indices=(a[1], b[1], c[1]),
        material_name=material_name,
    )


def _compute_face_normal(points: list[tuple[float, float, float]]) -> tuple[float, float, float]:
    nx = 0.0
    ny = 0.0
    nz = 0.0
    count = len(points)
    for index in range(count):
        x0, y0, z0 = points[index]
        x1, y1, z1 = points[(index + 1) % count]
        nx += (y0 - y1) * (z0 + z1)
        ny += (z0 - z1) * (x0 + x1)
        nz += (x0 - x1) * (y0 + y1)

    normal = (nx, ny, nz)
    if _length3(normal) > TRIANGULATION_EPSILON:
        return normal

    origin = points[0]
    for index in range(1, count - 1):
        edge_a = _vector_sub(points[index], origin)
        edge_b = _vector_sub(points[index + 1], origin)
        normal = _cross3(edge_a, edge_b)
        if _length3(normal) > TRIANGULATION_EPSILON:
            return normal

    return (0.0, 0.0, 0.0)


def _face_planarity_ratio(
    points: list[tuple[float, float, float]],
    normal: tuple[float, float, float],
) -> float:
    normal_length = _length3(normal)
    if normal_length <= TRIANGULATION_EPSILON:
        return 0.0

    origin = points[0]
    inv_length = 1.0 / normal_length
    max_distance = 0.0
    min_x = max_x = points[0][0]
    min_y = max_y = points[0][1]
    min_z = max_z = points[0][2]

    for point in points[1:]:
        min_x = min(min_x, point[0])
        max_x = max(max_x, point[0])
        min_y = min(min_y, point[1])
        max_y = max(max_y, point[1])
        min_z = min(min_z, point[2])
        max_z = max(max_z, point[2])

        distance = abs(_dot3(_vector_sub(point, origin), normal)) * inv_length
        max_distance = max(max_distance, distance)

    extent = max(max_x - min_x, max_y - min_y, max_z - min_z, TRIANGULATION_EPSILON)
    return max_distance / extent


def _project_polygon_to_2d(
    points: list[tuple[float, float, float]],
    normal: tuple[float, float, float],
) -> list[tuple[float, float]]:
    abs_x = abs(normal[0])
    abs_y = abs(normal[1])
    abs_z = abs(normal[2])

    if abs_x >= abs_y and abs_x >= abs_z:
        return [(point[1], point[2]) for point in points]
    if abs_y >= abs_z:
        return [(point[0], point[2]) for point in points]
    return [(point[0], point[1]) for point in points]


def _orientation_2d(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _signed_area_2d(points: list[tuple[float, float]]) -> float:
    area = 0.0
    count = len(points)
    for index in range(count):
        x0, y0 = points[index]
        x1, y1 = points[(index + 1) % count]
        area += x0 * y1 - y0 * x1
    return area * 0.5


def _point_in_triangle_2d(
    point: tuple[float, float],
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
    winding_positive: bool,
) -> bool:
    edge0 = _orientation_2d(a, b, point)
    edge1 = _orientation_2d(b, c, point)
    edge2 = _orientation_2d(c, a, point)
    if winding_positive:
        return (
            edge0 >= -TRIANGULATION_EPSILON
            and edge1 >= -TRIANGULATION_EPSILON
            and edge2 >= -TRIANGULATION_EPSILON
        )
    return (
        edge0 <= TRIANGULATION_EPSILON
        and edge1 <= TRIANGULATION_EPSILON
        and edge2 <= TRIANGULATION_EPSILON
    )


def _on_segment_2d(
    a: tuple[float, float],
    b: tuple[float, float],
    point: tuple[float, float],
) -> bool:
    return (
        min(a[0], b[0]) - TRIANGULATION_EPSILON <= point[0] <= max(a[0], b[0]) + TRIANGULATION_EPSILON
        and min(a[1], b[1]) - TRIANGULATION_EPSILON <= point[1] <= max(a[1], b[1]) + TRIANGULATION_EPSILON
        and abs(_orientation_2d(a, b, point)) <= TRIANGULATION_EPSILON
    )


def _segments_intersect_2d(
    a0: tuple[float, float],
    a1: tuple[float, float],
    b0: tuple[float, float],
    b1: tuple[float, float],
) -> bool:
    orientation0 = _orientation_2d(a0, a1, b0)
    orientation1 = _orientation_2d(a0, a1, b1)
    orientation2 = _orientation_2d(b0, b1, a0)
    orientation3 = _orientation_2d(b0, b1, a1)

    if (
        ((orientation0 > TRIANGULATION_EPSILON and orientation1 < -TRIANGULATION_EPSILON) or (orientation0 < -TRIANGULATION_EPSILON and orientation1 > TRIANGULATION_EPSILON))
        and ((orientation2 > TRIANGULATION_EPSILON and orientation3 < -TRIANGULATION_EPSILON) or (orientation2 < -TRIANGULATION_EPSILON and orientation3 > TRIANGULATION_EPSILON))
    ):
        return True

    if abs(orientation0) <= TRIANGULATION_EPSILON and _on_segment_2d(a0, a1, b0):
        return True
    if abs(orientation1) <= TRIANGULATION_EPSILON and _on_segment_2d(a0, a1, b1):
        return True
    if abs(orientation2) <= TRIANGULATION_EPSILON and _on_segment_2d(b0, b1, a0):
        return True
    if abs(orientation3) <= TRIANGULATION_EPSILON and _on_segment_2d(b0, b1, a1):
        return True
    return False


def _polygon_self_intersects(points: list[tuple[float, float]]) -> bool:
    count = len(points)
    for index in range(count):
        a0 = points[index]
        a1 = points[(index + 1) % count]
        for other_index in range(index + 1, count):
            if abs(index - other_index) <= 1 or (index == 0 and other_index == count - 1):
                continue
            b0 = points[other_index]
            b1 = points[(other_index + 1) % count]
            if _segments_intersect_2d(a0, a1, b0, b1):
                return True
    return False


def _polygon_is_concave(points: list[tuple[float, float]]) -> bool:
    area = _signed_area_2d(points)
    if abs(area) <= TRIANGULATION_EPSILON:
        return False

    winding_positive = area > 0.0
    count = len(points)
    for index in range(count):
        prev_point = points[(index - 1) % count]
        curr_point = points[index]
        next_point = points[(index + 1) % count]
        orientation = _orientation_2d(prev_point, curr_point, next_point)
        if abs(orientation) <= TRIANGULATION_EPSILON:
            continue
        if winding_positive and orientation < -TRIANGULATION_EPSILON:
            return True
        if (not winding_positive) and orientation > TRIANGULATION_EPSILON:
            return True
    return False


def _ear_clip_triangulation_indices(points: list[tuple[float, float]]) -> list[tuple[int, int, int]]:
    area = _signed_area_2d(points)
    if abs(area) <= TRIANGULATION_EPSILON:
        raise ValueError("projected polygon area is zero")

    winding_positive = area > 0.0
    remaining = list(range(len(points)))
    triangles: list[tuple[int, int, int]] = []
    guard = len(points) * len(points)

    while len(remaining) > 3 and guard > 0:
        ear_found = False
        remaining_count = len(remaining)
        for position, current_index in enumerate(remaining):
            prev_index = remaining[(position - 1) % remaining_count]
            next_index = remaining[(position + 1) % remaining_count]
            orientation = _orientation_2d(points[prev_index], points[current_index], points[next_index])
            if winding_positive:
                if orientation <= TRIANGULATION_EPSILON:
                    continue
            elif orientation >= -TRIANGULATION_EPSILON:
                continue

            is_ear = True
            for other_index in remaining:
                if other_index in (prev_index, current_index, next_index):
                    continue
                if _point_in_triangle_2d(
                    points[other_index],
                    points[prev_index],
                    points[current_index],
                    points[next_index],
                    winding_positive,
                ):
                    is_ear = False
                    break

            if not is_ear:
                continue

            triangles.append((prev_index, current_index, next_index))
            del remaining[position]
            ear_found = True
            break

        if not ear_found:
            raise ValueError("ear clipping failed")
        guard -= 1

    if len(remaining) != 3:
        raise ValueError("ear clipping ended with an unexpected vertex count")

    final_orientation = _orientation_2d(points[remaining[0]], points[remaining[1]], points[remaining[2]])
    if abs(final_orientation) <= TRIANGULATION_EPSILON:
        raise ValueError("final ear is degenerate")
    triangles.append((remaining[0], remaining[1], remaining[2]))
    return triangles


def _triangulate_face(
    face_items: list[tuple[int, int]],
    vertices: list[tuple[float, float, float]],
    material_name: str | None,
    report: TriangulationReport,
    face_number: int,
) -> tuple[list[Triangle], tuple[int, ...]]:
    cleaned_items = _sanitize_face_items(face_items)
    wire_indices = tuple(item[0] for item in cleaned_items)
    if len(cleaned_items) < 3:
        report.degenerate_face_count += 1
        report.add_note(f"Face {face_number}: skipped degenerate polygon with fewer than 3 unique vertices")
        return [], wire_indices

    if len(cleaned_items) == 3:
        report.convex_face_count += 1
        return [_make_triangle(cleaned_items, (0, 1, 2), material_name)], wire_indices

    points_3d = [vertices[item[0]] for item in cleaned_items]
    normal = _compute_face_normal(points_3d)
    if _length3(normal) <= TRIANGULATION_EPSILON:
        report.degenerate_face_count += 1
        report.add_note(f"Face {face_number}: skipped degenerate polygon with zero area")
        return [], wire_indices

    projected_points = _project_polygon_to_2d(points_3d, normal)
    planarity_ratio = _face_planarity_ratio(points_3d, normal)
    is_concave = _polygon_is_concave(projected_points)
    if is_concave:
        report.concave_face_count += 1
    else:
        report.convex_face_count += 1

    if planarity_ratio > PLANARITY_WARN_RATIO:
        report.projected_face_count += 1
        if planarity_ratio <= PLANARITY_FALLBACK_RATIO:
            report.add_note(
                f"Face {face_number}: projected non-planar polygon ({planarity_ratio:.1%} deviation)"
            )

    fallback_reason = None
    if _polygon_self_intersects(projected_points):
        fallback_reason = "self-intersecting polygon"
    elif planarity_ratio > PLANARITY_FALLBACK_RATIO:
        fallback_reason = f"non-planar polygon ({planarity_ratio:.1%} deviation)"

    if fallback_reason is None:
        try:
            triangle_indices = _ear_clip_triangulation_indices(projected_points)
        except ValueError as exc:
            fallback_reason = str(exc)
            triangle_indices = _fan_triangulation_indices(len(cleaned_items))
    else:
        triangle_indices = _fan_triangulation_indices(len(cleaned_items))

    if fallback_reason is not None:
        report.fallback_face_count += 1
        report.add_note(f"Face {face_number}: {fallback_reason}; used fan triangulation fallback")

    return [
        _make_triangle(cleaned_items, triangle_index, material_name)
        for triangle_index in triangle_indices
    ], wire_indices


def parse_obj(path: Path) -> tuple[
    list[tuple[float, float, float]],
    list[tuple[float, float]],
    list[Triangle],
    list[WirePrimitive],
    dict[str, Material],
    TriangulationReport,
]:
    vertices: list[tuple[float, float, float]] = []
    texcoords: list[tuple[float, float]] = []
    triangles: list[Triangle] = []
    wire_primitives: list[WirePrimitive] = []
    materials: dict[str, Material] = {}
    current_material: str | None = None
    triangulation_report = TriangulationReport()
    face_number = 0

    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            parts = line.split()
            head = parts[0]

            if head == "mtllib":
                for name in parts[1:]:
                    materials.update(parse_mtl_file((path.parent / name).resolve()))
            elif head == "usemtl":
                current_material = parts[1] if len(parts) > 1 else None
            elif head == "v" and len(parts) >= 4:
                vertices.append((float(parts[1]), float(parts[2]), float(parts[3])))
            elif head == "vt" and len(parts) >= 3:
                texcoords.append((float(parts[1]), float(parts[2])))
            elif head == "f" and len(parts) >= 4:
                face_number += 1
                triangulation_report.source_face_count += 1
                parsed = [
                    parse_face_vertex(token, len(vertices), len(texcoords)) for token in parts[1:]
                ]
                face_triangles, wire_indices = _triangulate_face(
                    parsed,
                    vertices,
                    current_material,
                    triangulation_report,
                    face_number,
                )
                if len(wire_indices) >= 2:
                    wire_primitives.append(
                        WirePrimitive(
                            vertex_indices=wire_indices,
                            closed=True,
                            material_name=current_material,
                        )
                    )
                triangles.extend(face_triangles)
            elif head == "l" and len(parts) >= 3:
                wire_vertices = tuple(
                    parse_vertex_index(token, len(vertices)) for token in parts[1:]
                )
                if len(wire_vertices) >= 2:
                    wire_primitives.append(
                        WirePrimitive(
                            vertex_indices=wire_vertices,
                            closed=False,
                            material_name=current_material,
                        )
                    )

    if not vertices:
        raise ValueError("OBJ contains no vertices")
    if not triangles and not wire_primitives:
        raise ValueError("OBJ contains no faces or lines")

    return vertices, texcoords, triangles, wire_primitives, materials, triangulation_report


def load_bmp(
    path: Path,
    color_lut=None,
) -> tuple[int, int, list[tuple[int, int, int]]]:
    blob = path.read_bytes()
    if len(blob) < 54 or blob[:2] != b"BM":
        raise ValueError(f"Unsupported BMP file: {path}")

    data_offset = struct.unpack_from("<I", blob, 10)[0]
    dib_size = struct.unpack_from("<I", blob, 14)[0]
    width = struct.unpack_from("<i", blob, 18)[0]
    height = struct.unpack_from("<i", blob, 22)[0]
    planes = struct.unpack_from("<H", blob, 26)[0]
    bits_per_pixel = struct.unpack_from("<H", blob, 28)[0]
    compression = struct.unpack_from("<I", blob, 30)[0]

    if dib_size < 40:
        raise ValueError("Unsupported BMP header size")
    if planes != 1:
        raise ValueError("Invalid BMP plane count")
    if width <= 0 or height == 0:
        raise ValueError("Invalid BMP dimensions")
    if bits_per_pixel not in (24, 32):
        raise ValueError("Only uncompressed 24-bit and 32-bit BMP textures are supported")
    if compression != 0:
        raise ValueError("Compressed BMP textures are not supported")

    top_down = height < 0
    abs_height = -height if top_down else height
    bytes_per_pixel = bits_per_pixel // 8
    row_bytes = (width * bytes_per_pixel + 3) & ~3
    total_bytes = row_bytes * abs_height
    if data_offset + total_bytes > len(blob):
        raise ValueError("BMP pixel data truncated")

    pixels: list[tuple[int, int, int]] = []
    for row in range(abs_height):
        src_row = row if top_down else (abs_height - 1 - row)
        row_base = data_offset + src_row * row_bytes
        for col in range(width):
            base = row_base + col * bytes_per_pixel
            blue = blob[base + 0]
            green = blob[base + 1]
            red = blob[base + 2]
            if color_lut is not None:
                blue = color_lut[blue]
                green = color_lut[green]
                red = color_lut[red]
            pixels.append((red, green, blue))

    return width, abs_height, pixels


def resize_pixels(
    pixels: list[tuple[int, int, int]],
    width: int,
    height: int,
    max_width: int,
    max_height: int,
) -> tuple[int, int, list[tuple[int, int, int]]]:
    if width <= max_width and height <= max_height:
        return width, height, pixels

    scale = min(max_width / float(width), max_height / float(height))
    new_width = max(1, int(round(width * scale)))
    new_height = max(1, int(round(height * scale)))

    resized: list[tuple[int, int, int]] = []
    for y in range(new_height):
        src_y = min(height - 1, int(y * height / new_height))
        row_base = src_y * width
        for x in range(new_width):
            src_x = min(width - 1, int(x * width / new_width))
            resized.append(pixels[row_base + src_x])

    return new_width, new_height, resized


def choose_texture_path(
    explicit_texture: Path | None,
    triangles: list[Triangle],
    materials: dict[str, Material],
) -> Path | None:
    if explicit_texture is not None:
        return explicit_texture.resolve()

    texture_paths: set[Path] = set()
    for tri in triangles:
        if not tri.material_name:
            continue
        material = materials.get(tri.material_name)
        if material and material.texture_path is not None:
            texture_paths.add(material.texture_path)

    if not texture_paths:
        return None
    if len(texture_paths) > 1:
        joined = ", ".join(str(path) for path in sorted(texture_paths))
        raise ValueError(
            "V1 exporter supports only one diffuse texture per mesh; found: " + joined
        )
    return next(iter(texture_paths))


def quantize_vertices(
    vertices: list[tuple[float, float, float]],
    fit: int,
    center: bool,
) -> list[tuple[int, int, int]]:
    min_x = min(v[0] for v in vertices)
    min_y = min(v[1] for v in vertices)
    min_z = min(v[2] for v in vertices)
    max_x = max(v[0] for v in vertices)
    max_y = max(v[1] for v in vertices)
    max_z = max(v[2] for v in vertices)

    center_x = (min_x + max_x) * 0.5 if center else 0.0
    center_y = (min_y + max_y) * 0.5 if center else 0.0
    center_z = (min_z + max_z) * 0.5 if center else 0.0

    max_extent = 0.0
    for x, y, z in vertices:
        max_extent = max(
            max_extent,
            abs(x - center_x),
            abs(y - center_y),
            abs(z - center_z),
        )

    if max_extent <= 0.0:
        scale = 1.0
    else:
        scale = float(fit) / max_extent

    quantized: list[tuple[int, int, int]] = []
    for x, y, z in vertices:
        qx = int(round((x - center_x) * scale))
        qy = int(round((y - center_y) * scale))
        qz = int(round((z - center_z) * scale))
        if not (-32768 <= qx <= 32767 and -32768 <= qy <= 32767 and -32768 <= qz <= 32767):
            raise ValueError("Quantized vertex exceeds int16 range; reduce fit or rescale source")
        quantized.append((qx, qy, qz))

    return quantized


def quantize_uvs(
    texcoords: list[tuple[float, float]],
    texture_width: int,
    texture_height: int,
) -> list[tuple[int, int]]:
    quantized: list[tuple[int, int]] = []
    for u, v in texcoords:
        tex_u = int(round(u * max(0, texture_width - 1)))
        tex_v = int(round((1.0 - v) * max(0, texture_height - 1)))
        if tex_u < 0:
            tex_u = 0
        elif tex_u >= texture_width:
            tex_u = texture_width - 1
        if tex_v < 0:
            tex_v = 0
        elif tex_v >= texture_height:
            tex_v = texture_height - 1
        quantized.append((tex_u, tex_v))
    return quantized


def pack_normal(x: int, y: int, z: int) -> tuple[int, int, int]:
    length = math.sqrt(float(x * x + y * y + z * z))
    if length <= 0.0:
        return 0, 0, -127

    scale = 127.0 / length
    nx = int(round(x * scale))
    ny = int(round(y * scale))
    nz = int(round(z * scale))

    nx = max(-127, min(127, nx))
    ny = max(-127, min(127, ny))
    nz = max(-127, min(127, nz))
    return nx, ny, nz


def material_rgb565(material_name: str | None, materials: dict[str, Material]) -> int:
    if material_name and material_name in materials:
        r_f, g_f, b_f = materials[material_name].color
    else:
        r_f, g_f, b_f = 1.0, 1.0, 1.0

    red = clamp_byte(int(round(r_f * 255.0)))
    green = clamp_byte(int(round(g_f * 255.0)))
    blue = clamp_byte(int(round(b_f * 255.0)))
    return rgb565(red, green, blue)


def build_face_records(
    vertices: list[tuple[int, int, int]],
    triangles: list[Triangle],
    materials: dict[str, Material],
    has_texture: bool,
) -> bytes:
    face_blob = bytearray()
    for tri in triangles:
        v0 = vertices[tri.vertex_indices[0]]
        v1 = vertices[tri.vertex_indices[1]]
        v2 = vertices[tri.vertex_indices[2]]

        edge1 = (v1[0] - v0[0], v1[1] - v0[1], v1[2] - v0[2])
        edge2 = (v2[0] - v0[0], v2[1] - v0[1], v2[2] - v0[2])
        normal_x = edge1[1] * edge2[2] - edge1[2] * edge2[1]
        normal_y = edge1[2] * edge2[0] - edge1[0] * edge2[2]
        normal_z = edge1[0] * edge2[1] - edge1[1] * edge2[0]
        packed_normal = pack_normal(normal_x, normal_y, normal_z)

        face_flags = 0
        if has_texture and all(index != 0xFFFF for index in tri.texcoord_indices):
            face_flags |= FACE_FLAG_TEXTURED

        face_blob.extend(
            struct.pack(
                "<6H3bBHH",
                tri.vertex_indices[0],
                tri.vertex_indices[1],
                tri.vertex_indices[2],
                tri.texcoord_indices[0] if tri.texcoord_indices[0] != 0xFFFF else 0,
                tri.texcoord_indices[1] if tri.texcoord_indices[1] != 0xFFFF else 0,
                tri.texcoord_indices[2] if tri.texcoord_indices[2] != 0xFFFF else 0,
                packed_normal[0],
                packed_normal[1],
                packed_normal[2],
                face_flags,
                material_rgb565(tri.material_name, materials),
                0,
            )
        )
    return bytes(face_blob)


def build_wire_records(
    vertices: list[tuple[int, int, int]],
    wire_primitives: list[WirePrimitive],
    materials: dict[str, Material],
) -> bytes:
    wire_blob = bytearray()
    for primitive in wire_primitives:
        count = len(primitive.vertex_indices)
        if count < 2:
            continue
        if count > 0xFFFF:
            raise ValueError("Wire primitive exceeds uint16 vertex count")

        flags = 0
        if primitive.closed:
            flags |= WIRE_FLAG_CLOSED

        nx = 0
        ny = 0
        nz = 0
        if primitive.closed and count >= 3:
            v0 = vertices[primitive.vertex_indices[0]]
            v1 = vertices[primitive.vertex_indices[1]]
            v2 = vertices[primitive.vertex_indices[2]]
            edge1 = (v1[0] - v0[0], v1[1] - v0[1], v1[2] - v0[2])
            edge2 = (v2[0] - v0[0], v2[1] - v0[1], v2[2] - v0[2])
            normal_x = edge1[1] * edge2[2] - edge1[2] * edge2[1]
            normal_y = edge1[2] * edge2[0] - edge1[0] * edge2[2]
            normal_z = edge1[0] * edge2[1] - edge1[1] * edge2[0]
            if normal_x or normal_y or normal_z:
                flags |= WIRE_FLAG_HAS_NORMAL
                nx, ny, nz = pack_normal(normal_x, normal_y, normal_z)

        wire_blob.extend(
            struct.pack(
                "<HHBbbb",
                count,
                material_rgb565(primitive.material_name, materials),
                flags,
                nx,
                ny,
                nz,
            )
        )
        for vertex_index in primitive.vertex_indices:
            wire_blob.extend(struct.pack("<H", vertex_index))

    return bytes(wire_blob)


def mesh_bounds(vertices: list[tuple[int, int, int]]) -> tuple[tuple[int, int, int], tuple[int, int, int], int]:
    min_x = min(v[0] for v in vertices)
    min_y = min(v[1] for v in vertices)
    min_z = min(v[2] for v in vertices)
    max_x = max(v[0] for v in vertices)
    max_y = max(v[1] for v in vertices)
    max_z = max(v[2] for v in vertices)
    radius = 0
    for x, y, z in vertices:
        radius = max(radius, int(round(math.sqrt(x * x + y * y + z * z))))
    if radius > 0xFFFF:
        radius = 0xFFFF
    return (min_x, min_y, min_z), (max_x, max_y, max_z), radius


def write_mesh(
    path: Path,
    vertices: list[tuple[int, int, int]],
    uvs: list[tuple[int, int]],
    face_records: bytes,
    wire_records: bytes,
    wire_prim_count: int,
    has_texture: bool,
) -> None:
    bounds_min, bounds_max, radius = mesh_bounds(vertices)

    header_flags = 0
    if has_texture:
        header_flags |= H3DM_FLAG_TEXTURED
    if wire_prim_count > 0:
        header_flags |= H3DM_FLAG_WIREFRAME

    header = struct.pack(
        "<4s6H6h2H",
        H3DM_MAGIC,
        H3D_VERSION,
        header_flags,
        len(vertices),
        len(uvs),
        len(face_records) // 20,
        wire_prim_count,
        bounds_min[0],
        bounds_min[1],
        bounds_min[2],
        bounds_max[0],
        bounds_max[1],
        bounds_max[2],
        radius,
        0,
    )

    with path.open("wb") as handle:
        handle.write(header)
        for x, y, z in vertices:
            handle.write(struct.pack("<3h", x, y, z))
        for u, v in uvs:
            handle.write(struct.pack("<2H", u, v))
        handle.write(face_records)
        handle.write(wire_records)


def write_texture(path: Path, width: int, height: int, pixels: list[tuple[int, int, int]]) -> None:
    header = struct.pack("<4s4HI", H3DT_MAGIC, H3D_VERSION, 0, width, height, 0)
    with path.open("wb") as handle:
        handle.write(header)
        for red, green, blue in pixels:
            handle.write(struct.pack("<H", rgb565(red, green, blue)))


def build_output_paths(input_obj: Path, mesh_out: Path | None, texture_out: Path | None) -> tuple[Path, Path]:
    base = input_obj.with_suffix("")
    mesh_path = mesh_out if mesh_out is not None else base.with_suffix(".h3dm")
    texture_path = texture_out if texture_out is not None else base.with_suffix(".h3dt")
    return mesh_path, texture_path


def format_export_result(result: ExportResult) -> str:
    texture_state = "enabled" if result.has_texture else "fallback"
    gamma_state = "on" if result.gamma_corrected_texture else "off"
    lines = [
        f"OBJ:      {result.obj_path}",
        f"Vertices: {result.vertex_count}",
        f"UVs:      {result.uv_count}",
        f"Faces:    {result.face_count}",
        f"Wire:     {result.wire_count}",
        f"Texture:  {result.texture_width}x{result.texture_height} ({texture_state}, gamma {gamma_state})",
        f"Triang:   {result.triangulation_report.summary_text()}",
        f"Mesh out: {result.mesh_path} ({result.mesh_path.stat().st_size} bytes)",
        f"Tex out:  {result.texture_path} ({result.texture_path.stat().st_size} bytes)",
    ]
    for note in result.triangulation_report.notes:
        lines.append(f"Note:     {note}")
    return "\n".join(lines)


def run_export(options: ExportOptions) -> ExportResult:
    if options.obj_path is None:
        raise ValueError("OBJ path is required")

    obj_path = options.obj_path.resolve()
    if not obj_path.exists():
        raise FileNotFoundError(obj_path)

    vertices_f, texcoords_f, triangles, wire_primitives, materials, triangulation_report = parse_obj(obj_path)
    texture_path = choose_texture_path(options.texture, triangles, materials)
    texture_gamma_enabled = bool(options.gamma_correct_texture)
    color_lut = build_color_lut(DEFAULT_TEXTURE_GAMMA) if texture_gamma_enabled else None
    if texture_path is None:
        texture_width = 1
        texture_height = 1
        texture_pixels = [(255, 255, 255)]
        has_texture = False
    else:
        texture_width, texture_height, texture_pixels = load_bmp(texture_path, color_lut=color_lut)
        texture_width, texture_height, texture_pixels = resize_pixels(
            texture_pixels,
            texture_width,
            texture_height,
            max(1, int(options.texture_max_width)),
            max(1, int(options.texture_max_height)),
        )
        has_texture = True

    vertices_q = quantize_vertices(vertices_f, max(1, int(options.fit)), bool(options.center))
    uvs_q = quantize_uvs(texcoords_f, texture_width, texture_height) if texcoords_f else []
    face_records = build_face_records(vertices_q, triangles, materials, has_texture)
    wire_records = build_wire_records(vertices_q, wire_primitives, materials)

    mesh_path, texture_out_path = build_output_paths(
        obj_path,
        options.mesh_out,
        options.texture_out,
    )
    mesh_path.parent.mkdir(parents=True, exist_ok=True)
    texture_out_path.parent.mkdir(parents=True, exist_ok=True)

    write_mesh(
        mesh_path,
        vertices_q,
        uvs_q,
        face_records,
        wire_records,
        len(wire_primitives),
        has_texture,
    )
    write_texture(texture_out_path, texture_width, texture_height, texture_pixels)

    if options.preview:
        preview_mesh(vertices_q, triangles, materials, options.preview_mode)

    return ExportResult(
        obj_path=obj_path,
        mesh_path=mesh_path,
        texture_path=texture_out_path,
        vertex_count=len(vertices_q),
        uv_count=len(uvs_q),
        face_count=len(triangles),
        wire_count=len(wire_primitives),
        texture_width=texture_width,
        texture_height=texture_height,
        has_texture=has_texture,
        gamma_corrected_texture=texture_gamma_enabled and has_texture,
        triangulation_report=triangulation_report,
    )


def preview_mesh(vertices, triangles, materials, mode):
    try:
        import pygame
    except ImportError as exc:
        raise RuntimeError("Preview requires pygame on the desktop Python environment") from exc

    pygame.init()
    screen = pygame.display.set_mode((640, 480))
    clock = pygame.time.Clock()
    font = pygame.font.SysFont("Consolas", 16)

    angle = 0.0
    running = True
    wireframe = mode == "wireframe"

    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False
                elif event.key == pygame.K_SPACE:
                    wireframe = not wireframe

        yaw = math.radians(angle)
        pitch = math.radians(angle * 0.65)
        cos_y = math.cos(yaw)
        sin_y = math.sin(yaw)
        cos_x = math.cos(pitch)
        sin_x = math.sin(pitch)

        transformed = []
        for x, y, z in vertices:
            x0 = x * cos_y + z * sin_y
            z0 = -x * sin_y + z * cos_y
            y1 = y * cos_x - z0 * sin_x
            z1 = y * sin_x + z0 * cos_x + 380.0
            transformed.append((x0, y1, z1))

        screen.fill((8, 10, 18))

        faces_to_draw = []
        for tri in triangles:
            v0 = transformed[tri.vertex_indices[0]]
            v1 = transformed[tri.vertex_indices[1]]
            v2 = transformed[tri.vertex_indices[2]]

            e1 = (v1[0] - v0[0], v1[1] - v0[1], v1[2] - v0[2])
            e2 = (v2[0] - v0[0], v2[1] - v0[1], v2[2] - v0[2])
            nx = e1[1] * e2[2] - e1[2] * e2[1]
            ny = e1[2] * e2[0] - e1[0] * e2[2]
            nz = e1[0] * e2[1] - e1[1] * e2[0]
            cx = (v0[0] + v1[0] + v2[0]) / 3.0
            cy = (v0[1] + v1[1] + v2[1]) / 3.0
            cz = (v0[2] + v1[2] + v2[2]) / 3.0
            if nx * cx + ny * cy + nz * cz >= 0.0:
                continue

            length = math.sqrt(nx * nx + ny * ny + nz * nz)
            if length <= 0.0:
                continue
            lambert = max(0.0, -nz / length)
            shade = 0.2 + lambert * 0.8

            base_color = material_rgb565(tri.material_name, materials)
            red = ((base_color >> 11) & 0x1F) * 255 // 31
            green = ((base_color >> 5) & 0x3F) * 255 // 63
            blue = (base_color & 0x1F) * 255 // 31
            color = (
                clamp_byte(int(red * shade)),
                clamp_byte(int(green * shade)),
                clamp_byte(int(blue * shade)),
            )

            projected = []
            for vx, vy, vz in (v0, v1, v2):
                if vz <= 1.0:
                    projected = []
                    break
                projected.append(
                    (
                        int(320 + (vx * 180.0) / vz),
                        int(240 - (vy * 180.0) / vz),
                    )
                )
            if len(projected) == 3:
                faces_to_draw.append((cz, projected, color))

        faces_to_draw.sort(key=lambda item: item[0], reverse=True)
        for _depth, points, color in faces_to_draw:
            if wireframe:
                pygame.draw.polygon(screen, color, points, 1)
            else:
                pygame.draw.polygon(screen, color, points, 0)
                pygame.draw.polygon(screen, (12, 14, 18), points, 1)

        info = font.render("ESC quit  SPACE toggle wire/solid", True, (255, 255, 255))
        screen.blit(info, (12, 12))
        pygame.display.flip()
        clock.tick(60)
        angle += 1.25

    pygame.quit()


def run_gui(initial_options: ExportOptions | None = None) -> int:
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk
    except ImportError as exc:
        raise RuntimeError("GUI mode requires tkinter in the desktop Python environment") from exc

    options = initial_options if initial_options is not None else ExportOptions()

    root = tk.Tk()
    root.title("OBJ to H3D Exporter")
    root.geometry("820x520")
    root.minsize(760, 480)

    header = ttk.Frame(root, padding=(12, 12, 12, 6))
    header.grid(row=0, column=0, sticky="ew")
    header.columnconfigure(0, weight=1)
    header.columnconfigure(1, weight=0)
    ttk.Label(header, text="OBJ to H3D Exporter", font=("Segoe UI Semibold", 16)).grid(
        row=0, column=0, sticky="w"
    )
    ttk.Label(
        header,
        text="Convert textured OBJ assets into Pico-friendly H3DM/H3DT output files.",
    ).grid(row=1, column=0, sticky="w", pady=(2, 0))

    container = ttk.Frame(root, padding=12)
    container.grid(row=1, column=0, sticky="nsew")
    root.columnconfigure(0, weight=1)
    root.rowconfigure(1, weight=1)
    container.columnconfigure(1, weight=1)
    container.rowconfigure(9, weight=1)

    obj_var = tk.StringVar(value="" if options.obj_path is None else str(options.obj_path))
    texture_var = tk.StringVar(value="" if options.texture is None else str(options.texture))
    mesh_out_var = tk.StringVar(value="" if options.mesh_out is None else str(options.mesh_out))
    texture_out_var = tk.StringVar(value="" if options.texture_out is None else str(options.texture_out))
    fit_var = tk.StringVar(value=str(int(options.fit)))
    tex_w_var = tk.StringVar(value=str(int(options.texture_max_width)))
    tex_h_var = tk.StringVar(value=str(int(options.texture_max_height)))
    gamma_correct_texture_var = tk.BooleanVar(value=bool(options.gamma_correct_texture))
    center_var = tk.BooleanVar(value=bool(options.center))
    preview_var = tk.BooleanVar(value=bool(options.preview))
    preview_mode_var = tk.StringVar(value=str(options.preview_mode))

    def append_status(text: str) -> None:
        status_text.configure(state="normal")
        status_text.delete("1.0", tk.END)
        status_text.insert(tk.END, text)
        status_text.configure(state="disabled")

    def open_support_link() -> None:
        try:
            webbrowser.open(BUY_ME_A_COFFEE_URL)
            append_status("Opened support link in your browser")
        except Exception as exc:
            messagebox.showerror("OBJ to H3D Exporter", f"Failed to open link:\n{exc}")

    support = tk.Label(
        header,
        text=BUY_ME_A_COFFEE_LABEL,
        fg="#2563eb",
        bg=root.cget("bg"),
        cursor="hand2",
        font=("Segoe UI", 13, "underline"),
    )
    support.grid(row=0, column=1, rowspan=2, sticky="e")
    support.bind("<Button-1>", lambda _event: open_support_link())

    def set_default_outputs() -> None:
        raw_path = obj_var.get().strip()
        if not raw_path:
            return
        obj_path = Path(raw_path)
        default_mesh, default_texture = build_output_paths(obj_path, None, None)
        if not mesh_out_var.get().strip():
            mesh_out_var.set(str(default_mesh))
        if not texture_out_var.get().strip():
            texture_out_var.set(str(default_texture))

    def browse_obj() -> None:
        filename = filedialog.askopenfilename(
            title="Select OBJ file",
            filetypes=(("OBJ files", "*.obj"), ("All files", "*.*")),
        )
        if not filename:
            return
        obj_var.set(filename)
        mesh_out_var.set("")
        texture_out_var.set("")
        set_default_outputs()

    def browse_texture() -> None:
        filename = filedialog.askopenfilename(
            title="Select BMP texture override",
            filetypes=(("BMP files", "*.bmp"), ("All files", "*.*")),
        )
        if filename:
            texture_var.set(filename)

    def browse_mesh_out() -> None:
        filename = filedialog.asksaveasfilename(
            title="Select H3DM output",
            defaultextension=".h3dm",
            filetypes=(("H3DM files", "*.h3dm"), ("All files", "*.*")),
        )
        if filename:
            mesh_out_var.set(filename)

    def browse_texture_out() -> None:
        filename = filedialog.asksaveasfilename(
            title="Select H3DT output",
            defaultextension=".h3dt",
            filetypes=(("H3DT files", "*.h3dt"), ("All files", "*.*")),
        )
        if filename:
            texture_out_var.set(filename)

    def build_options() -> ExportOptions:
        obj_path_raw = obj_var.get().strip()
        if not obj_path_raw:
            raise ValueError("Select an OBJ file first")

        texture_raw = texture_var.get().strip()
        mesh_out_raw = mesh_out_var.get().strip()
        texture_out_raw = texture_out_var.get().strip()

        return ExportOptions(
            obj_path=Path(obj_path_raw),
            mesh_out=Path(mesh_out_raw) if mesh_out_raw else None,
            texture_out=Path(texture_out_raw) if texture_out_raw else None,
            texture=Path(texture_raw) if texture_raw else None,
            fit=int(fit_var.get().strip()),
            texture_max_width=int(tex_w_var.get().strip()),
            texture_max_height=int(tex_h_var.get().strip()),
            gamma_correct_texture=bool(gamma_correct_texture_var.get()),
            center=bool(center_var.get()),
            preview=bool(preview_var.get()),
            preview_mode=preview_mode_var.get().strip() or "solid",
        )

    def on_export() -> None:
        try:
            result = run_export(build_options())
        except Exception as exc:
            append_status(f"Error:\n{exc}")
            messagebox.showerror("OBJ to H3D Exporter", str(exc), parent=root)
            return

        summary = format_export_result(result)
        append_status(summary)
        messagebox.showinfo("OBJ to H3D Exporter", "Export complete", parent=root)

    def clear_texture_override() -> None:
        texture_var.set("")

    row = 0
    ttk.Label(container, text="OBJ file").grid(row=row, column=0, sticky="w", pady=(0, 6))
    ttk.Entry(container, textvariable=obj_var).grid(row=row, column=1, sticky="ew", padx=(8, 8), pady=(0, 6))
    ttk.Button(container, text="Browse...", command=browse_obj).grid(row=row, column=2, sticky="ew", pady=(0, 6))

    row += 1
    ttk.Label(container, text="Texture override").grid(row=row, column=0, sticky="w", pady=(0, 6))
    ttk.Entry(container, textvariable=texture_var).grid(row=row, column=1, sticky="ew", padx=(8, 8), pady=(0, 6))
    texture_button_bar = ttk.Frame(container)
    texture_button_bar.grid(row=row, column=2, sticky="ew", pady=(0, 6))
    ttk.Button(texture_button_bar, text="Browse...", command=browse_texture).grid(row=0, column=0, sticky="ew")
    ttk.Button(texture_button_bar, text="Clear", command=clear_texture_override).grid(row=0, column=1, sticky="ew", padx=(6, 0))

    row += 1
    ttk.Label(container, text="Mesh output").grid(row=row, column=0, sticky="w", pady=(0, 6))
    ttk.Entry(container, textvariable=mesh_out_var).grid(row=row, column=1, sticky="ew", padx=(8, 8), pady=(0, 6))
    ttk.Button(container, text="Save as...", command=browse_mesh_out).grid(row=row, column=2, sticky="ew", pady=(0, 6))

    row += 1
    ttk.Label(container, text="Texture output").grid(row=row, column=0, sticky="w", pady=(0, 6))
    ttk.Entry(container, textvariable=texture_out_var).grid(row=row, column=1, sticky="ew", padx=(8, 8), pady=(0, 6))
    ttk.Button(container, text="Save as...", command=browse_texture_out).grid(row=row, column=2, sticky="ew", pady=(0, 6))

    row += 1
    settings = ttk.LabelFrame(container, text="Export settings", padding=10)
    settings.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(8, 8))
    for index in range(4):
        settings.columnconfigure(index, weight=1 if index % 2 else 0)

    ttk.Label(settings, text="Fit").grid(row=0, column=0, sticky="w")
    ttk.Entry(settings, textvariable=fit_var, width=10).grid(row=0, column=1, sticky="ew", padx=(6, 12))
    ttk.Label(settings, text="Texture max width").grid(row=0, column=2, sticky="w")
    ttk.Entry(settings, textvariable=tex_w_var, width=10).grid(row=0, column=3, sticky="ew", padx=(6, 0))

    ttk.Label(settings, text="Texture max height").grid(row=1, column=0, sticky="w", pady=(8, 0))
    ttk.Entry(settings, textvariable=tex_h_var, width=10).grid(row=1, column=1, sticky="ew", padx=(6, 12), pady=(8, 0))
    ttk.Checkbutton(settings, text="Center mesh", variable=center_var).grid(row=1, column=2, sticky="w", pady=(8, 0))
    ttk.Checkbutton(
        settings,
        text="Gamma-correct BMP texture (2.2)",
        variable=gamma_correct_texture_var,
    ).grid(row=1, column=3, sticky="w", pady=(8, 0))

    ttk.Checkbutton(settings, text="Preview after export", variable=preview_var).grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))
    ttk.Label(settings, text="Preview mode").grid(row=2, column=2, sticky="w", pady=(8, 0))
    preview_mode_box = ttk.Combobox(settings, textvariable=preview_mode_var, values=("solid", "wireframe"), state="readonly")
    preview_mode_box.grid(row=2, column=3, sticky="ew", padx=(6, 0), pady=(8, 0))

    row += 1
    button_bar = ttk.Frame(container)
    button_bar.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(0, 8))
    button_bar.columnconfigure(0, weight=1)
    button_bar.columnconfigure(1, weight=1)
    ttk.Button(button_bar, text="Export", command=on_export).grid(row=0, column=0, sticky="ew")
    ttk.Button(button_bar, text="Close", command=root.destroy).grid(row=0, column=1, sticky="ew", padx=(8, 0))

    row += 1
    ttk.Label(container, text="Status").grid(row=row, column=0, sticky="w")

    row += 1
    status_text = tk.Text(container, height=10, wrap="word")
    status_text.grid(row=row, column=0, columnspan=3, sticky="nsew")
    status_text.configure(state="disabled")

    set_default_outputs()
    append_status("Select an OBJ file, adjust options if needed, then export.")

    root.mainloop()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert OBJ + BMP texture to H3DM/H3DT")
    parser.add_argument("obj", type=Path, nargs="?", help="Input OBJ path")
    parser.add_argument("--mesh-out", type=Path, default=None, help="Output H3DM path")
    parser.add_argument("--texture-out", type=Path, default=None, help="Output H3DT path")
    parser.add_argument(
        "--gui",
        action="store_true",
        help="Launch the graphical exporter UI",
    )
    parser.add_argument(
        "--texture",
        type=Path,
        default=None,
        help="Override diffuse BMP texture path",
    )
    parser.add_argument(
        "--fit",
        type=int,
        default=96,
        help="Scale the mesh so the largest centered axis fits within +/- FIT units",
    )
    parser.add_argument(
        "--texture-max-width",
        type=int,
        default=64,
        help="Maximum exported texture width",
    )
    parser.add_argument(
        "--texture-max-height",
        type=int,
        default=64,
        help="Maximum exported texture height",
    )
    parser.add_argument(
        "--gamma-correct-texture",
        action="store_true",
        help="Apply a 2.2 gamma curve to the incoming BMP texture before RGB565 export",
    )
    parser.add_argument(
        "--no-center",
        action="store_true",
        help="Keep the original model origin instead of recentering before quantization",
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Preview the quantized mesh on the desktop with pygame after export",
    )
    parser.add_argument(
        "--preview-mode",
        choices=("solid", "wireframe"),
        default="solid",
        help="Desktop preview mode",
    )
    args = parser.parse_args()

    options = ExportOptions(
        obj_path=args.obj,
        mesh_out=args.mesh_out,
        texture_out=args.texture_out,
        texture=args.texture,
        fit=int(args.fit),
        texture_max_width=int(args.texture_max_width),
        texture_max_height=int(args.texture_max_height),
        gamma_correct_texture=bool(args.gamma_correct_texture),
        center=not args.no_center,
        preview=bool(args.preview),
        preview_mode=args.preview_mode,
    )

    if args.gui or args.obj is None:
        return run_gui(options)

    result = run_export(options)
    print(format_export_result(result))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())