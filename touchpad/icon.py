"""Icono de TouchPad para la bandeja del sistema (activo en azul, inactivo en gris)."""
import io

from PIL import Image

import brand


def draw_icon(size=64, active=True):
    data = brand.png("TRAY_ON_64" if active else "TRAY_OFF_64")
    img = Image.open(io.BytesIO(data)).convert("RGBA")
    return img if img.size == (size, size) else img.resize((size, size), Image.LANCZOS)
