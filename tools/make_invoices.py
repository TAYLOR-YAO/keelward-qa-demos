"""Renders the two scanned sample invoices (C and D) from apps/data/invoices.json. Run once; the images are committed."""
import json
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

DATA = Path(__file__).resolve().parent.parent / "apps" / "data"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"

for inv in json.loads((DATA / "invoices.json").read_text(encoding="utf-8"))["invoices"]:
    if inv["kind"] != "image":
        continue
    img = Image.new("RGB", (900, 120 + 34 * len(inv["lines"])), (252, 251, 247))
    d = ImageDraw.Draw(img)
    y = 50
    for i, line in enumerate(inv["lines"]):
        font = ImageFont.truetype(BOLD if i < 2 else FONT, 30 if i == 0 else 21)
        d.text((60, y), line, font=font, fill=(30, 30, 35))
        y += 44 if i == 0 else 34
    if inv["id"] == "D":  # a phone photo of a crumpled page: rotated, grainy, soft, heavily compressed
        random.seed(4)
        img = img.rotate(2.6, expand=True, fillcolor=(214, 210, 200), resample=Image.BICUBIC)
        px = img.load()
        for _ in range(9000):
            x, yy = random.randrange(img.width), random.randrange(img.height)
            g = random.randint(150, 230)
            px[x, yy] = (g, g, g)
        img = img.filter(ImageFilter.GaussianBlur(0.9)).resize((img.width * 3 // 4, img.height * 3 // 4))
        img.save(DATA / "invoices" / inv["file"], quality=38)
    else:
        img.save(DATA / "invoices" / inv["file"])
    print("wrote", inv["file"], img.size)
