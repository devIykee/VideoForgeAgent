"""CROO Agent Store integration for MinecraftCast (optional layer).

Enable by setting ``ENABLE_CROO=true`` in ``.env``. This wraps the core pipeline
(``pipeline.py``, which has zero CROO dependency) as a CROO provider agent: it
connects over WebSocket, auto-accepts negotiations, and on ``ORDER_PAID`` runs
the pipeline, uploads the result to R2, and delivers the public URL back.

CROO SDK docs: https://docs.croo.network/developer-docs/quick-start
"""

import os
import json
import uuid
import signal
import asyncio
import logging

from croo import (
    AgentClient,
    Config,
    Event,
    EventType,
    DeliverOrderRequest,
    DeliverableType,
    APIError,
    is_insufficient_balance,
    is_not_found,
)

from config import VideoConfig, CharacterConfig

logger = logging.getLogger("minecraftcast.croo")

# Track in-flight orders so a reconnect or duplicate event can't double-run one.
_active_orders: set[str] = set()


def _parse_requirements(order) -> dict:
    """Extract the requester's requirements dict from an Order object.

    The requirements arrive as a JSON string (or already-parsed dict) under one
    of several possible attribute names depending on SDK version, so we probe
    defensively and fall back to an empty dict.
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


def _build_config(requirements: dict) -> VideoConfig:
    """Map a CROO requirements dict onto a VideoConfig."""
    voice_provider = requirements.get("voice_provider", "elevenlabs")
    return VideoConfig(
        topic=requirements.get("topic", "Minecraft adventure"),
        char1=CharacterConfig(
            name=requirements.get("char1_name", "Alex"),
            personality=requirements.get("char1_personality", "energetic and funny"),
            voice_provider=voice_provider,
            voice_id=requirements.get("char1_voice_id"),
            avatar_skin=requirements.get("char1_skin", "alex"),
            shirt_color=requirements.get("char1_shirt_color", "#6AA84F"),
        ),
        char2=CharacterConfig(
            name=requirements.get("char2_name", "Steve"),
            personality=requirements.get("char2_personality", "calm and skeptical"),
            voice_provider=voice_provider,
            voice_id=requirements.get("char2_voice_id"),
            avatar_skin=requirements.get("char2_skin", "steve"),
            shirt_color=requirements.get("char2_shirt_color", "#3B6BB5"),
        ),
        duration_minutes=float(requirements.get("duration_minutes", 3.0)),
        footage_source=requirements.get("footage_source", "youtube"),
        footage_type=requirements.get("footage_type", "survival gameplay"),
        job_id=str(uuid.uuid4()),
    )


async def run_croo_provider() -> None:
    """Connect to CROO and process paid orders forever.

    Auto-accepts negotiations; on ORDER_PAID, runs the MinecraftCast pipeline,
    uploads the video to R2, and delivers the URL back to the requester.
    """
    if not os.getenv("CROO_SDK_KEY"):
        raise SystemExit("CROO_SDK_KEY is not set. See .env.example")

    config = Config(
        base_url=os.getenv("CROO_API_URL", "https://api.croo.network"),
        ws_url=os.getenv("CROO_WS_URL", "wss://api.croo.network/ws"),
    )
    client = AgentClient(config, os.getenv("CROO_SDK_KEY"))

    # Track jobs in the local SQLite DB for observability.
    import db
    await db.init_db()

    async def _handle_negotiation(negotiation_id: str) -> None:
        """Auto-accept an incoming negotiation."""
        try:
            result = await client.accept_negotiation(negotiation_id)
            order = getattr(result, "order", None)
            order_id = getattr(order, "order_id", None) if order else None
            logger.info("Accepted negotiation %s -> order %s", negotiation_id, order_id)
        except APIError as e:
            logger.error("Failed to accept negotiation %s: %s", negotiation_id, e)
        except Exception as e:  # noqa: BLE001
            if is_not_found(e):
                logger.warning("Negotiation %s not found", negotiation_id)
            else:
                logger.exception("Error accepting negotiation %s", negotiation_id)

    async def _handle_order_paid(order_id: str) -> None:
        """Run the pipeline for a paid order and deliver the result."""
        if order_id in _active_orders:
            logger.info("Order %s already processing; ignoring duplicate", order_id)
            return
        _active_orders.add(order_id)
        try:
            order = await client.get_order(order_id)
            requirements = _parse_requirements(order)
            logger.info("ORDER_PAID %s. Requirements: %s", order_id, requirements)

            video_config = _build_config(requirements)
            await db.create_job(video_config.job_id, requirements)

            # Run the core pipeline (no CROO dependency).
            from pipeline import run as run_pipeline
            video_path = await run_pipeline(video_config)

            # Upload to R2 and get a public URL.
            from storage.r2 import upload_video
            video_url = await upload_video(video_config.job_id, video_path)
            await db.update_job(video_config.job_id, status="complete", output_url=video_url)

            await client.deliver_order(
                order_id,
                DeliverOrderRequest(
                    deliverable_type=DeliverableType.SCHEMA,
                    deliverable_schema=json.dumps({
                        "video_url": video_url,
                        "title": video_config.topic,
                        "duration_minutes": video_config.duration_minutes,
                        "status": "complete",
                    }),
                ),
            )
            logger.info("Order %s delivered ✓ -> %s", order_id, video_url)

        except APIError as e:
            logger.error("API error on order %s: %s", order_id, e)
            if is_insufficient_balance(e):
                logger.error("Insufficient balance — cannot complete order %s", order_id)
            await _safe_reject(client, order_id, f"API error: {e}")
        except Exception as e:  # noqa: BLE001
            if is_not_found(e):
                logger.warning("Order %s not found", order_id)
            else:
                logger.exception("Pipeline failed for order %s", order_id)
            await _safe_reject(client, order_id, str(e))
        finally:
            _active_orders.discard(order_id)

    def on_negotiation(e: Event) -> None:
        """WS callback: dispatch negotiation handling as a task."""
        asyncio.create_task(_handle_negotiation(e.negotiation_id))

    def on_paid(e: Event) -> None:
        """WS callback: dispatch order processing as a task."""
        asyncio.create_task(_handle_order_paid(e.order_id))

    logger.info("Connecting to CROO WebSocket: %s", config.ws_url)
    stream = await client.connect_websocket()
    stream.on(EventType.NEGOTIATION_CREATED, on_negotiation)
    stream.on(EventType.ORDER_PAID, on_paid)
    logger.info("MinecraftCast CROO provider online ✓ — waiting for orders...")

    # Stay alive until interrupted.
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
        logger.info("Shutting down CROO provider...")
        await stream.close()
        await client.close()


async def _safe_reject(client, order_id: str, reason: str) -> None:
    """Reject an order, swallowing any secondary errors."""
    try:
        await client.reject_order(order_id, reason[:480])
        logger.info("Rejected order %s: %s", order_id, reason)
    except Exception:  # noqa: BLE001
        logger.exception("Failed to reject order %s", order_id)
