"""VideoForge AI Script Engine — the core creative skill.

Produces broadcast-quality, retention-optimized video scripts as strict JSON
via a pluggable AI provider (see tools/providers.py): `complete()` generates the
full script; `fast_complete()` optimizes stock-footage search queries on demand.

This module is intentionally self-contained and defensive: it validates and
auto-repairs model output, and retries with self-correction feedback so the rest
of the pipeline always receives a clean, schema-conformant script dict.
"""

import re
import json
import logging

from tools.providers import get_provider

log = logging.getLogger("videoforge.script")

# The AI backend is chosen via the AI_PROVIDER env var (see tools/providers.py).
# Loaded once at module import; reused for every script and query call.
ai = get_provider()

# Words spoken per second at a natural narration pace (150 wpm == 2.5 wps).
WORDS_PER_SECOND = 2.5

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MASTER_SYSTEM_PROMPT = """
You are VideoForge — the world's most advanced AI video script generator.
You have deep expertise in:
- YouTube retention psychology (pattern interrupts, open loops, hooks)
- Documentary storytelling (tension arcs, revelation structure)
- Viral short-form content (3-second hooks, single insight, strong CTA)
- Stock footage optimization (knowing exactly what search terms yield great clips)
- Narration pacing (150 words = exactly 60 seconds at natural pace)

Your output is always a single valid JSON object. No markdown fences.
No explanation. No preamble. Just the JSON.

OUTPUT SCHEMA (follow exactly):
{
  "title": string,
  "style": "explainer|documentary|shorts|faceless|slideshow",
  "total_duration_seconds": number,
  "target_resolution": "1920x1080|1080x1920",
  "hook": string,
  "hook_type": "question|bold_claim|shocking_stat|story_open|contrarian",
  "chapters": [
    {
      "chapter_index": number,
      "title": string,
      "chapter_purpose": "intro|buildup|climax|resolution|cta",
      "scenes": [
        {
          "scene_index": number,
          "duration_seconds": number,
          "narration": string,
          "narration_word_count": number,
          "visual_description": string,
          "pexels_video_query": string,
          "pexels_image_query": string,
          "pixabay_video_query": string,
          "pixabay_image_query": string,
          "visual_type": "video|image",
          "emotion": "inspiring|educational|dramatic|calm|exciting|curious|urgent",
          "retention_device": "pattern_interrupt|open_loop|payoff|curiosity_gap|social_proof|null",
          "text_overlay": string|null,
          "text_overlay_position": "top|middle|bottom|null",
          "transition": "cut|fade|dissolve",
          "color_grade": "warm|cool|neutral|cinematic|vibrant",
          "music_intensity": "low|medium|high"
        }
      ]
    }
  ],
  "background_music_mood": "uplifting|dramatic|calm|energetic|mysterious|tense",
  "color_grade_global": "warm|cool|neutral|cinematic|vibrant",
  "style_notes": string,
  "seo_title": string,
  "seo_description": string,
  "suggested_thumbnail_scene_index": number
}

STRICT RULES:
1. narration_word_count = duration_seconds * 2.5 (150wpm). Calculate this precisely.
   If narration has wrong word count, rewrite it until it matches.
2. Hook must land in first 3 seconds. Make it impossible to skip.
3. Every pexels/pixabay query: 2-4 concrete nouns only. No adjectives of quality.
   GOOD: "city skyline night timelapse" BAD: "beautiful amazing urban landscape"
4. Shorts: total_duration_seconds <= 58, target_resolution = 1080x1920,
   max 1 chapter, pattern_interrupt every 10 seconds.
5. Documentary: minimum 3 chapters, use open_loop in chapter 1, payoff in final chapter.
6. Explainer: build concept progressively, use curiosity_gap between chapters.
7. Every 5th scene must have a retention_device (not null) — this keeps viewers watching.
8. suggested_thumbnail_scene_index: pick the scene with highest visual impact.
9. Return ONLY the JSON object. Nothing before or after it.
"""

