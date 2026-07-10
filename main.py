"""MinecraftCast entry point.

Two modes, chosen by the ``ENABLE_CROO`` env var:

* ``ENABLE_CROO=false`` (default) — run the interactive CLI: onboarding collects
  a :class:`VideoConfig`, then the core pipeline produces an MP4.
* ``ENABLE_CROO=true``            — run as a CROO Agent Store provider that
  listens for paid orders and delivers finished videos.

The core pipeline (pipeline.py) is identical in both modes and has no CROO
dependency; the CROO path merely wraps it.
"""

import os
import sys
import uuid
import asyncio

from dotenv import load_dotenv

load_dotenv()

# Ensure the console can print the banners (═ ╔ ✓) on every platform, including
# the legacy-codepage Windows terminal, before any output happens.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 — best effort; never fatal
        pass

from onboarding import run_onboarding  # noqa: E402  (import after load_dotenv)
from pipeline import run as run_pipeline  # noqa: E402


async def main() -> None:
    """Interactive CLI flow: onboard, generate, report the output path."""
    config = run_onboarding()
    config.job_id = str(uuid.uuid4())

    final_path = await run_pipeline(config)

    print(f"\n{'═' * 50}")
    print("  ✓ VIDEO READY")
    print(f"{'═' * 50}")
    print(f"  File: {final_path}")
    print("\n  Upload directly to YouTube — it's ready.")
    print(f"{'═' * 50}\n")


# Mode selection (checked in priority order):
#   ENABLE_CROO=true                 -> CROO Agent Store provider (outbound WebSocket)
#   SERVE_REST=true / RUN_MODE=rest  -> FastAPI REST server (e.g. Hugging Face Spaces)
#   otherwise                        -> interactive CLI
if os.getenv("ENABLE_CROO", "false").lower() == "true":
    from integrations.croo_provider import run_croo_provider

    asyncio.run(run_croo_provider())
elif (os.getenv("SERVE_REST", "").lower() == "true"
      or os.getenv("RUN_MODE", "").lower() == "rest"):
    import uvicorn  # lazy — only needed in REST mode

    # Hugging Face Spaces (and most PaaS) inject the port via $PORT; default to
    # the HF Docker convention of 7860.
    port = int(os.getenv("PORT", "7860"))
    uvicorn.run("integrations.rest_api:app", host="0.0.0.0", port=port)
else:
    asyncio.run(main())
