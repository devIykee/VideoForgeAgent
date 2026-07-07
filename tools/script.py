"""Groq-powered dialogue script generator for MinecraftCast.

Produces a structured, validated JSON script describing a back-and-forth
conversation between two characters reacting to Minecraft gameplay. Groq exposes
an OpenAI-compatible endpoint, so we drive it with the ``openai`` async client
pointed at Groq's base URL.
"""

import os
import json
import asyncio

from openai import AsyncOpenAI, RateLimitError

from config import VideoConfig

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_MODEL = "llama-3.3-70b-versatile"

# A single shared async client. Created lazily so importing this module never
# requires an API key to be present.
_groq_client: AsyncOpenAI | None = None


def _client() -> AsyncOpenAI:
    """Return the shared Groq client, creating it on first use."""
    global _groq_client
    if _groq_client is None:
        _groq_client = AsyncOpenAI(
            api_key=os.getenv("GROQ_API_KEY"),
            base_url=GROQ_BASE_URL,
        )
    return _groq_client


MASTER_SYSTEM_PROMPT = """
You are MinecraftCast Script AI — you write viral, entertaining dialogue
scripts for Minecraft faceless YouTube videos.

Two characters talk to each other like real people — natural, funny, reactive.
They're watching/experiencing Minecraft gameplay and reacting to it in real time.

ALWAYS return only valid JSON. No markdown fences. No explanation. Just JSON.

OUTPUT SCHEMA:
{
  "title": string,
  "youtube_title": string,
  "total_duration_seconds": number,
  "segments": [
    {
      "segment_index": number,
      "type": "dialogue" | "narration",
      "speaker": "char1" | "char2" | "narrator",
      "text": string,
      "word_count": number,
      "duration_seconds": number,
      "emotion": "excited" | "scared" | "skeptical" | "laughing" | "serious" | "curious" | "shocked" | "smug",
      "minecraft_action": string,
      "footage_timestamp_hint": string
    }
  ],
  "footage_search_query": string,
  "background_music_mood": "chill" | "tense" | "upbeat" | "mysterious",
  "thumbnail_segment_index": number
}

RULES:
- word_count * 0.4 = duration_seconds (approx 150wpm)
- Always start with char1 saying a hook line that grabs attention immediately
- Characters must reference what the other just said — real conversation, not monologue
- char1 and char2 must have distinctly different speaking styles matching their personalities
- minecraft_action: what's happening in Minecraft at this exact moment
  (e.g. "player finds diamond", "creeper explosion destroys house", "falling into void")
  This is used to find relevant footage clips.
- footage_timestamp_hint: rough timestamp in the footage to use
  (e.g. "0:30", "2:15", "4:00") — spread across the video
- narration segments: used for transitions ("Meanwhile, deeper in the cave...")
- Every 4-5 segments, insert something unexpected to keep viewer watching
- footage_search_query: best search term for Minecraft footage matching the topic
- Return ONLY JSON. Absolutely nothing else.
"""


async def generate_script(config: VideoConfig) -> dict:
    """Generate a full dialogue script via Groq.

    Retries up to three times, feeding validation errors back into the prompt so
    the model can self-correct. Raises RuntimeError if all attempts fail.
    """
    style_note = ""
    if config.duration_minutes <= 3:
        style_note = "This is a SHORT video. Hook immediately. One main idea. Fast pacing."
    elif config.duration_minutes >= 10:
        style_note = "This is a LONG video. Multiple acts. Build tension. Reward at the end."

    user_prompt = f"""
Topic: {config.topic}
Duration: {config.duration_minutes} minutes ({int(config.duration_minutes * 60)} seconds total)

Character 1: {config.char1.name}
Personality: {config.char1.personality}

Character 2: {config.char2.name}
Personality: {config.char2.personality}

{style_note}

Write the complete script. Make it feel like a real podcast or react video.
The characters are experiencing Minecraft gameplay happening on screen behind them.
Be entertaining. Be natural. Let personalities clash and complement each other.
"""

    for attempt in range(3):
        try:
            response = await _client().chat.completions.create(
                model=GROQ_MODEL,
                messages=[
                    {"role": "system", "content": MASTER_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.8,
                max_tokens=6000,
            )
            raw = (response.choices[0].message.content or "").strip()
            raw = _strip_fences(raw)
            script = json.loads(raw)
            validate_script(script)
            _normalize_script(script, config)
            return script
        except (json.JSONDecodeError, ValueError) as e:
            if attempt == 2:
                raise RuntimeError(f"Script generation failed after 3 attempts: {e}")
            user_prompt += f"\n\nERROR IN PREVIOUS ATTEMPT: {e}\nFix this and try again."
        except RateLimitError:
            await asyncio.sleep(10 * (attempt + 1))

    raise RuntimeError("Script generation failed after 3 attempts")


def _strip_fences(raw: str) -> str:
    """Remove accidental ```json code fences the model may add despite instructions."""
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    return raw


def validate_script(script: dict) -> None:
    """Validate the script schema. Raise ValueError if invalid."""
    required = ["title", "total_duration_seconds", "segments",
                "footage_search_query", "background_music_mood"]
    for field in required:
        if field not in script:
            raise ValueError(f"Missing field: {field}")
    if not isinstance(script["segments"], list) or len(script["segments"]) < 3:
        raise ValueError("Script must have at least 3 segments")
    for seg in script["segments"]:
        if seg.get("speaker") not in ("char1", "char2", "narrator"):
            raise ValueError(f"Invalid speaker: {seg.get('speaker')}")
        if "text" not in seg or len(seg["text"]) < 5:
            raise ValueError(f"Segment {seg.get('segment_index')} has no text")


def _normalize_script(script: dict, config: VideoConfig) -> None:
    """Fill in missing per-segment fields so downstream stages never KeyError.

    The model is reliable but not perfect; we defensively re-derive indices,
    word counts, and durations, and supply sane defaults for optional fields.
    """
    for i, seg in enumerate(script["segments"]):
        seg["segment_index"] = i
        seg.setdefault("type", "dialogue")
        seg.setdefault("emotion", "curious")
        seg.setdefault("minecraft_action", "")
        seg.setdefault("footage_timestamp_hint", "")

        words = len(seg["text"].split())
        seg["word_count"] = words
        # 150 wpm ≈ 0.4s per word; enforce a minimum so very short lines still play.
        seg["duration_seconds"] = max(1.5, round(words * 0.4, 2))

    # Recompute the total from segment durations to keep music fades accurate.
    script["total_duration_seconds"] = round(
        sum(s["duration_seconds"] for s in script["segments"]), 2
    )
    script.setdefault("youtube_title", script.get("title", config.topic))
    script.setdefault("thumbnail_segment_index", 0)
    if script.get("background_music_mood") not in ("chill", "tense", "upbeat", "mysterious"):
        script["background_music_mood"] = "chill"
