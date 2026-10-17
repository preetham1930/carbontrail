"""Runtime configuration (all from environment variables)."""
from __future__ import annotations

import os

PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "")
LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "global")

# Gemini via Vertex AI. ADK and google-genai read these variables directly.
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "True")
os.environ.setdefault("GOOGLE_GENAI_USE_ENTERPRISE", "True")
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", LOCATION)

# Fast multimodal model for document reading; reasoning model for the analyst agents.
MODEL_INTAKE = os.getenv("CT_MODEL_INTAKE", "gemini-3.8-flash")
MODEL_REASON = os.getenv("CT_MODEL_REASON", "gemini-3.8-flash")
# If the primary model is overloaded (429 RESOURCE_EXHAUSTED) or slow, each agent call is retried on these, in order.
MODEL_FALLBACKS = [m.strip() for m in os.getenv("CT_MODEL_FALLBACKS", "gemini-3.5-flash,gemini-3.8-flash").split(",") if m.strip()]
# Per-attempt time limits (seconds) so an overloaded model fails fast and the next one is tried.
TIMEOUT_INTAKE = float(os.getenv("CT_TIMEOUT_INTAKE", "75"))
TIMEOUT_AGENTS = float(os.getenv("CT_TIMEOUT_AGENTS", "150"))
TIMEOUT_CHAT = float(os.getenv("CT_TIMEOUT_CHAT", "20"))
# Gemini 3 thinking level for the agents (MINIMAL, LOW, MEDIUM, HIGH). Empty = model default + temperature.
THINKING_LEVEL = os.getenv("CT_THINKING_LEVEL", "LOW")
# Intake thinking level; LOW reads the demo register correctly in about a third of the time. Empty = model default.
THINKING_LEVEL_INTAKE = os.getenv("CT_THINKING_LEVEL_INTAKE", "LOW")

GCS_BUCKET = os.getenv("CT_GCS_BUCKET", "")          # optional: store uploaded documents
USE_FIRESTORE = os.getenv("CT_FIRESTORE", "0") == "1"  # optional: persist run results

# If Gemini cannot be reached for a bundled sample document, replay its stored
# extraction so a live demo never dies. Always shown as "replayed" in the UI.
ALLOW_SAMPLE_REPLAY = os.getenv("CT_ALLOW_SAMPLE_REPLAY", "1") == "1"

MAX_UPLOAD_MB = int(os.getenv("CT_MAX_UPLOAD_MB", "15"))
INTAKE_CONCURRENCY = int(os.getenv("CT_INTAKE_CONCURRENCY", "10"))
