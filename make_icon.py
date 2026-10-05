"""Genera build/icon.ico para el .exe."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "touchpad"))
from icon import draw_icon  # noqa: E402

out = Path("build")
out.mkdir(exist_ok=True)
draw_icon(256).save(out / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                                             (64, 64), (128, 128), (256, 256)])
print("build/icon.ico generado")
