"""Dibuja el icono de TouchPad (matriz de puntos 3x3)."""
from PIL import Image, ImageDraw

BLUE = (47, 150, 255, 255)
DIM = (91, 100, 114, 255)
# 0 = punto apagado, 1 = encendido, 2 = encendido y grande (centro)
PATTERN = [1, 1, 0,
           1, 2, 1,
           0, 1, 0]


def draw_icon(size=256, active=True):
    s = size * 4  # dibujar grande y reducir = bordes suaves
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    bg = (27, 42, 64, 255) if active else (44, 44, 50, 255)
    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=int(s * 0.27), fill=bg)
    for i, v in enumerate(PATTERN):
        cx = s * (0.3 + 0.2 * (i % 3))
        cy = s * (0.3 + 0.2 * (i // 3))
        r = s * (0.088 if v == 2 else 0.066)
        if active:
            col = BLUE if v else DIM
        else:
            col = (160, 165, 175, 255) if v else (84, 88, 96, 255)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=col)
    return img.resize((size, size), Image.LANCZOS)
