"""Generate the evmedia app icon: a rounded blue tile with a white play triangle.

Pure stdlib (zlib + struct) — no Pillow on this machine. Rendered at 4x and box-filtered
down, which is enough anti-aliasing for an icon this simple.
"""

import os
import struct
import zlib

SIZES = [16, 32, 48, 64, 128, 256]
SS = 4  # supersample factor

TOP = (0x3B, 0x82, 0xF6)  # blue-500
BOTTOM = (0x1D, 0x4E, 0xD8)  # blue-700
WHITE = (0xFF, 0xFF, 0xFF)


def inside_rounded(x, y, size, radius):
    if x < 0 or y < 0 or x >= size or y >= size:
        return False
    # Distance to the nearest corner circle, if in a corner quadrant.
    cx = radius if x < radius else (size - radius if x > size - radius else x)
    cy = radius if y < radius else (size - radius if y > size - radius else y)
    dx, dy = x - cx, y - cy
    return dx * dx + dy * dy <= radius * radius


def inside_triangle(x, y, size):
    ax, ay = 0.38 * size, 0.27 * size
    bx, by = 0.38 * size, 0.73 * size
    cx, cy = 0.73 * size, 0.50 * size
    d1 = (x - bx) * (ay - by) - (ax - bx) * (y - by)
    d2 = (x - cx) * (by - cy) - (bx - cx) * (y - cy)
    d3 = (x - ax) * (cy - ay) - (cx - ax) * (y - ay)
    has_neg = d1 < 0 or d2 < 0 or d3 < 0
    has_pos = d1 > 0 or d2 > 0 or d3 > 0
    return not (has_neg and has_pos)


def render(size):
    """Return RGBA rows top-down."""
    big = size * SS
    radius = 0.22 * big
    # Accumulate supersampled coverage per output pixel.
    rows = []
    for py in range(size):
        row = []
        for px in range(size):
            r = g = b = 0.0
            covered = 0
            for sy in range(SS):
                for sx in range(SS):
                    x = px * SS + sx + 0.5
                    y = py * SS + sy + 0.5
                    if not inside_rounded(x, y, big, radius):
                        continue
                    if inside_triangle(x, y, big):
                        cr, cg, cb = WHITE
                    else:
                        t = y / big
                        cr = int(TOP[0] + (BOTTOM[0] - TOP[0]) * t)
                        cg = int(TOP[1] + (BOTTOM[1] - TOP[1]) * t)
                        cb = int(TOP[2] + (BOTTOM[2] - TOP[2]) * t)
                    r += cr
                    g += cg
                    b += cb
                    covered += 1
            if covered == 0:
                row.append((0, 0, 0, 0))
            else:
                # Average the colour over the covered subsamples only, and carry the coverage
                # as alpha, so edges fade out instead of darkening toward black.
                row.append((
                    int(round(r / covered)),
                    int(round(g / covered)),
                    int(round(b / covered)),
                    int(round(255 * covered / (SS * SS))),
                ))
        rows.append(row)
    return rows


def bmp_icon_image(rows):
    """One ICO image as a BMP: BGRA bottom-up plus a zeroed AND mask."""
    size = len(rows)
    header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, 0, 0, 0, 0, 0)
    pixels = bytearray()
    for y in range(size - 1, -1, -1):
        for (r, g, b, a) in rows[y]:
            pixels += bytes((b, g, r, a))
    # 1bpp AND mask, rows padded to 4 bytes; all zero because alpha already carries shape.
    mask_stride = ((size + 31) // 32) * 4
    mask = bytes(mask_stride * size)
    return header + bytes(pixels) + mask


def png_bytes(rows):
    size = len(rows)
    raw = bytearray()
    for row in rows:
        raw.append(0)  # filter: none
        for (r, g, b, a) in row:
            raw += bytes((r, g, b, a))

    def chunk(tag, data):
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    out = os.path.join(here, "icons")
    os.makedirs(out, exist_ok=True)

    rendered = {size: render(size) for size in SIZES}

    images = [bmp_icon_image(rendered[size]) for size in SIZES]
    header = struct.pack("<HHH", 0, 1, len(SIZES))
    offset = len(header) + 16 * len(SIZES)
    entries = b""
    for size, image in zip(SIZES, images):
        entries += struct.pack(
            "<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(image), offset
        )
        offset += len(image)
    with open(os.path.join(out, "icon.ico"), "wb") as handle:
        handle.write(header + entries + b"".join(images))

    for size in (32, 128, 256):
        with open(os.path.join(out, "%dx%d.png" % (size, size)), "wb") as handle:
            handle.write(png_bytes(rendered[size]))
    with open(os.path.join(out, "icon.png"), "wb") as handle:
        handle.write(png_bytes(rendered[256]))

    for name in sorted(os.listdir(out)):
        print(name, os.path.getsize(os.path.join(out, name)))


if __name__ == "__main__":
    main()
