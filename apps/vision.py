"""Image and video AI: three small applications under test in the "Image and video AI" demo.

  check_catalog   a visual QA assistant that looks at a product-catalog screenshot and reports broken photos.
  moderate        a listing-photo moderator for a marketplace (no personal data, no off-platform contact).
  describe_video  a video reviewer that watches a screen recording and lists what happened, with timestamps.

Each has two releases:
  good       structured output, explicit rules, "only report what you can see".
  regressed  cost and "simplicity" changes: small thumbnails and "does it look OK?", a moderation prompt that
             only checks for nudity or violence, and "write an engaging summary".
"""
import io
import json
from typing import List, Literal, Optional

from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel

from keelward_live import ai

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def _font(size, bold=False):
    for path in ((BOLD if bold else FONT), "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def _part(data, mime):
    from google.genai import types

    return types.Part.from_bytes(data=data, mime_type=mime)


def _json(raw):
    t = raw.strip()
    a, b = t.find("{"), t.rfind("}")
    try:
        return json.loads(t[a:b + 1]) if a >= 0 and b > a else None
    except ValueError:
        return None


# ---- 1. Visual catalog check -----------------------------------------------------------------------

class CatalogCheck(BaseModel):
    products_visible: int
    distinct_photos: int
    issues: List[str]
    verdict: Literal["pass", "fail"]


CATALOG_GOOD = """You are a visual QA assistant for an online store. Look at the screenshot of the product catalog.
Count the products and how many DIFFERENT product photos are shown. Report as issues only clear defects:
- the same photo repeated for different products,
- a missing, blank or broken image,
- a photo of a completely different kind of product (for example a dog photo for a backpack).
Do not judge style details: any clothing photo is fine for a clothing item (T-shirt, onesie, jacket, sweatshirt).
verdict is "fail" if there is any issue, otherwise "pass"."""

CATALOG_REGRESSED = """Take a quick look at this store page. Does it look OK? Reply in JSON:
{"products_visible": n, "distinct_photos": n, "issues": [], "verdict": "pass" or "fail"}"""


def thumbnail(png, width=260):
    """Release 2 sends a small thumbnail instead of the full screenshot, to cut costs."""
    img = Image.open(io.BytesIO(png)).convert("RGB")
    img = img.resize((width, max(1, img.height * width // img.width)))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=40)
    return buf.getvalue()


def check_catalog(png, release="good", step=None):
    if release == "good":
        raw = ai.generate([_part(png, "image/png"), "Check this catalog."], system=CATALOG_GOOD, schema=CatalogCheck, step=step, max_tokens=500)
    else:
        raw = ai.generate([_part(thumbnail(png), "image/jpeg"), "Check this page."], system=CATALOG_REGRESSED, temperature=0.9, step=step, max_tokens=300)
    data = _json(raw)
    try:
        return CatalogCheck.model_validate(data) if data else None, raw
    except ValueError:
        return None, raw


# ---- 2. Listing-photo moderation -------------------------------------------------------------------

class Moderation(BaseModel):
    allowed: bool
    reasons: List[str]
    category: Literal["ok", "personal-data", "off-platform-contact", "other"]


MODERATION_GOOD = """You moderate photos for a handmade-goods marketplace. Apply this policy strictly:
- personal-data: the image shows payment card numbers, ID or account numbers, or a person's private details.
- off-platform-contact: the image shows a phone number, email, website or social handle to buy outside the platform.
- other: anything else that breaks marketplace rules.
A normal product photo, even with text such as a product name, price tag or care instructions, is "ok" and allowed.
Read all text in the image before deciding."""

MODERATION_REGRESSED = """Check that this image contains no nudity, violence or gore. Reply in JSON:
{"allowed": true or false, "reasons": []}"""

SAMPLES = [
    {"id": "M1", "label": "Product photo: ceramic mug", "allowed": True, "category": "ok"},
    {"id": "M2", "label": "Product photo with a phone number to buy directly", "allowed": False, "category": "off-platform-contact"},
    {"id": "M3", "label": "Photo showing a full payment card number", "allowed": False, "category": "personal-data"},
    {"id": "M4", "label": "Handwritten-style care note", "allowed": True, "category": "ok"},
]


def sample_image(sample_id):
    """Draws the moderation samples (no real people, brands or data; the card uses a published test number)."""
    img = Image.new("RGB", (800, 560), (246, 242, 234))
    d = ImageDraw.Draw(img)
    if sample_id in ("M1", "M2"):
        d.rounded_rectangle((250, 120, 520, 420), radius=40, fill=(68, 112, 160))           # mug body
        d.arc((470, 190, 620, 350), start=-80, end=80, fill=(68, 112, 160), width=28)        # handle
        d.ellipse((250, 95, 520, 150), fill=(92, 140, 190))
        d.text((400, 470), "Handmade ceramic mug - 12 oz", font=_font(30, True), fill=(40, 40, 40), anchor="mm")
        if sample_id == "M2":
            d.rectangle((90, 500, 710, 548), fill=(255, 236, 120))
            d.text((400, 524), "Skip the fees! Call 404-555-0137 to buy directly", font=_font(24, True), fill=(20, 20, 20), anchor="mm")
    elif sample_id == "M3":
        d.rounded_rectangle((130, 110, 670, 450), radius=26, fill=(30, 60, 110))
        d.rectangle((180, 190, 260, 250), fill=(212, 175, 55))                               # chip
        d.text((180, 300), "4111 1111 1111 1111", font=_font(40, True), fill=(240, 240, 240))
        d.text((180, 370), "VALID THRU 08/29      JANE SAMPLE", font=_font(22), fill=(220, 220, 220))
        d.text((400, 510), "Selling my old wallet - card included in photo", font=_font(24), fill=(40, 40, 40), anchor="mm")
    else:
        d.rectangle((150, 80, 650, 480), fill=(255, 252, 240), outline=(200, 190, 170), width=3)
        for i, line in enumerate(["Thank you for your order!", "Hand wash only.", "Do not microwave.", "- Studio Clay & Co"]):
            d.text((190, 140 + i * 70), line, font=_font(32), fill=(60, 50, 90))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def moderate(png, release="good", step=None):
    if release == "good":
        raw = ai.generate([_part(png, "image/png"), "Moderate this listing photo."], system=MODERATION_GOOD, schema=Moderation, step=step, max_tokens=300)
    else:
        raw = ai.generate([_part(png, "image/png"), "Check this image."], system=MODERATION_REGRESSED, temperature=0.9, step=step, max_tokens=300)
    data = _json(raw)
    if data is not None and "category" not in data:
        data["category"] = "ok" if data.get("allowed") else "other"
    try:
        return Moderation.model_validate(data) if data else None, raw
    except ValueError:
        return None, raw


# ---- 3. Video review --------------------------------------------------------------------------------

class Event(BaseModel):
    seconds: float
    event: Literal["signed-in", "added-to-cart", "opened-checkout", "error-shown", "order-completed", "other"]
    note: str


class VideoReview(BaseModel):
    events: List[Event]
    error_message: Optional[str]
    order_completed: bool
    summary: str


VIDEO_GOOD = """You review screen recordings of web tests. Watch the whole video and list the key events you SEE,
with the time in seconds from the start of the video: signed-in (the product list appears), added-to-cart, opened-checkout
(the checkout information form appears), error-shown, order-completed. Copy any error message exactly as shown.
Only report events that are visible in the video; never assume an outcome."""

VIDEO_REGRESSED = """Write an engaging summary of this shopping video for a marketing report, with a timeline of the
customer's journey in JSON: {"events": [{"seconds": n, "event": "signed-in|added-to-cart|opened-checkout|error-shown|order-completed|other",
"note": ""}], "error_message": null, "order_completed": true or false, "summary": ""}"""


def describe_video(webm, release="good", step=None):
    if release == "good":
        raw = ai.generate([_part(webm, "video/webm"), "Review this recording."], system=VIDEO_GOOD, schema=VideoReview, step=step, max_tokens=800)
    else:
        raw = ai.generate([_part(webm, "video/webm"), "Summarize this video."], system=VIDEO_REGRESSED, temperature=0.9, step=step, max_tokens=800)
    data = _json(raw)
    try:
        return VideoReview.model_validate(data) if data else None, raw
    except ValueError:
        return None, raw
