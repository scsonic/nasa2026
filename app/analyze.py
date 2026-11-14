"""Gemini looks at an SVS visualization (frame + text) and writes a music brief."""
from __future__ import annotations

from typing import Any

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from .config import settings

_client: genai.Client | None = None


def client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(vertexai=True, project=settings.project, location=settings.gemini_location)
    return _client


class Mapping(BaseModel):
    visual_feature: str = Field(description="Something seen in the frame or the data, e.g. 'deep red warming over the Arctic'")
    musical_choice: str = Field(description="How it is expressed in sound, e.g. 'rising string glissando in the upper register'")


class Section(BaseModel):
    seconds: str = Field(description="Time span in the 30 s piece: '0-5', '5-10', '10-15', '15-20', '20-25' or '25-30'")
    visual: str = Field(description="What the matching video frame (or, without video, that stage of the data story) shows. Max 20 words")
    music: str = Field(description="What the music does in this section; it must differ a little from the section before. Max 20 words")


class Analysis(BaseModel):
    summary: str = Field(description="1-2 plain-language sentences: what this visualization shows and why it matters")
    visual_description: str = Field(description="Alt text for blind and low-vision listeners: what the frame looks like (colors, shapes, layout). Max 60 words")
    video_description: str = Field(description="Video analysis: what happens across the attached video frames, in time order - motion, what grows, shrinks, appears or moves, camera changes, the overall trend and any repeating cycle or frequency. 3-5 sentences. Empty string when no video frames are attached")
    sections: list[Section] = Field(description="Exactly 6 sections of about 5 seconds each, in order, that together make the 30 s piece. Each section varies a little from the previous one (instrument added or dropped, register, density, rhythm, dynamics) following the video frames in time order; without video, follow the arc of the data story")
    keywords: list[str] = Field(description="5 to 8 key words that drive the music")
    mood: str = Field(description="2-4 words")
    tempo_bpm: int = Field(description="Tempo in BPM, 50-160")
    musical_key: str = Field(description="e.g. 'D minor', 'E-flat major', 'C lydian'")
    instruments: list[str] = Field(description="3 to 6 instruments")
    mappings: list[Mapping] = Field(description="3 to 5 visual/data-to-sound mappings")
    lyria_prompt: str = Field(description="Rich English prompt for an instrumental text-to-music model, 70-130 words. Genre, instruments, tempo, key, mood, then the 30 seconds as six short sections in order ('opens with..., then..., ...'), each slightly different, matching `sections`. No artist or song names, no lyrics, no vocals.")
    negative_prompt: str = Field(description="Comma separated things to avoid, e.g. 'vocals, lyrics, distortion'")
    musicgen_prompt: str = Field(description="Concise English prompt for MusicGen, max 55 words, instrumental: genre, instruments, tempo, key, then the six-step progression in very few words (e.g. 'sparse pads -> pulse enters -> strings swell -> ...')")
    listening_guide: str = Field(description="2-3 sentences telling the listener how the music tells the story of the data")


