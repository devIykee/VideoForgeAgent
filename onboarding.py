"""Interactive CLI onboarding flow for MinecraftCast.

Walks a creator through six steps — topic, two characters, voices, avatars, and
length/footage — and returns a fully populated :class:`VideoConfig`. Everything
is plain ``input()`` prompts so it works in any terminal with no dependencies.
"""

import os
import sys

from config import CharacterConfig, VideoConfig


# ElevenLabs preset voices: menu key -> (display name, voice_id).
_ELEVEN_PRESETS = {
    "1": ("adam", "pNInz6obpgDQGcFmaJgB"),
    "2": ("rachel", "21m00Tcm4TlvDq8ikWAM"),
    "3": ("domi", "AZnzlk1XvdvUeBnXmlld"),
    "4": ("bella", "EXAVITQu4vr4xnSDxMaL"),
    "5": ("antoni", "ErXwobaYiN019PkySvjV"),
    "6": ("josh", "TxGEqnHWrfWFTfGW9XjX"),
}

# Coqui XTTS built-in speakers: menu key -> speaker name.
_COQUI_PRESETS = {
    "1": "Claribel Dervla",
    "2": "Daisy Studious",
    "3": "Gracie Wise",
    "4": "Tammie Ema",
    "5": "Alison Dietlinde",
    "6": "Ana Florence",
}

# Avatar skins: menu key -> (skin id, default shirt hex).
_SKIN_MAP = {
    "1": ("steve", "#3B6BB5"),
    "2": ("alex", "#6AA84F"),
    "3": ("creeper", "#4CAF50"),
    "4": ("enderman", "#1A1A1A"),
}


def _clear_screen() -> None:
    """Clear the terminal (best effort, cross-platform)."""
    os.system("cls" if os.name == "nt" else "clear")


def _banner(text: str) -> None:
    """Print a step banner with horizontal rules."""
    print("─" * 45)
    print(text)
    print("─" * 45)


def _prompt_topic() -> str:
    """STEP 1 — collect a free-text video topic (min length enforced)."""
    _banner("STEP 1 of 6  —  Video Topic")
    print("What's your video about? Describe it freely.")
    print("Example: two guys react to the scariest Minecraft seeds ever")
    print()
    topic = input("Your idea: ").strip()
    while len(topic) < 10:
        print("Please describe a bit more.")
        topic = input("Your idea: ").strip()
    return topic


def _prompt_character(step_label: str, index: int, default_name: str) -> tuple[str, str]:
    """Collect a character's name + personality. Returns (name, personality)."""
    _banner(step_label)
    print("Give your character a name and personality.")
    print()
    name = input(f"Character {index} name [default: {default_name}]: ").strip() or default_name
    print(f"What's {name}'s personality? (describe in a few words)")
    print("Example: sarcastic and funny, always doubting everything")
    personality = input(f"{name}'s personality: ").strip()
    while len(personality) < 5:
        personality = input(f"{name}'s personality: ").strip()
    return name, personality


def _prompt_voice_provider() -> str:
    """STEP 4a — pick the voice engine for both characters."""
    _banner("STEP 4 of 6  —  Voice Setup")
    print("""
Pick your voice provider:
  [1] ElevenLabs  — Best quality, realistic voices (needs API key)
  [2] Coqui       — Free, runs locally, good quality
""")
    choice = input("Choice [1/2]: ").strip()
    return "elevenlabs" if choice == "1" else "coqui"


def _collect_voice(name: str, provider: str) -> tuple[str | None, str | None]:
    """Collect voice settings for one character.

    Returns ``(voice_id, voice_sample_path)`` — exactly one is non-None.
    """
    print(f"\n── {name}'s Voice ──")
    print(f"Do you have a voice sample to clone for {name}?")
    print("(A 30-60 second clear audio clip works best)")
    has_sample = input("[y/n]: ").strip().lower()

    if has_sample == "y":
        print("Drag and drop the audio file or paste its full path:")
        sample = input("Path: ").strip().strip('"').strip("'")
        while not os.path.isfile(sample):
            print("That file doesn't exist. Try again (or leave blank to pick a preset).")
            sample = input("Path: ").strip().strip('"').strip("'")
            if not sample:
                break
        if sample and os.path.isfile(sample):
            return None, sample
        # Fall through to preset selection if the path was abandoned.

    if provider == "elevenlabs":
        print(f"\nPick a preset voice for {name}:")
        print("  [1] Adam    — Deep, authoritative male")
        print("  [2] Rachel  — Warm, clear female")
        print("  [3] Domi    — Energetic, young female")
        print("  [4] Bella   — Soft, warm female")
        print("  [5] Antoni  — Well-rounded male")
        print("  [6] Josh    — Deep, serious male")
        preset = input("Choice [1-6]: ").strip()
        _, voice_id = _ELEVEN_PRESETS.get(preset, _ELEVEN_PRESETS["1"])
        return voice_id, None

    # Coqui
    print(f"\nPick a preset speaker for {name}:")
    print("  [1] Claribel Dervla  — Female, clear")
    print("  [2] Daisy Studious   — Female, professional")
    print("  [3] Gracie Wise      — Female, warm")
    print("  [4] Tammie Ema       — Female, energetic")
    print("  [5] Alison Dietlinde — Male, deep")
    print("  [6] Ana Florence     — Female, calm")
    preset = input("Choice [1-6]: ").strip()
    speaker = _COQUI_PRESETS.get(preset, "Claribel Dervla")
    return speaker, None


