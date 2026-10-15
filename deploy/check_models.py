"""Find which Gemini model IDs this project can call on Vertex AI (global endpoint)."""
import os, sys
from google import genai

project = os.environ.get("GOOGLE_CLOUD_PROJECT") or sys.argv[1]
client = genai.Client(vertexai=True, project=project, location="global")
ok = []
for m in ["gemini-3-flash-preview", "gemini-3.1-flash-preview", "gemini-3.1-pro-preview", "gemini-3-pro-preview",
          "gemini-flash-latest", "gemini-2.5-flash", "gemini-2.5-pro"]:
    try:
        r = client.models.generate_content(model=m, contents="Reply with the single word OK.")
        print(f"OK   {m}: {r.text.strip()[:20]}")
        ok.append(m)
    except Exception as e:
        print(f"FAIL {m}: {type(e).__name__}: {str(e)[:120]}")
print("FIRST_OK=" + (ok[0] if ok else ""))
