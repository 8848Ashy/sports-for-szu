"""Export generated artwork into Windows ICO sizes; no visual redesign."""
from pathlib import Path
from PIL import Image

root = Path(__file__).resolve().parents[1]
image = Image.open(root / 'sports_szu/assets/noodles.png')
image.save(root / 'sports_szu/assets/noodles.ico', sizes=[
    (16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