def _collect_avatar(name: str) -> tuple[str, str]:
    """STEP 5 — collect avatar skin + shirt color for one character."""
    print(f"\n── {name}'s Avatar ──")
    print("  [1] Steve      — Classic blue shirt")
    print("  [2] Alex       — Green shirt")
    print("  [3] Creeper    — Green creeper face")
    print("  [4] Enderman   — Dark with purple eyes")
    print("  [5] Custom     — Enter your own shirt color")
    choice = input("Choice [1-5]: ").strip()

    if choice == "5":
        hex_color = input("Shirt hex color (e.g. #FF5733): ").strip()
        if not hex_color.startswith("#"):
            hex_color = "#" + hex_color
        return "custom", hex_color

    skin, shirt = _SKIN_MAP.get(choice, _SKIN_MAP["1"])
    return skin, shirt


def _prompt_length() -> float:
    """STEP 6a — choose target video length in minutes."""
    print("""
How long should the video be?
  [1] Short  — 2-3 minutes  (good for shorts/clips)
  [2] Medium — 5-7 minutes  (standard YouTube)
  [3] Long   — 10-12 minutes (monetization-friendly)
""")
    choice = input("Choice [1/2/3]: ").strip()
    return {"1": 2.5, "2": 6.0, "3": 11.0}.get(choice, 6.0)


def _prompt_footage() -> tuple[str, str]:
    """STEP 6b — choose footage source and type. Returns (source, type)."""
    print("""
Minecraft footage source:
  [1] Auto-fetch from YouTube     (huge library, instant)
  [2] Auto-fetch from Archive.org (fully legal, no grey area)
  [3] Upload my own footage       (drop file in uploads/ folder)
""")
    choice = input("Choice [1/2/3]: ").strip()
    source = {"1": "youtube", "2": "archive", "3": "upload"}.get(choice, "youtube")

    if source in ("youtube", "archive"):
        print("\nWhat type of Minecraft gameplay? (helps find better footage)")
        print("Examples: survival, horror seeds, speedrun, caves, nether, building")
        footage_type = input("Type [default: survival gameplay]: ").strip() or "survival gameplay"
    else:
        footage_type = "upload"
        os.makedirs("uploads", exist_ok=True)
        print("\nDrop your Minecraft video file into the uploads/ folder now.")
        print("Press Enter when ready...")
        input()

    return source, footage_type


def run_onboarding() -> VideoConfig:
    """Run the full interactive flow and return a populated VideoConfig.

    The ``job_id`` is left as an empty string here; the caller (main.py) assigns
    a fresh UUID before running the pipeline.
    """
    _clear_screen()
    print("╔══════════════════════════════════════╗")
    print("║       MinecraftCast 🎮               ║")
    print("║  Faceless content, zero effort       ║")
    print("║  Type your idea. Get a YouTube video. ║")
    print("╚══════════════════════════════════════╝")
    print()

    # STEP 1 — topic
    topic = _prompt_topic()

    # STEP 2 & 3 — characters
    char1_name, char1_personality = _prompt_character("STEP 2 of 6  —  Character 1", 1, "Alex")
    char2_name, char2_personality = _prompt_character("STEP 3 of 6  —  Character 2", 2, "Steve")

    # STEP 4 — voices
    voice_provider = _prompt_voice_provider()
    char1_voice_id, char1_sample = _collect_voice(char1_name, voice_provider)
    char2_voice_id, char2_sample = _collect_voice(char2_name, voice_provider)

    # STEP 5 — avatars
    _banner("STEP 5 of 6  —  Avatar Appearance")
    char1_skin, char1_shirt = _collect_avatar(char1_name)
    char2_skin, char2_shirt = _collect_avatar(char2_name)

    # STEP 6 — length + footage
    _banner("STEP 6 of 6  —  Length & Footage")
    duration_minutes = _prompt_length()
    footage_source, footage_type = _prompt_footage()

    char1 = CharacterConfig(
        name=char1_name,
        personality=char1_personality,
        voice_provider=voice_provider,
        voice_id=char1_voice_id,
        voice_sample_path=char1_sample,
        avatar_skin=char1_skin,
        shirt_color=char1_shirt,
    )
    char2 = CharacterConfig(
        name=char2_name,
        personality=char2_personality,
        voice_provider=voice_provider,
        voice_id=char2_voice_id,
        voice_sample_path=char2_sample,
        avatar_skin=char2_skin,
        shirt_color=char2_shirt,
    )

    # Confirmation screen
    print("\n" + "═" * 45)
    print("  READY TO GENERATE")
    print("═" * 45)
    print(f"  Topic:      {topic}")
    print(f"  Char 1:     {char1_name} — {char1_personality}")
    print(f"  Char 2:     {char2_name} — {char2_personality}")
    print(f"  Voices:     {voice_provider}")
    print(f"  Duration:   {duration_minutes} minutes")
    print(f"  Footage:    {footage_source} ({footage_type})")
    print("═" * 45)
    confirm = input("\nGenerate video? [y/n]: ").strip().lower()
    if confirm != "y":
        print("Cancelled.")
        sys.exit(0)

    return VideoConfig(
        topic=topic,
        char1=char1,
        char2=char2,
        duration_minutes=duration_minutes,
        footage_source=footage_source,
        footage_type=footage_type,
        job_id="",
    )
