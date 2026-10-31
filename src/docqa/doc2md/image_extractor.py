from __future__ import annotations

import struct
from pathlib import Path
from typing import Optional, Tuple

from ..utils import ensure_dir


def guess_image_ext(name: str = "", data: bytes = b"", default: str = ".png") -> str:
    if data.startswith(b"\x89PNG"):
        return ".png"
    if data.startswith(b"\xff\xd8"):
        return ".jpg"
    if data.startswith(b"GIF8"):
        return ".gif"
    if data.startswith(b"BM"):
        return ".bmp"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    suffix = Path(name).suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff", ".emf", ".wmf", ".svg"}:
        return suffix
    return default


def image_dimensions(data: bytes) -> Tuple[int, int]:
    """Read PNG/JPEG/GIF dimensions without Pillow."""
    try:
        if data.startswith(b"\x89PNG") and len(data) >= 24:
            width, height = struct.unpack(">II", data[16:24])
            return int(width), int(height)
        if data.startswith(b"\xff\xd8"):
            index = 2
            while index + 9 < len(data):
                if data[index] != 0xFF:
                    index += 1
                    continue
                marker = data[index + 1]
                if marker in (0xD8, 0xD9):
                    index += 2
                    continue
                length = struct.unpack(">H", data[index + 2 : index + 4])[0]
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    height, width = struct.unpack(">HH", data[index + 5 : index + 9])
                    return int(width), int(height)
                index += 2 + length
        if data.startswith(b"GIF8") and len(data) >= 10:
            width, height = struct.unpack("<HH", data[6:10])
            return int(width), int(height)
    except Exception:
        pass
    return 0, 0


def save_image(data: bytes, dest_dir: str | Path, filename: str) -> Tuple[Path, int, int]:
    ensure_dir(dest_dir)
    dest = Path(dest_dir) / filename
    dest.write_bytes(data)
    width, height = image_dimensions(data)
    return dest, width, height


def copy_image(src: str | Path, dest_dir: str | Path, filename: str) -> Tuple[Path, int, int]:
    data = Path(src).read_bytes()
    return save_image(data, dest_dir, filename)