INSTRUCTIONS = """You are the sound designer of "Earth Spins", a NASA data jukebox (NASA Space Apps 2026).
You translate one NASA Scientific Visualization Studio (SVS) visualization into a ~30 second
instrumental piece: a sonification-inspired soundscape that is faithful to the data AND pleasant to hear.

Study the attached visualization frame closely (if present): dominant colors and what they encode
(use the captions/colorbar), brightness, spatial density, texture, implied motion, geometry.
Combine it with the text below. Map concrete visual/scientific features to musical parameters
(pitch, register, harmony, rhythm, tempo, dynamics, timbre, instrumentation). For example:
warming reds -> brighter timbre and rising register; dense cloud cover -> thick sustained pads;
spiral storms -> swirling arpeggios; sparse dark ocean -> slow low drones; seasonal cycles -> repeating phrases.
Reflect the emotional weight of the science (urgency of change, beauty of Earth systems).

PHYSICAL ELEMENTS: look in the title, description, captions, keywords and images for the physical
state of what is shown, e.g. wind, fire, water, ice, rain, snow, clouds, dust, smoke, lightning, lava,
plasma, magnetic fields. These shape the sound directly, so let the dominant element set the TIMBRE
and texture, and name those instruments and sound qualities in both the Lyria and MusicGen prompts:
  wind / air -> breathy flutes, airy whooshing pads, filtered noise swells
  fire / heat / wildfire -> crackling textures, distorted or bright brass, driving taiko-like percussion
  water / ocean / rain -> flowing harp and piano arpeggios, liquid synth plucks, soft rolling waves of sound
  ice / snow / glaciers -> glassy bells, celesta, crystalline high register, cold sparse reverb
  storms / lightning -> dark rumbling low strings, sudden accents, thunderous timpani
  sun / plasma / space weather -> shimmering synths, bright metallic tones, pulsing arpeggiators
  land / rock / volcano -> deep earthy drums, low cello and bass, gritty textures
When several elements appear, blend them and let the most prominent one lead.

VIDEO: when video frames are attached they are stills from the first 30 seconds of the
visualization's video, one every 3 seconds in time order (0 s, 3 s, 6 s, ... 27 s, end).
Pay close attention to the changes in the video: compare each frame with the one before and
notice what moves, grows, shrinks, appears, fades or changes color, and how fast it happens.
Look for the TREND across all frames (rising, falling, spreading, shrinking, steady, accelerating)
and for any FREQUENCY or rhythm in it (daily or seasonal cycles, orbits, rotations, pulses, waves,
repeating patterns and roughly how often they repeat). Turn the trend into the overall arc of the
music (e.g. rising intensity, register or density) and the frequency into rhythm and tempo (e.g. a
fast repeating cycle -> a quicker pulse or ostinato; a slow cycle -> long phrases that return).
State the trend and the frequency in video_description.
Describe those changes (video_description) and build the music as SIX sections of ~5 seconds,
each following the frames that fall in its 5-second window, so the music follows what happens
on screen. Each section must differ a little from the one before. Without video frames, still write six sections that follow the arc of the data story
from the text and the main image, and leave video_description empty.
All prompts must be in English and describe instrumental music only."""


def build_context(item: dict[str, Any]) -> str:
    parts = [
        f"SVS ID: {item['id']}",
        f"TITLE: {item['title']}",
        f"TYPE: {item.get('page_type') or ''}",
        f"RELEASED: {item.get('release_date') or ''}",
        f"KEYWORDS: {', '.join(item.get('keywords') or [])}",
        f"MISSIONS: {', '.join(item.get('missions') or [])}",
        f"DESCRIPTION: {item.get('description') or ''}",
    ]
    for i, cap in enumerate(item.get("captions") or [], 1):
        parts.append(f"CAPTION {i}: {cap}")
    return "\n".join(parts)[:8000]


async def analyze(item: dict[str, Any], image: tuple[bytes, str] | None,
                  frames: list[tuple[float, bytes]] | None = None) -> dict[str, Any]:
    frames = frames or []
    contents: list[Any] = []
    if image:
        data, mime = image
        contents.append("Main image of this visualization:")
        contents.append(types.Part.from_bytes(data=data, mime_type=mime))
    for i, (t, jpg) in enumerate(frames):
        last = i == len(frames) - 1 and i > 0
        contents.append(f"Video frame {i + 1} of {len(frames)} at {t:.0f} s{' (end of the 30 s window)' if last else ''}:")
        contents.append(types.Part.from_bytes(data=jpg, mime_type="image/jpeg"))
    note = []
    note.append("(The main image is a frame of this visualization.)" if image else "(No main image available.)")
    note.append(f"({len(frames)} video frames from the first 30 s are attached, in time order.)" if frames
                else "(No video for this page: skip video analysis and rely on the page text and image.)")
    contents.append(INSTRUCTIONS + "\n\n--- VISUALIZATION ---\n" + build_context(item) + "\n" + "\n".join(note))
    resp = await client().aio.models.generate_content(
        model=settings.gemini_model,
        contents=contents,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=Analysis,
            temperature=0.9,
        ),
    )
    parsed = resp.parsed
    if parsed is None:
        parsed = Analysis.model_validate_json(resp.text)
    result = parsed.model_dump()
    if not frames:
        result["video_description"] = ""
    result["model"] = settings.gemini_model
    result["used_image"] = bool(image)
    result["video_frames_s"] = [round(t, 2) for t, _ in frames]
    return result
