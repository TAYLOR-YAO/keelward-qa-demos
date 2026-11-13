"""Lists which Gemini models answer on Vertex AI for this project (used when picking or updating models)."""
import sys
import time

from google import genai
from google.genai import types

CANDIDATES = sys.argv[1:] or ["gemini-3.1-flash-lite", "gemini-3-flash", "gemini-3-flash-preview", "gemini-2.5-flash", "gemini-2.5-flash-lite"]
for location in ("global", "us-central1"):
    c = genai.Client(vertexai=True, project="keelward-web-50bc7", location=location)
    for m in CANDIDATES:
        start = time.monotonic()
        try:
            r = c.models.generate_content(model=m, contents="Reply with the single word: ok", config=types.GenerateContentConfig(temperature=0, max_output_tokens=20))
            u = r.usage_metadata
            print(f"OK   {location:12} {m:28} {int((time.monotonic()-start)*1000):5} ms  in={u.prompt_token_count} out={u.candidates_token_count} thoughts={getattr(u, 'thoughts_token_count', None)} -> {r.text!r}")
        except Exception as e:
            print(f"FAIL {location:12} {m:28} {str(e)[:110]}")
