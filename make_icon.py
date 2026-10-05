"""Genera build/icon.ico (Windows) y build/icon.png (Mac) a partir de assets/."""
from pathlib import Path

from PIL import Image

root = Path(__file__).parent
out = root / "build"
out.mkdir(exist_ok=True)
logo = Image.open(root / "assets" / "logo.png").convert("RGBA")
small = Image.open(root / "assets" / "tray-activo.png").convert("RGBA")

# Windows: en tamaños pequeños (16-32) se usa la versión simplificada, que se lee mejor
frames = []
for s in (16, 24, 32, 48, 64, 128, 256):
    src = small if s <= 32 else logo
    frames.append(src.resize((s, s), Image.LANCZOS))
frames[-1].save(out / "icon.ico", format="ICO", sizes=[f.size for f in frames],
                append_images=frames[:-1])

# Mac: los iconos llevan margen alrededor
canvas = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
inner = logo.resize((824, 824), Image.LANCZOS)
canvas.paste(inner, (100, 100), inner)
canvas.save(out / "icon.png")
print("build/icon.ico y build/icon.png generados")
