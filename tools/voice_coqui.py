"""Coqui XTTS v2 local TTS engine for MinecraftCast.

Runs entirely offline once the model weights are downloaded. Supports both
built-in preset speakers and zero-shot voice cloning from a reference WAV.
The XTTS model is synchronous, so speech generation is dispatched to a thread
pool to keep the async pipeline responsive. Output WAV is transcoded to MP3 so
every downstream stage sees a uniform format regardless of engine.
"""

import os
import asyncio
import subprocess

from config import CharacterConfig


class CoquiVoice:
    """Wrapper around the Coqui TTS XTTS v2 multilingual model."""

    def __init__(self):
        """Load the XTTS model once at startup."""
        from TTS.api import TTS

        self.model = TTS("tts_models/multilingual/multi-dataset/xtts_v2")
        print("      Coqui XTTS loaded ✓")

    async def setup_voice(self, char: CharacterConfig) -> str:
        """Return the speaker name or reference-WAV path for this character."""
        if char.voice_sample_path:
            return char.voice_sample_path  # used as speaker_wav for cloning
        return char.voice_id or "Claribel Dervla"

    def generate_speech(self, text: str, speaker: str,
                        job_id: str, segment_index: int) -> str:
        """Synthesize one segment to MP3. ``speaker`` is a name or a WAV path."""
        output_path = f"/tmp/minecraftcast/{job_id}/audio/seg_{segment_index:03d}.wav"
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        if os.path.isfile(speaker):
            # Voice-cloning mode: use the reference WAV as the target speaker.
            self.model.tts_to_file(
                text=text,
                speaker_wav=speaker,
                language="en",
                file_path=output_path,
            )
        else:
            # Preset-speaker mode.
            self.model.tts_to_file(
                text=text,
                speaker=speaker,
                language="en",
                file_path=output_path,
            )

        # Transcode WAV -> MP3 for a uniform downstream format.
        mp3_path = output_path.replace(".wav", ".mp3")
        subprocess.run(
            ["ffmpeg", "-y", "-i", output_path,
             "-codec:a", "libmp3lame", "-qscale:a", "2", mp3_path],
            check=True, capture_output=True,
        )
        os.remove(output_path)
        return mp3_path

    async def generate_all(self, segments: list, char1_speaker: str,
                           char2_speaker: str, job_id: str) -> dict:
        """Run all TTS in a thread pool (XTTS is sync). Returns {index: mp3_path}."""
        loop = asyncio.get_event_loop()
        results: dict = {}
        for seg in segments:
            speaker = {
                "char1": char1_speaker,
                "char2": char2_speaker,
                "narrator": "Claribel Dervla",
            }[seg["speaker"]]
            path = await loop.run_in_executor(
                None, self.generate_speech,
                seg["text"], speaker, job_id, seg["segment_index"],
            )
            results[seg["segment_index"]] = path
        return results
