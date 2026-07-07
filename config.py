"""Pydantic configuration models for MinecraftCast.

These models are the single source of truth for a video job. They are shared by
the standalone CLI (onboarding.py), the core pipeline (pipeline.py), and every
integration adapter (REST, MCP, CROO). Nothing here depends on any external
service, so the models can be imported anywhere without side effects.
"""

from typing import Optional

from pydantic import BaseModel


class CharacterConfig(BaseModel):
    """One on-screen character: how they talk, sound, and look."""

    name: str
    personality: str
    voice_provider: str                       # "elevenlabs" | "coqui"
    voice_id: Optional[str] = None            # ElevenLabs voice ID or Coqui speaker
    voice_sample_path: Optional[str] = None   # path to audio file for cloning
    avatar_skin: str = "steve"                # "steve"|"alex"|"creeper"|"enderman"|"custom"
    shirt_color: str = "#3B6BB5"              # hex color


class VideoConfig(BaseModel):
    """A complete, self-contained description of one video to generate."""

    topic: str
    char1: CharacterConfig
    char2: CharacterConfig
    duration_minutes: float                   # 2.5 | 6.0 | 11.0
    footage_source: str                       # "youtube" | "archive" | "upload"
    footage_type: str                         # "survival" | "horror" | "speedrun" | ...
    job_id: str
