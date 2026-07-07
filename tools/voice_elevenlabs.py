"""ElevenLabs TTS engine for MinecraftCast.

Handles preset voices, on-the-fly voice cloning from an audio sample, and
emotion-aware speech synthesis. Emotion is mapped onto ElevenLabs'
stability / similarity-boost knobs. All network I/O is async via aiohttp.
"""

import os
import asyncio

import aiohttp

from config import CharacterConfig


class ElevenLabsVoice:
    """Thin async wrapper over the ElevenLabs v1 REST API."""

    BASE_URL = "https://api.elevenlabs.io/v1"

    PRESET_VOICES = {
        "adam":   "pNInz6obpgDQGcFmaJgB",
        "rachel": "21m00Tcm4TlvDq8ikWAM",
        "domi":   "AZnzlk1XvdvUeBnXmlld",
        "bella":  "EXAVITQu4vr4xnSDxMaL",
        "antoni": "ErXwobaYiN019PkySvjV",
        "josh":   "TxGEqnHWrfWFTfGW9XjX",
    }
    NARRATOR_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"  # Rachel for narrator

    # Emotion -> voice_settings. Lower stability = more expressive/variable.
    EMOTION_SETTINGS = {
        "excited":   {"stability": 0.3, "similarity_boost": 0.8},
        "scared":    {"stability": 0.2, "similarity_boost": 0.9},
        "skeptical": {"stability": 0.6, "similarity_boost": 0.7},
        "laughing":  {"stability": 0.2, "similarity_boost": 0.8},
        "serious":   {"stability": 0.7, "similarity_boost": 0.8},
        "curious":   {"stability": 0.4, "similarity_boost": 0.8},
        "shocked":   {"stability": 0.2, "similarity_boost": 0.9},
        "smug":      {"stability": 0.6, "similarity_boost": 0.7},
    }

    def _api_key(self) -> str:
        """Return the ElevenLabs API key, raising a clear error if missing."""
        key = os.getenv("ELEVENLABS_API_KEY")
        if not key:
            raise RuntimeError(
                "ELEVENLABS_API_KEY is not set. Add it to your .env or choose the "
                "Coqui voice provider instead."
            )
        return key

    async def setup_voice(self, char: CharacterConfig) -> str:
        """Resolve the voice_id to use for a character.

        Clones from a sample if provided, otherwise uses the chosen preset (or
        Adam as a final fallback).
        """
        if char.voice_sample_path:
            return await self.clone_voice(char.name, char.voice_sample_path)
        return char.voice_id or self.PRESET_VOICES["adam"]

    async def clone_voice(self, name: str, audio_path: str) -> str:
        """Clone a voice from an audio sample. Returns the new voice_id."""
        async with aiohttp.ClientSession() as session:
            with open(audio_path, "rb") as f:
                form = aiohttp.FormData()
                form.add_field("name", f"MinecraftCast_{name}")
                form.add_field(
                    "files", f,
                    filename=os.path.basename(audio_path),
                    content_type="audio/mpeg",
                )
                async with session.post(
                    f"{self.BASE_URL}/voices/add",
                    headers={"xi-api-key": self._api_key()},
                    data=form,
                ) as resp:
                    if resp.status != 200:
                        raise RuntimeError(
                            f"ElevenLabs voice clone failed {resp.status}: {await resp.text()}"
                        )
                    data = await resp.json()
                    return data["voice_id"]

    async def generate_speech(self, text: str, voice_id: str,
                              emotion: str, job_id: str,
                              segment_index: int) -> str:
        """Synthesize one segment to MP3. Returns the output path."""
        settings = self.EMOTION_SETTINGS.get(
            emotion, {"stability": 0.5, "similarity_boost": 0.8}
        )

        payload = {
            "text": text,
            "model_id": "eleven_turbo_v2",
            "voice_settings": settings,
        }

        output_path = f"/tmp/minecraftcast/{job_id}/audio/seg_{segment_index:03d}.mp3"
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.BASE_URL}/text-to-speech/{voice_id}",
                headers={
                    "xi-api-key": self._api_key(),
                    "Content-Type": "application/json",
                },
                json=payload,
            ) as resp:
                if resp.status != 200:
                    raise RuntimeError(
                        f"ElevenLabs error {resp.status}: {await resp.text()}"
                    )
                with open(output_path, "wb") as f:
                    async for chunk in resp.content.iter_chunked(4096):
                        f.write(chunk)

        return output_path

    async def generate_all(self, segments: list, char1_voice_id: str,
                           char2_voice_id: str, job_id: str) -> dict:
        """Synthesize audio for every segment. Returns {segment_index: mp3_path}."""
        results: dict = {}
        for seg in segments:
            voice_id = {
                "char1": char1_voice_id,
                "char2": char2_voice_id,
                "narrator": self.NARRATOR_VOICE_ID,
            }[seg["speaker"]]

            path = await self.generate_speech(
                seg["text"], voice_id, seg["emotion"],
                job_id, seg["segment_index"],
            )
            results[seg["segment_index"]] = path
            await asyncio.sleep(0.5)  # gentle rate-limit buffer
        return results
