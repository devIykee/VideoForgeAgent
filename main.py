"""VideoForgeAgent — CROO Agent Store provider.

A long-running process (no HTTP server) that:
  1. Connects to CROO over WebSocket via croo-sdk
  2. Auto-accepts incoming negotiations
  3. Runs the full video pipeline when an order is paid
  4. Delivers the resulting video URL back via deliver_order()

The only interface is the CROO WebSocket. Run with: python main.py
"""

import os
import json
import signal
import asyncio
import logging

from dotenv import load_dotenv

load_dotenv()

from croo import (  # noqa: E402  (import after load_dotenv)
    AgentClient,
    Config,
    EventType,
    Event,
    DeliverableType,
    APIError,
    is_insufficient_balance,
    is_not_found,
)

# The real SDK ships DeliverOrderRequest; alias to the spec's DeliverRequest name.
try:  # noqa: SIM105
    from croo import DeliverOrderRequest as DeliverRequest
except ImportError:  # pragma: no cover - fallback for alternate SDK builds
    from croo import DeliverRequest  # type: ignore

import db  # noqa: E402
import pipeline  # noqa: E402
from tools import script  # noqa: E402  (selects the AI provider at import)
from tools import voice, captions  # noqa: E402  (for model warmup)

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("videoforge.main")

config = Config(
    base_url=os.getenv("CROO_API_URL", "https://api.croo.network"),
    ws_url=os.getenv("CROO_WS_URL", "wss://api.croo.network/ws"),
)
client = AgentClient(config, os.getenv("CROO_SDK_KEY"))

# Track in-flight order ids so a reconnect or duplicate event can't double-run.
_active_orders: set[str] = set()


# ---------------------------------------------------------------------------
# Requirements parsing
# ---------------------------------------------------------------------------

def parse_requirements(order) -> dict:
    """Extract the requester's requirements dict from an Order object.

    The schema requirements arrive as a JSON string (or already-parsed dict)
    under one of several possible attribute names depending on SDK version, so
    we probe defensively.
    """
    raw = None
    for attr in ("requirements", "requirement", "input", "input_data",
                 "params", "payload", "deliverable_requirements"):
        val = getattr(order, attr, None)
        if val:
            raw = val
            break
    if raw is None and isinstance(order, dict):
        raw = order.get("requirements") or order.get("input")

    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {"topic": str(parsed)}
        except json.JSONDecodeError:
            return {"topic": raw}
    return {}


# ---------------------------------------------------------------------------
# Event handlers (async work, dispatched via asyncio.create_task)
# ---------------------------------------------------------------------------

async def _handle_negotiation(negotiation_id: str) -> None:
    try:
        result = await client.accept_negotiation(negotiation_id)
        order = getattr(result, "order", None)
        order_id = getattr(order, "order_id", None) if order else None
        log.info("Accepted negotiation %s -> order %s", negotiation_id, order_id)
    except APIError as e:
        log.error("Failed to accept negotiation %s: %s", negotiation_id, e)
    except Exception as e:  # noqa: BLE001
        if is_not_found(e):
            log.warning("Negotiation %s not found", negotiation_id)
        else:
            log.exception("Unexpected error accepting negotiation %s", negotiation_id)


async def _handle_order_paid(order_id: str) -> None:
    if order_id in _active_orders:
        log.info("Order %s already processing; ignoring duplicate event", order_id)
        return
    _active_orders.add(order_id)

    job_id = str(order_id)
    try:
        order = await client.get_order(order_id)
        requirements = parse_requirements(order)
        log.info("Order %s paid. Requirements: %s", order_id, requirements)

        await db.create_job(job_id, requirements)

        video_url = await pipeline.run(job_id, requirements)

        await client.deliver_order(
            order_id,
            DeliverRequest(
                deliverable_type=DeliverableType.SCHEMA,
                deliverable_schema=json.dumps({"video_url": video_url}),
            ),
        )
        log.info("Order %s delivered: %s", order_id, video_url)

    except APIError as e:
        log.error("API error processing order %s: %s", order_id, e)
        if is_insufficient_balance(e):
            log.error("Insufficient balance — cannot complete order %s", order_id)
        await _safe_reject(order_id, f"API error: {e}")
    except Exception as e:  # noqa: BLE001
        if is_not_found(e):
            log.warning("Order %s not found", order_id)
        else:
            log.exception("Pipeline failed for order %s", order_id)
        await _safe_reject(order_id, str(e))
    finally:
        _active_orders.discard(order_id)


async def _safe_reject(order_id: str, reason: str) -> None:
    try:
        await client.reject_order(order_id, reason[:480])
        log.info("Rejected order %s: %s", order_id, reason)
    except Exception:  # noqa: BLE001
        log.exception("Failed to reject order %s", order_id)


# ---------------------------------------------------------------------------
# Synchronous WS callbacks -> async tasks
# ---------------------------------------------------------------------------

def on_negotiation_created(e: Event) -> None:
    log.info("Event NEGOTIATION_CREATED: %s", e.negotiation_id)
    asyncio.create_task(_handle_negotiation(e.negotiation_id))


def on_order_paid(e: Event) -> None:
    log.info("Event ORDER_PAID: %s", e.order_id)
    asyncio.create_task(_handle_order_paid(e.order_id))


def on_order_completed(e: Event) -> None:
    log.info("Event ORDER_COMPLETED: %s (settled)", e.order_id)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

async def main() -> None:
    if not os.getenv("CROO_SDK_KEY"):
        raise SystemExit("CROO_SDK_KEY is not set. See .env.example")

    await db.init_db()

    log.info("AI provider: %s", script.ai.provider_name)

    # Load heavy local models ONCE before accepting any work.
    log.info("Warming up local models (Kokoro + Whisper)...")
    await asyncio.to_thread(voice.warmup)
    await asyncio.to_thread(captions.warmup)

    log.info("Connecting to CROO WebSocket: %s", config.ws_url)
    stream = await client.connect_websocket()
    stream.on(EventType.NEGOTIATION_CREATED, on_negotiation_created)
    stream.on(EventType.ORDER_PAID, on_order_paid)
    stream.on(EventType.ORDER_COMPLETED, on_order_completed)

    log.info("VideoForgeAgent online. Listening for negotiations and orders.")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows lacks add_signal_handler for SIGTERM
            signal.signal(sig, lambda *_: stop.set())

    try:
        await stop.wait()
    finally:
        log.info("Shutting down...")
        await stream.close()
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
