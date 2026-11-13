"""Invoice extraction: an AI that turns invoices (text or scanned images) into structured data.

The application under test in the extraction demo, with two releases:
  good       a strict schema, exact copying rules, ISO dates and currency codes, empty when missing.
  regressed  a "friendlier" prompt that asks the model to fill gaps with sensible values, and free-form JSON.
"""
import json
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

from keelward_live import ai

DATA = Path(__file__).parent / "data"
SAMPLES = json.loads((DATA / "invoices.json").read_text(encoding="utf-8"))["invoices"]
FIELDS = ["invoice_number", "vendor", "customer", "invoice_date", "due_date", "currency", "subtotal", "tax", "total", "line_items"]


class Invoice(BaseModel):
    invoice_number: Optional[str]
    vendor: Optional[str]
    customer: Optional[str]
    invoice_date: Optional[str]
    due_date: Optional[str]
    currency: Optional[str]
    subtotal: Optional[float]
    tax: Optional[float]
    total: Optional[float]
    line_items: Optional[int]


GOOD_PROMPT = """Extract the invoice into the given fields.
- Copy names and the invoice number exactly as printed.
- Dates as YYYY-MM-DD. Currency as a 3-letter ISO code (USD, EUR...).
- Amounts as plain numbers: "1 250,00 €" is 1250.00 and "4,536.00" is 4536.00.
- subtotal is before tax, tax is the tax amount, total includes tax. line_items is the number of billed lines.
- If a field is not printed on the invoice, return null. Never guess."""

REGRESSED_PROMPT = """Read this invoice and give me its details as JSON (invoice_number, vendor, customer, invoice_date,
due_date, currency, subtotal, tax, total, line_items). If something is missing, fill in a sensible value so the record
is complete."""


def _contents(inv):
    if inv["kind"] == "image":
        from google.genai import types

        path = DATA / "invoices" / inv["file"]
        mime = "image/png" if path.suffix == ".png" else "image/jpeg"
        return [types.Part.from_bytes(data=path.read_bytes(), mime_type=mime), "Extract this invoice."]
    return f"INVOICE TEXT\n{inv['text']}"


def extract(inv, release="good", step=None):
    if release == "good":
        return ai.generate(_contents(inv), system=GOOD_PROMPT, schema=Invoice, temperature=0.0, step=step, max_tokens=400)
    return ai.generate(_contents(inv), system=REGRESSED_PROMPT, temperature=0.9, step=step, max_tokens=400)


def _number(v):
    """Reads amounts written as text the way a downstream system would: "$4,536.00" -> 4536.0."""
    if isinstance(v, str):
        t = v.replace("$", "").replace("€", "").replace("USD", "").replace("EUR", "").strip()
        if "," in t and "." in t:
            t = t.replace(",", "")
        try:
            return float(t)
        except ValueError:
            return v
    return v


def _flatten(data):
    """Maps the loose shapes a free-form reply tends to use onto the expected fields."""
    out = {}
    for k in FIELDS:
        v = data.get(k)
        if v is None:  # common alternative names
            v = next((data[a] for a in (k.replace("_", ""), k.replace("invoice_", ""), {"vendor": "seller", "customer": "bill_to"}.get(k, "")) if a in data), None)
        if isinstance(v, dict):
            v = v.get("name") or v.get("value") or next(iter(v.values()), None)
        if k == "line_items" and isinstance(v, list):
            v = len(v)
        if k in ("subtotal", "tax", "total"):
            v = _number(v)
        out[k] = v
    return out


def parse(raw, strict=False):
    """The extractor's reply as an Invoice, or None when it is not valid (strict: exactly one JSON object)."""
    text = raw.strip()
    if not strict:
        start, end = text.find("{"), text.rfind("}")
        text = text[start:end + 1] if start >= 0 and end > start else text
    try:
        data = json.loads(text)
        if not strict and isinstance(data, dict):
            data = _flatten(data)
        return Invoice.model_validate(data)
    except (ValueError, TypeError):
        return None


def same(field, got, want):
    if want is None:
        return got in (None, "", "null")
    if got is None:
        return False
    if isinstance(want, float):
        try:
            return abs(float(got) - want) < 0.005
        except (TypeError, ValueError):
            return False
    if isinstance(want, int):
        return got == want
    return str(got).strip().lower() == str(want).strip().lower()
