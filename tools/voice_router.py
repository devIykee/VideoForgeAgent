"""Voice engine router for MinecraftCast.

Returns a concrete TTS engine (ElevenLabs or Coqui) based on the provider string
carried on the character config. Imports are deferred so that selecting one
provider never forces the (heavy) dependencies of the other to load.
"""


def get_voice_engine(provider: str):
    """Return the voice engine instance for ``provider``.

    Valid values are ``"elevenlabs"`` and ``"coqui"``.
    """
    if provider == "elevenlabs":
        from tools.voice_elevenlabs import ElevenLabsVoice
        return ElevenLabsVoice()
    elif provider == "coqui":
        from tools.voice_coqui import CoquiVoice
        return CoquiVoice()
    elif provider == "edge":
        from tools.voice_edge import EdgeVoice
        return EdgeVoice()
    else:
        raise ValueError(
            f"Unknown voice provider: {provider}. Use 'elevenlabs', 'coqui', or 'edge'"
        )
