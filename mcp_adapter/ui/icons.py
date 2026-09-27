"""Extract the real application icon from an installed executable (Windows) and encode it as PNG.

No third-party packages: the icon is read through the Win32 API with ctypes and written as a minimal
RGBA PNG with zlib. On other platforms (or when extraction fails) ``icon_data_uri`` returns None and the
UI falls back to a monogram, so nothing trademarked is ever shipped with the package.
"""
from __future__ import annotations

import base64
import os
import struct
import zlib
from pathlib import Path

_CACHE: dict[tuple[str, float, int], str | None] = {}


def _png_from_rgba(width: int, height: int, rgba: bytes) -> bytes:
    stride = width * 4
    raw = b"".join(b"\x00" + rgba[y * stride:(y + 1) * stride] for y in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def _extract_windows(exe: str, size: int) -> bytes | None:  # pragma: no cover - exercised only on Windows
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    class ICONINFO(ctypes.Structure):
        _fields_ = [("fIcon", wintypes.BOOL), ("xHotspot", wintypes.DWORD), ("yHotspot", wintypes.DWORD),
                    ("hbmMask", wintypes.HBITMAP), ("hbmColor", wintypes.HBITMAP)]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                    ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]

    # 64-bit handles must be declared, otherwise ctypes truncates them to C int.
    user32.PrivateExtractIconsW.argtypes = [wintypes.LPCWSTR, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                            ctypes.POINTER(wintypes.HICON), ctypes.POINTER(wintypes.UINT),
                                            wintypes.UINT, wintypes.UINT]
    user32.PrivateExtractIconsW.restype = wintypes.UINT
    user32.GetIconInfo.argtypes = [wintypes.HICON, ctypes.POINTER(ICONINFO)]
    user32.GetIconInfo.restype = wintypes.BOOL
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.ReleaseDC.restype = ctypes.c_int
    user32.DestroyIcon.argtypes = [wintypes.HICON]
    user32.DestroyIcon.restype = wintypes.BOOL
    gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT, ctypes.c_void_p,
                                ctypes.POINTER(BITMAPINFO), wintypes.UINT]
    gdi32.GetDIBits.restype = ctypes.c_int
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteObject.restype = wintypes.BOOL
    hicon = wintypes.HICON()
    icon_id = wintypes.UINT()
    got = user32.PrivateExtractIconsW(exe, 0, size, size, ctypes.byref(hicon), ctypes.byref(icon_id), 1, 0)
    if got == 0 or not hicon.value:
        return None
    try:
        info = ICONINFO()
        if not user32.GetIconInfo(hicon, ctypes.byref(info)):
            return None
        try:
            hdc = user32.GetDC(None)
            try:
                def dib(hbm: int, w: int, h: int) -> bytes | None:
                    bmi = BITMAPINFO()
                    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
                    bmi.bmiHeader.biWidth = w
                    bmi.bmiHeader.biHeight = -h  # top-down
                    bmi.bmiHeader.biPlanes = 1
                    bmi.bmiHeader.biBitCount = 32
                    bmi.bmiHeader.biCompression = 0  # BI_RGB
                    buf = ctypes.create_string_buffer(w * h * 4)
                    if gdi32.GetDIBits(hdc, hbm, 0, h, buf, ctypes.byref(bmi), 0) == 0:
                        return None
                    return buf.raw

                color = dib(info.hbmColor, size, size) if info.hbmColor else None
                if color is None:
                    return None
                px = bytearray(color)
                has_alpha = any(px[i + 3] for i in range(0, len(px), 4))
                if not has_alpha and info.hbmMask:
                    mask = dib(info.hbmMask, size, size)
                    for i in range(0, len(px), 4):
                        px[i + 3] = 0 if (mask and mask[i]) else 255
                elif not has_alpha:
                    for i in range(0, len(px), 4):
                        px[i + 3] = 255
                for i in range(0, len(px), 4):  # BGRA -> RGBA
                    px[i], px[i + 2] = px[i + 2], px[i]
                return _png_from_rgba(size, size, bytes(px))
            finally:
                user32.ReleaseDC(None, hdc)
        finally:
            if info.hbmColor:
                gdi32.DeleteObject(info.hbmColor)
            if info.hbmMask:
                gdi32.DeleteObject(info.hbmMask)
    finally:
        user32.DestroyIcon(hicon)


def extract_icon_png(executable: str | os.PathLike, size: int = 64) -> bytes | None:
    """PNG bytes of the executable's icon, or None when unavailable (non-Windows, missing file, no icon)."""
    if os.name != "nt":
        return None
    path = Path(executable)
    if not path.is_file():
        return None
    try:
        return _extract_windows(str(path), size)
    except Exception:  # any Win32 failure just means "no icon"
        return None


def icon_data_uri(executable: str | os.PathLike, size: int = 64) -> str | None:
    """Cached data: URI (image/png) for the executable's icon, or None."""
    try:
        p = Path(executable)
        key = (str(p), p.stat().st_mtime if p.exists() else 0.0, size)
    except OSError:
        return None
    if key not in _CACHE:
        png = extract_icon_png(p, size)
        _CACHE[key] = ("data:image/png;base64," + base64.b64encode(png).decode("ascii")) if png else None
    return _CACHE[key]
