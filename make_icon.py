"""Genera build/icon.ico (Windows) y build/icon.png (Mac)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "touchpad"))
from icon import draw_icon  # noqa: E402

out = Path("build")
out.mkdir(exist_ok=True)
img = draw_icon(512)
img.save(out / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                                  (64, 64), (128, 128), (256, 256)])
# En Mac los iconos llevan margen alrededor del cuadrado redondeado
mac = img.copy().resize((824, 824))
from PIL import Image  # noqa: E402
canvas = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
canvas.paste(mac, (100, 100), mac)
canvas.save(out / "icon.png")
print("build/icon.ico y build/icon.png generados")
