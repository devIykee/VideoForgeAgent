"""Microsoft Edge neural TTS engine for MinecraftCast (free, no API key).

Uses the `edge-tts` package, which reaches Microsoft's public neural voices — the
same ones Edge's "Read aloud" uses. It requires internet but **no key and no
signup**, and the voices are high quality, which makes it the best zero-cost
option for testing and for creators who don't want to pay for ElevenLabs.

Emotion is approximated by nudging the speaking rate and pitch, since the public
endpoint doesn't expose ElevenLabs-style expressiveness controls.
"""

import os

import edge_tts

from config import CharacterConfig


class EdgeVoice:
    """Async wrapper over the free Edge neural TTS voices."""

    # Friendly preset names -> Edge voice short names.
    PRESET_VOICES = {
        "guy":         "en-US-GuyNeural",         # casual male
        "christopher": "en-US-ChristopherNeural",  # deeper male
        "eric":        "en-US-EricNeural",         # warm male
        "aria":        "en-US-AriaNeural",         # clear female
        "jenny":       "en-US-JennyNeural",        # friendly female
        "ana":         "en-US-AnaNeural",          # young female
        "ryan":        "en-GB-RyanNeural",         # british male
    }
    DEFAULT_CHAR1 = "en-US-GuyNeural"
    DEFAULT_CHAR2 = "en-US-ChristopherNeural"
    NARRATOR_VOICE = "en-US-AriaNeural"

    # Emotion -> (rate, pitch) deltas passed to edge-tts.
    EMOTION_SETTINGS = {
        "excited":   ("+12%", "+15Hz"),
        "scared":    ("+8%",  "+20Hz"),
        "skeptical": ("+0%",  "-3Hz"),
        "laughing":  ("+6%",  "+10Hz"),
        "serious":   ("-6%",  "-5Hz"),
        "curious":   ("+0%",  "+6Hz"),
        "shocked":   ("+10%", "+25Hz"),
        "smug":      ("-4%",  "+0Hz"),
    }

    def _resolve(self, name_or_voice: str | None, default: str) -> str:
        """Map a preset name (or raw Edge voice id) to an Edge voice short name."""
        if not name_or_voice:
            return default
        return self.PRESET_VOICES.get(name_or_voice.lower(), name_or_voice)

    async def setup_voice(self, char: CharacterConfig) -> str:
        """Resolve the Edge voice for a character.

        Cloning isn't supported by the free endpoint, so a provided
        ``voice_sample_path`` is ignored in favor of the chosen/known voice.
        """
        return self._resolve(char.voice_id, self.DEFAULT_CHAR1)

    async def generate_speech(self, text: str, voice: str, emotion: str,
                              job_id: str, segment_index: int) -> str:
        """Synthesize one segment to MP3. Returns the output path."""
        rate, pitch = self.EMOTION_SETTINGS.get(emotion, ("+0%", "+0Hz"))

        output_path = f"/tmp/minecraftcast/{job_id}/audio/seg_{segment_index:03d}.mp3"
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
        await communicate.save(output_path)
        return output_path

    async def generate_all(self, segments: list, char1_voice: str,
                           char2_voice: str, job_id: str) -> dict:
        """Synthesize audio for every segment. Returns {segment_index: mp3_path}."""
        results: dict = {}
        for seg in segments:
            voice = {
                "char1": char1_voice,
                "char2": char2_voice,
                "narrator": self.NARRATOR_VOICE,
            }[seg["speaker"]]
            path = await self.generate_speech(
                seg["text"], voice, seg["emotion"], job_id, seg["segment_index"]
            )
            results[seg["segment_index"]] = path
        return results
