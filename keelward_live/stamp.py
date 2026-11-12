"""Adds a capture bar to each screenshot: the exact time (UTC), the run id and the page address,
so anyone looking at the evidence can see when and where it was taken."""
import io
from datetime import datetime, timezone

from PIL import Image, ImageDraw, ImageFont

FONTS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
]
BAR = 38
NAVY, WHITE, MUTED, RED = (15, 39, 66), (255, 255, 255), (159, 179, 200), (214, 69, 54)


def _font(size, bold=False):
    for path in FONTS:
        try:
            return ImageFont.truetype(path.replace("Sans.ttf", "Sans-Bold.ttf") if bold else path, size)
        except OSError:
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default(size=size)


def stamp(png, url, run_id, when=None, quality=60):
    """PNG bytes in, JPEG bytes out, with the capture bar on top."""
    when = when or datetime.now(timezone.utc)
    shot = Image.open(io.BytesIO(png)).convert("RGB")
    w, h = shot.size
    out = Image.new("RGB", (w, h + BAR), NAVY)
    out.paste(shot, (0, BAR))
    d = ImageDraw.Draw(out)
    small, bold = _font(13), _font(13, bold=True)
    x = 12
    label = "LIVE TEST RUN"
    lw = d.textlength(label, font=bold)
    d.rounded_rectangle((x, 9, x + lw + 16, BAR - 9), radius=10, fill=RED)
    d.text((x + 8, BAR / 2), label, font=bold, fill=WHITE, anchor="lm")
    x += lw + 28
    stamp_text = when.strftime("%Y-%m-%d %H:%M:%S UTC")
    d.text((x, BAR / 2), stamp_text, font=bold, fill=WHITE, anchor="lm")
    x += d.textlength(stamp_text, font=bold) + 16
    run_text = f"Keelward run {run_id[:8]}"
    d.text((x, BAR / 2), run_text, font=small, fill=MUTED, anchor="lm")
    x += d.textlength(run_text, font=small) + 20
    # The address, shortened from the left if it does not fit.
    room = w - x - 12
    addr = url
    while addr and d.textlength(addr, font=small) > room:
        addr = "…" + addr[2:]
    if room > 60:
        d.text((w - 12, BAR / 2), addr, font=small, fill=WHITE, anchor="rm")
    buf = io.BytesIO()
    out.save(buf, "JPEG", quality=quality, optimize=True)
    return buf.getvalue()