STYLE_USER_PROMPTS = {
    "explainer": """
Create a clear, educational explainer video about: {topic}
Target audience: {target_audience}
Tone: {tone}
Duration: {duration_minutes} minutes

Structure:
- Chapter 1: Hook + "here's what most people get wrong about {topic}"
- Chapter 2-N: Build concept from basics to advanced, one idea per chapter
- Final chapter: Summary + surprising insight + soft CTA

Use analogies. Every complex idea needs a real-world comparison.
Insert curiosity gaps between chapters ("but here's where it gets interesting...").
""",

    "documentary": """
Create a cinematic documentary script about: {topic}
Tone: {tone}
Duration: {duration_minutes} minutes

Structure (strict):
- Cold open: Drop into the most dramatic moment first (in medias res)
- Chapter 1: Context + open loop ("the answer to this question changed everything")
- Middle chapters: Build tension, introduce conflict or mystery
- Climax: The revelation or turning point
- Resolution: What this means for the viewer

Use the "but/therefore" rule: never connect scenes with "and then".
Always connect with "but" (conflict) or "therefore" (consequence).
""",

    "shorts": """
Create a viral short-form script about: {topic}
Duration: EXACTLY 55 seconds. Not more.
Target: {target_audience}

STRUCTURE (non-negotiable):
Seconds 0-3: HOOK — one sentence that creates immediate curiosity or shock
Seconds 4-35: THE MEAT — one key insight, explained fast with one example
Seconds 36-50: THE TWIST — something they didn't expect
Seconds 51-55: CTA — "follow for more like this" or question to answer in comments

Pattern interrupt every 10 seconds: change visual, change pacing, add text.
Write like you're talking to a friend, not presenting to a boardroom.
""",

    "faceless": """
Create a faceless YouTube video script about: {topic}
Tone: {tone}
Duration: {duration_minutes} minutes
Target: {target_audience}

RETENTION RULES (YouTube algorithm):
- Hook in first 30 seconds must tease the payoff ("by the end of this video...")
- Pattern interrupt every 60 seconds minimum
- Use "you" constantly — make it personal
- Open loop in intro, close it only in final chapter
- Every chapter title should be a curiosity trigger

STOCK FOOTAGE OPTIMIZATION:
- Write visual_descriptions assuming only generic stock footage exists
- Avoid anything requiring specific people, brands, or locations
- Prefer: nature, technology, abstract, city, workspace, hands, data visualizations
""",

    "slideshow": """
Create a slideshow-style video script about: {topic}
Tone: {tone}
Duration: {duration_minutes} minutes

Each scene = one slide concept. Keep narration tight (max 25 words per scene).
Visual type should always be "image" for slides.
text_overlay should always be set — it's the slide title.
Color grade: neutral or cool for professional feel.
""",
}


# ---------------------------------------------------------------------------
# Public pipeline entrypoint
# ---------------------------------------------------------------------------

async def generate(req: dict) -> dict:
    """Pipeline-facing wrapper. Unpacks an order requirements dict and returns
    a validated script dict."""
    topic = req.get("topic") or req.get("subject") or "an interesting topic"
    style = (req.get("style") or "faceless").lower()
    if style not in STYLE_USER_PROMPTS:
        style = "faceless"
    duration_minutes = req.get("duration_minutes") or req.get("duration") or 3
    tone = req.get("tone") or "engaging and authoritative"
    target_audience = req.get("target_audience") or "a general audience"

    return await generate_script(
        topic=topic,
        style=style,
        duration_minutes=duration_minutes,
        tone=tone,
        target_audience=target_audience,
    )


