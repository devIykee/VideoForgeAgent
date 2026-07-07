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
import uuid
import asyncio

from dotenv import load_dotenv

load_dotenv()

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


if os.getenv("ENABLE_CROO", "false").lower() == "true":
    from integrations.croo_provider import run_croo_provider

    asyncio.run(run_croo_provider())
else:
    asyncio.run(main())
