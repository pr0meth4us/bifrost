"""Google AI client factories for Bifrost consumers.

Bifrost already owns *credentials* (``bifrost_client.get_config`` resolves secrets
from the vault, with env fallback). This module adds the other half of "talk to
Google AI": constructing the google.genai (Vertex) and Cloud Vision clients with
those credentials — so no downstream project hand-rolls
``genai.Client(vertexai=True, project=..., location=...)`` or re-points
GOOGLE_APPLICATION_CREDENTIALS on its own (the boilerplate that used to live,
copy-pasted, in a dozen scripts across every repo).

    import sys; sys.path.insert(0, "/Users/nicksng/code/bifrost/sdk/python")
    from bifrost_ai import get_genai_client, get_vision_client
    client = get_genai_client()    # Vertex Gemini
    vision = get_vision_client()   # Cloud Vision OCR

google.genai / google-cloud-vision are imported lazily inside each factory, so
this module stays free for SDK users who only need secrets (bifrost_client).

Project / location come from the vault/env (VERTEX_PROJECT / VERTEX_LOCATION);
the defaults below are the workspace's GCP project. Pass project=/location= to
override per call.
"""
import os

from bifrost_client import get_config

DEFAULT_PROJECT = "egd-ai-services-1782364268"
DEFAULT_LOCATION = "us-central1"

_creds_ready = False


def ensure_credentials():
    """Point GOOGLE_APPLICATION_CREDENTIALS at a usable service-account file.

    The value is resolved by bifrost (vault first, then local env). Idempotent
    and safe to call repeatedly.
    """
    global _creds_ready
    if _creds_ready:
        return
    sa = get_config("GOOGLE_APPLICATION_CREDENTIALS")
    if sa:
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = sa
    _creds_ready = True


def get_genai_client(project=None, location=None):
    """Return a google.genai Client wired to Vertex AI, creds handled by bifrost."""
    ensure_credentials()
    from google import genai

    return genai.Client(
        vertexai=True,
        project=project or get_config("VERTEX_PROJECT", DEFAULT_PROJECT),
        location=location or get_config("VERTEX_LOCATION", DEFAULT_LOCATION),
    )


def get_vision_client():
    """Return a google.cloud.vision ImageAnnotatorClient, creds handled by bifrost."""
    ensure_credentials()
    from google.cloud import vision

    return vision.ImageAnnotatorClient()


if __name__ == "__main__":
    # Self-check: wiring only, no network and no google imports required.
    assert callable(get_genai_client) and callable(get_vision_client)
    ensure_credentials()
    assert ensure_credentials() is None  # idempotent second call
    print(
        "bifrost_ai self-check passed; creds ->",
        os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "(none)"),
    )