async def generate_script(topic, style, duration_minutes, tone, target_audience) -> dict:
    """Main script generation. Calls the configured AI provider. Validates.
    Auto-retries up to 3 times, appending the previous error so the model
    self-corrects."""
    user_prompt = STYLE_USER_PROMPTS[style].format(
        topic=topic,
        duration_minutes=duration_minutes,
        tone=tone,
        target_audience=target_audience,
    )

    last_error: Exception | None = None
    for attempt in range(3):
        log.info("Generating script (style=%s) attempt %d/3", style, attempt + 1)
        raw = await ai.complete(
            system=MASTER_SYSTEM_PROMPT,
            user=user_prompt,
            max_tokens=6000,
            temperature=0.7,
        )
        raw = raw.strip()

        try:
            script = json.loads(_strip_json(raw))
            validate_and_fix_script(script)  # raises ValueError if unfixable
            log.info("Script validated: %s (%d chapters)",
                     script.get("title"), len(script.get("chapters", [])))
            return script
        except (json.JSONDecodeError, ValueError, KeyError, TypeError) as e:
            last_error = e
            log.warning("Script attempt %d failed: %s", attempt + 1, e)
            if attempt == 2:
                raise RuntimeError(
                    f"AI provider failed to produce valid script after 3 attempts: {e}"
                )
            user_prompt += f"\n\nPREVIOUS ATTEMPT FAILED: {e}. Fix this and try again."

    # Unreachable, but keeps type-checkers happy.
    raise RuntimeError(f"AI provider failed to produce valid script: {last_error}")


async def optimize_search_queries(visual_description: str) -> dict:
    """When Pexels/Pixabay returns 0 results, use the provider's fast model to
    generate 5 alternative search terms ranked by stock-footage availability."""
    raw = await ai.fast_complete(
        user=f"""
Visual description: "{visual_description}"

Generate 5 stock footage search queries for this scene, ranked best to worst.
Each query: 2-4 concrete nouns only. No adjectives.
Return JSON only: {{"queries": ["query1", "query2", "query3", "query4", "query5"]}}
""",
        max_tokens=200,
    )
    raw = raw.strip()
    try:
        data = json.loads(_strip_json(raw))
        queries = [q for q in data.get("queries", []) if isinstance(q, str) and q.strip()]
        return {"queries": queries}
    except (json.JSONDecodeError, ValueError, TypeError):
        return {"queries": []}


# ---------------------------------------------------------------------------
# Validation / repair
# ---------------------------------------------------------------------------

def validate_and_fix_script(script: dict) -> None:
    """Validate schema and auto-fix what can be fixed. Raises ValueError for
    unfixable issues. Mutates `script` in place."""
    if not isinstance(script, dict):
        raise ValueError("Top-level script is not a JSON object")

    required_top = ["title", "style", "total_duration_seconds", "chapters",
                    "background_music_mood", "target_resolution"]
    for field in required_top:
        if field not in script:
            raise ValueError(f"Missing required field: {field}")

    if not isinstance(script["chapters"], list) or not script["chapters"]:
        raise ValueError("chapters must be a non-empty list")

    # Sensible defaults for optional top-level fields the pipeline relies on.
    script.setdefault("color_grade_global", "neutral")
    script.setdefault("hook", script["title"])
    script.setdefault("style_notes", "")
    script.setdefault("seo_title", script["title"])
    script.setdefault("seo_description", "")

    total_scene_duration = 0.0
    scene_counter = 0

    for chapter in script["chapters"]:
        if "scenes" not in chapter or not isinstance(chapter["scenes"], list) \
                or not chapter["scenes"]:
            raise ValueError(
                f"Chapter {chapter.get('chapter_index', '?')} has no scenes"
            )

        for scene in chapter["scenes"]:
            for f in ("narration", "duration_seconds"):
                if f not in scene:
                    raise ValueError(
                        f"Scene {scene.get('scene_index', '?')} missing '{f}'"
                    )

            # Normalize duration to a positive number.
            try:
                scene["duration_seconds"] = max(1.0, float(scene["duration_seconds"]))
            except (TypeError, ValueError):
                raise ValueError(
                    f"Scene {scene.get('scene_index', '?')} has invalid duration"
                )

            # Auto-fix / record actual narration word count.
            actual_words = len(str(scene["narration"]).split())
            expected_words = int(scene["duration_seconds"] * WORDS_PER_SECOND)
            scene["narration_word_count"] = actual_words

            # Off by >20%: tolerated — assembly retimes to real audio length.
            if expected_words and abs(actual_words - expected_words) > expected_words * 0.2:
                log.debug(
                    "Scene %s narration off pace (%d words vs ~%d expected)",
                    scene.get("scene_index"), actual_words, expected_words,
                )

            # Fill defaults so downstream tools never KeyError.
            scene.setdefault("scene_index", scene_counter)
            scene.setdefault("visual_type", "video")
            scene.setdefault("visual_description", scene.get("narration", "")[:120])
            scene.setdefault("emotion", "educational")
            scene.setdefault("transition", "cut")
            scene.setdefault("color_grade", script["color_grade_global"])
            scene.setdefault("music_intensity", "medium")
            scene.setdefault("text_overlay", None)
            scene.setdefault("text_overlay_position", None)
            scene.setdefault("retention_device", None)
            _fill_query_defaults(scene)

            # Validate the two primary Pexels queries are 2-5 words.
            for query_field in ["pexels_video_query", "pexels_image_query"]:
                query = scene.get(query_field, "")
                words = len(str(query).split())
                if words < 2 or words > 5:
                    raise ValueError(
                        f"Scene {scene['scene_index']}: {query_field} must be "
                        f"2-4 words, got: '{query}'"
                    )

            total_scene_duration += scene["duration_seconds"]
            scene_counter += 1

    # Keep the declared total honest.
    script["total_duration_seconds"] = round(total_scene_duration, 2)

    if script["style"] == "shorts" and script["total_duration_seconds"] > 60:
        raise ValueError(
            f"Shorts must be <= 60 seconds, got {script['total_duration_seconds']}"
        )


def _fill_query_defaults(scene: dict) -> None:
    """Derive sensible 2-4 word search queries from the visual description when
    a query field is missing, empty, or out of bounds."""
    fallback = _derive_query(scene.get("visual_description") or scene.get("narration", ""))
    for f in ("pexels_video_query", "pexels_image_query",
              "pixabay_video_query", "pixabay_image_query"):
        q = str(scene.get(f, "") or "").strip()
        if not (2 <= len(q.split()) <= 5):
            scene[f] = fallback


def _derive_query(text: str) -> str:
    """Reduce arbitrary text to 3 concrete-ish lowercase keywords."""
    words = re.findall(r"[A-Za-z]+", text.lower())
    stop = {"the", "a", "an", "of", "and", "to", "in", "on", "with", "for",
            "is", "are", "this", "that", "as", "at", "by", "it", "its", "into",
            "from", "your", "you", "we", "our", "their", "his", "her"}
    keep = [w for w in words if w not in stop and len(w) > 2]
    chosen = keep[:3] if len(keep) >= 2 else (keep + ["abstract", "background"])[:3]
    return " ".join(chosen) if chosen else "abstract background motion"


def _strip_json(raw: str) -> str:
    """Strip accidental markdown fences and isolate the outermost JSON object."""
    s = raw.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\n?", "", s)
        s = re.sub(r"\n?```$", "", s).strip()
    start = s.find("{")
    end = s.rfind("}")
    if start != -1 and end != -1 and end > start:
        return s[start:end + 1]
    return s


# ---------------------------------------------------------------------------
# Style / rendering helpers (consumed by voice.py and assembly.py)
# ---------------------------------------------------------------------------

def get_voice_for_style(style: str) -> str:
    """Return best Kokoro voice ID for each video style."""
    return {
        "documentary": "am_echo",    # deep, authoritative
        "shorts": "af_sky",          # energetic, young
        "explainer": "af_heart",     # warm, clear
        "faceless": "af_heart",      # warm, clear
        "slideshow": "af_heart",     # warm, clear
    }.get(style, "af_heart")


def get_ffmpeg_color_grade(grade: str) -> str:
    """Return FFmpeg vf filter string for each color grade ('' = no filter)."""
    return {
        "warm": "curves=r='0/0 0.5/0.6 1/1':g='0/0 0.5/0.5 1/0.9':b='0/0 0.5/0.4 1/0.8'",
        "cool": "curves=r='0/0 0.5/0.4 1/0.8':g='0/0 0.5/0.5 1/0.95':b='0/0 0.5/0.6 1/1'",
        "cinematic": "curves=all='0/0 0.5/0.45 1/0.9',vignette=PI/4",
        "vibrant": "eq=saturation=1.4:contrast=1.05:brightness=0.02",
        "neutral": "",
    }.get(grade, "")
