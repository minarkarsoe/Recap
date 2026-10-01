"""Turn source subtitles into Burmese storytelling narration with a local LLM (Ollama)."""
from __future__ import annotations

import base64
import json
import logging
import math
import re
import time
from pathlib import Path
from typing import Any, Callable

import httpx

from .burmese import clean_narration, count_syllables, has_cjk, looks_like_zawgyi, spell_numbers

log = logging.getLogger(__name__)

NARRATOR_RULES = """\
You are a popular Burmese YouTube movie-recap narrator. You retell a Chinese donghua (xianxia, \
cultivation, wuxia animation) for a Myanmar audience from start to finish, in sync with the video: \
each line you write is spoken over the scene it describes, and your voice replaces the original \
dialogue. A viewer who cannot read {lang} must follow every line and everything that happens.

How you narrate:
1. Third person, as a storyteller. Call characters by name, spelled as the glossary gives it. If you \
do not know a name, describe the person (ဦးထုပ်ဆောင်းထားတဲ့ လူ၊ ကောင်တာက လူ).
2. Retell EVERY subtitle line, in order. Do not skip or summarize lines away. Work out who speaks \
from the images and the story, and always say who: dialogue is reported speech (…လို့ ဝမ်ဝူက \
ပြောလိုက်တယ်၊ …လို့ မေးတယ်) or a short quote closed with တဲ့ (မင်းက ပုန်ကန်ချင်နေတာလား တဲ့). Outside a \
quote you never speak as a character (no ကျွန်တော်/ငါ as the narrator).
3. Weave in what the images show: actions, faces, reactions, fights, places (သူ ပါးစပ်ဟောင်းသားနဲ့ \
ကြောင်သွားတယ်၊ ဓားကို ဆွဲထုတ်လိုက်တယ်). A beat without subtitles is a moment with no dialogue: \
narrate the action you see and build suspense.
4. Keep the story flowing from beat to beat with connectors such as အဲဒီအချိန်မှာ၊ ဒါပေမဲ့၊ ဒီတော့၊ \
ရုတ်တရက်၊ ဒီလိုနဲ့. Never repeat the previous narration.
5. Spoken Burmese (အပြောစကား) in Myanmar Unicode. Never literary endings (သည်၊ ၏၊ ၌). Vary endings \
(တယ်၊ လိုက်တယ်၊ တော့တယ်၊ ပါတော့တယ်၊ တဲ့၊ လေ). No Chinese characters, pinyin, English letters, emoji or \
digits. Never write sound words or interjections (အို၊ ဟိုး၊ အာ၊ ဟွန်း၊ ဟမ်၊ ဟဟ၊ အဲ) and never use "...": \
say what the sound means instead (သူ နှာခေါင်းရှုံ့ပြီး မကျေမနပ် ဖြစ်သွားတယ်).
Counts use the right classifier and the spoken number form: {money}, people လူငါးယောက်, blades ဓားတစ်လက်, animals ကျားတစ်ကောင်, 13 is ဆယ့်သုံး, \
21 is နှစ်ဆယ့်တစ်.
6. The narrator talks fast and almost without pause, so fill the time: each beat gives \
target_syllables and max_syllables. Write close to target_syllables and never above max_syllables. \
A syllable is one spoken beat such as တောင်၊ ပြီး၊ ကို. This holds for short beats too: 切 (tsk), \
哼 (hmph), 啊 (ah) and the like are sounds, not words, so narrate who makes the sound, how they \
react and what they do in the images instead of writing the sound alone.
7. Stay faithful: no events that are not in the subtitles or the images. A beat may carry "meaning": \
an English note on who says what and what happens. Trust it for speakers and meaning, but write only \
Burmese. Use a glossary name only when its exact characters appear in the subtitles.

Example of the voice (style only). Every Burmese name used as an example anywhere in these rules stands in for a real character: never copy one into your narration.
{example}
narration: ဒါကိုကြားတော့ ဝမ်ခွေက မဲ့ပြုံးလေးပြုံးပြီး မင်းလို အောက်ခြေအဆင့်က အသုံးမကျတဲ့ကောင်က ငါ့ကို ဘာလုပ်နိုင်မှာလဲ တဲ့။

Return JSON {"items": [{"index": n, "narration": "..."}]} with one item for every beat, in order."""

CONTEXT_SYSTEM = """\
You are a story editor preparing a Burmese voice-over of a Chinese animated series (donghua). Read \
the subtitle transcript and return JSON with:
- "summary": what happens in this part, 4 to 8 sentences of spoken Burmese in Myanmar Unicode.
- "names": every named character, sect, place, technique or cultivation term that matters. For each: \
"source" exactly as written in the subtitles, "burmese" a Burmese spelling that {names_hint}, and "note" a few Burmese words on who or what it is.
Names already in the glossary must keep their given Burmese spelling."""

CONTEXT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "names": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"source": {"type": "string"}, "burmese": {"type": "string"}, "note": {"type": "string"}},
                "required": ["source", "burmese", "note"],
            },
        },
    },
    "required": ["summary", "names"],
}

NARRATION_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"index": {"type": "integer"}, "narration": {"type": "string"}},
                "required": ["index", "narration"],
            },
        }
    },
    "required": ["items"],
}

SHORTEN_SCHEMA = {"type": "object", "properties": {"narration": {"type": "string"}}, "required": ["narration"]}

GLOSS_RULES = """\
You help a Burmese narrator understand a donghua scene. For each beat, read the {lang} \
subtitles and look at the beat's images (attached in the order listed), then explain in English:
- who speaks each line and to whom (use glossary names, otherwise describe the person, e.g. "the \
pawnshop owner");
- what each line means, translated faithfully{gloss_notes};
- what happens on screen (actions, faces, reactions).
For a beat without subtitles, describe only the action. Return JSON {"items": [{"index": n, "gloss": "..."}]}."""

GLOSS_SCHEMA = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": {
        "type": "object",
        "properties": {"index": {"type": "integer"}, "gloss": {"type": "string"}},
        "required": ["index", "gloss"]}}},
    "required": ["items"],
}

EDITOR_RULES = """\
You are the senior Burmese script editor of a popular movie-recap YouTube channel. A junior writer \
drafted narration for a donghua; each line comes with the {lang} subtitles it covers. The \
lines will be read aloud by a TTS voice, so every line must sound like natural spoken Burmese.

Fix each line:
- grammar, wrong or unnatural words, clumsy phrasing, and mistranslations: check the meaning and the \
speakers against the {lang} subtitles and the English "meaning" note, and correct them;
- counts: the right classifier and spoken number form ({money}; people လူငါးယောက်; 13 is ဆယ့်သုံး; \
21 is နှစ်ဆယ့်တစ်);
- dialogue must say who speaks (…လို့ ဝမ်ဝူက ပြောလိုက်တယ်, or a short quote closed with တဲ့); the \
narrator is third person and never ကျွန်တော်/ငါ outside a quote;
- remove sound words and interjections (အို၊ ဟိုး၊ အာ၊ ဟွန်း၊ ဟမ်၊ ဟဟ၊ အဲ), "..." and repeated phrases;
- keep names exactly as the glossary spells them, and keep the lively storyteller voice;
- keep the spoken register (တယ်၊ လိုက်တယ်၊ ပါတယ်၊ တဲ့၊ လို့၊ အဲဒီ). Never turn a line into written \
Burmese: no သည်၊ ၏၊ ၌၊ ၍၊ ဟု၊ ထို၊ သော၊ မည်;
- keep about the same length, never above max_syllables. Myanmar Unicode only, no digits or other scripts.
A line that is already good comes back unchanged. Return JSON {"items": [{"index": n, "narration": "..."}]} \
with every line."""


# The prompts above are written around a Chinese donghua with Chinese subtitles. Some shows ship
# only an English track, so the pieces that depend on the subtitle language live here and are swapped
# in by OllamaClient.rules(). replace() does the swapping, not format(): the prompts contain literal
# JSON braces.
LANGUAGE_NAMES = {"zh": "Chinese", "en": "English", "ja": "Japanese", "ko": "Korean"}

CHINESE_SLOTS = {
    "{money}": "money ငွေနှစ်ဆယ့်နှစ် (二十二两), never ကျပ်၊ ပြား၊ လုံး for 两",
    "{example}": "subtitles: 就凭你？一个炼气期的废物？  images: a young man sneering at another",
    "{names_hint}": "follows the Mandarin pronunciation (for example 萧炎 → ရှောင်ယန်)",
    "{gloss_notes}": ". Objects stay objects (玉佩 is a jade pendant, not a name); a glossary name applies "
                     "only when its exact characters appear. These uploads censor some words: 三 often stands "
                     "for 杀 (kill) or 死 (die), e.g. 三了 = killed, 我们会三吗 = will we die; and （可爱） replaces a "
                     "slur such as 怪物 (monster). Read them with their real meaning",
}

# Used for every other subtitle language: the names are already romanised, so nothing has to be
# sounded out from characters, and there is no censoring to see through.
LATIN_SLOTS = {
    "{money}": "money ငွေနှစ်ဆယ့်နှစ် (twenty-two taels of silver), never ကျပ်၊ ပြား၊ လုံး for taels",
    "{example}": "subtitles: You? A useless piece of trash still stuck at Qi Refining?  "
                 "images: a young man sneering at another",
    "{names_hint}": "follows how the subtitles spell the name (for example Lin Wan'er → လင်ဝမ်အာ)",
    "{gloss_notes}": ". Objects stay objects (a jade pendant is a thing, not somebody's name); a glossary "
                     "name applies only when the subtitles actually use it",
}


def language_name(code: str | None, fallback: str = "Chinese") -> str:
    """The subtitle language as the prompts name it: "zh-Hans" -> "Chinese".

    Subtitles attached by hand carry no language code, so the configured one stands.
    """
    return LANGUAGE_NAMES.get((code or "").split("-")[0].lower(), fallback)


def language_of(segments: list[dict], code: str | None, fallback: str = "Chinese") -> str:
    """The language the subtitles are really in, judged from the text and not just the track's code.

    A code can lie: one donghua ships its English subtitles under "zh". So a track that claims to
    be Chinese but carries no Chinese characters is read as a romanised one instead.
    """
    named = language_name(code, fallback)
    if named != "Chinese" or not segments:
        return named
    cjk = sum(1 for s in segments if has_cjk(s.get("text", "")))
    return "Chinese" if cjk >= 0.1 * len(segments) else "English"


def _finish(text: str) -> str:
    """Last touches on any LLM-written line: digits spelled out, sound words and ellipses removed."""
    return clean_narration(spell_numbers(text.strip()))


class LLMError(RuntimeError):
    pass


class OllamaClient:
    def __init__(self, cfg: dict[str, Any]):
        self.endpoint = cfg["endpoint"].rstrip("/")
        self.model = cfg["model"]
        self.temperature = cfg.get("temperature", 0.7)
        self.num_ctx = cfg.get("num_ctx", 16384)
        self.think = cfg.get("think")
        self.lang = cfg.get("source_language") or "Chinese"
        self.slots = {"{lang}": self.lang, **(CHINESE_SLOTS if self.lang == "Chinese" else LATIN_SLOTS)}
        self.http = httpx.Client(timeout=httpx.Timeout(900, connect=10))
        self.loaded = False

    def rules(self, prompt: str) -> str:
        """A prompt with this job's subtitle language written into it."""
        for slot, text in self.slots.items():
            prompt = prompt.replace(slot, text)
        return prompt

    def check(self) -> None:
        try:
            tags = self.http.get(f"{self.endpoint}/api/tags").json()
        except httpx.HTTPError as exc:
            raise LLMError(f"Ollama is not reachable at {self.endpoint}. Start Ollama first. ({exc})") from exc
        names = {m["name"] for m in tags.get("models", [])}
        if self.model not in names and f"{self.model}:latest" not in names:
            raise LLMError(f"Model {self.model} is not pulled. Run: ollama pull {self.model}")

    def chat_json(self, system: str, user: str, schema: dict, temperature: float | None = None,
                  images: list[Path] | None = None) -> dict:
        message: dict[str, Any] = {"role": "user", "content": user}
        if images:
            message["images"] = [base64.b64encode(p.read_bytes()).decode("ascii") for p in images]
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, message],
            "format": schema,
            "stream": False,
            "keep_alive": "10m",
            "options": {"temperature": self.temperature if temperature is None else temperature,
                        "num_ctx": self.num_ctx},
        }
        if self.think is not None:
            payload["think"] = self.think
        t0 = time.time()
        self.loaded = True
        resp = self.http.post(f"{self.endpoint}/api/chat", json=payload)
        if resp.status_code != 200:
            raise LLMError(f"Ollama returned {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        tokens = data.get("eval_count", 0)
        log.debug("LLM: %d tokens in %.1fs", tokens, time.time() - t0)
        try:
            return json.loads(data["message"]["content"])
        except (KeyError, json.JSONDecodeError) as exc:
            raise LLMError(f"LLM did not return valid JSON: {data.get('message', {}).get('content', '')[:300]}") from exc

    def unload(self) -> None:
        """Free the model's VRAM so the TTS model has room."""
        if not self.loaded:
            return
        try:
            self.http.post(f"{self.endpoint}/api/generate", json={"model": self.model, "keep_alive": 0})
            self.loaded = False
            log.info("Unloaded %s from Ollama", self.model)
        except httpx.HTTPError as exc:
            log.warning("Could not unload %s: %s", self.model, exc)


def _fmt_time(t: float) -> str:
    return f"{int(t // 60):02d}:{int(t % 60):02d}"


def _glossary_text(names: list[dict[str, str]]) -> str:
    if not names:
        return "(none yet)"
    return "\n".join(f"{n['source']} → {n['burmese']}" + (f"  ({n['note']})" if n.get("note") else "") for n in names)


def merge_names(names: list[dict[str, str]], glossary: dict[str, str]) -> list[dict[str, str]]:
    """De-duplicate names by source text; the user's glossary always wins."""
    out: dict[str, dict[str, str]] = {}
    for n in names:
        src = n.get("source", "").strip()
        if src and n.get("burmese", "").strip() and src not in out:
            out[src] = {"source": src, "burmese": n["burmese"].strip(), "note": n.get("note", "").strip()}
    for src, my in glossary.items():
        out.setdefault(src, {"source": src, "burmese": my, "note": ""})["burmese"] = my
    return list(out.values())


def build_context(client: OllamaClient, segments: list[dict], glossary: dict[str, str],
                  part_chars: int = 5000) -> dict[str, Any]:
    """Pass 1: read the whole transcript for plot summaries and a name glossary.

    Long transcripts are read in chunks; each chunk's summary is kept with its source-time range
    so narration for a stretch of video only needs the summaries around it.
    """
    chunks: list[list[dict]] = [[]]
    size = 0
    for seg in segments:
        if size + len(seg["text"]) > part_chars and chunks[-1]:
            chunks.append([])
            size = 0
        chunks[-1].append(seg)
        size += len(seg["text"]) + 8

    names = merge_names([], glossary)
    parts: list[dict[str, Any]] = []
    for i, chunk in enumerate(chunks, 1):
        log.info("Reading the story (chunk %d/%d)...", i, len(chunks))
        recent = " ".join(p["summary"] for p in parts[-3:])
        user = (
            f"Glossary so far:\n{_glossary_text(names)}\n\n"
            + (f"Story so far:\n{recent}\n\n" if recent else "")
            + "Subtitle transcript:\n" + "\n".join(f"[{_fmt_time(s['start'])}] {s['text']}" for s in chunk)
        )
        try:
            result = client.chat_json(client.rules(CONTEXT_SYSTEM), user, CONTEXT_SCHEMA, temperature=0.3)
        except LLMError as exc:
            # A chunk this size can push the reply past the context window, and the JSON then comes
            # back cut off mid-string. Reading the same stretch in halves leaves room for the reply.
            if len(chunk) < 2:
                raise
            log.warning("Chunk %d did not come back as JSON (%s); reading it in halves", i, exc)
            result = _context_in_halves(client, chunk, names, recent)
        parts.append({"start": chunk[0]["start"], "end": chunk[-1]["end"],
                      "summary": result.get("summary", "").strip()})
        names = merge_names(names + result.get("names", []), glossary)
    return {"parts": parts, "names": names}


def _context_in_halves(client: OllamaClient, chunk: list[dict], names: list[dict],
                       recent: str) -> dict[str, Any]:
    """Read one transcript chunk as two, and join the halves back into one summary."""
    summaries, found = [], []
    for half in (chunk[: len(chunk) // 2], chunk[len(chunk) // 2:]):
        if not half:
            continue
        user = (f"Glossary so far:\n{_glossary_text(names)}\n\n"
                + (f"Story so far:\n{recent}\n\n" if recent else "")
                + "Subtitle transcript:\n"
                + "\n".join(f"[{_fmt_time(s['start'])}] {s['text']}" for s in half))
        out = client.chat_json(client.rules(CONTEXT_SYSTEM), user, CONTEXT_SCHEMA, temperature=0.3)
        summaries.append(out.get("summary", "").strip())
        found += out.get("names", [])
        recent = " ".join(x for x in summaries if x)
    return {"summary": " ".join(s for s in summaries if s), "names": found}


def summary_for(context: dict[str, Any], start: float, end: float) -> str:
    """Summaries of the transcript chunks overlapping [start, end] (source seconds), plus the one before."""
    parts = context.get("parts") or [{"start": 0, "end": float("inf"), "summary": context.get("summary", "")}]
    hits = [i for i, p in enumerate(parts) if p["end"] >= start and p["start"] <= end]
    if not hits:
        return parts[-1]["summary"]
    return " ".join(p["summary"] for p in parts[max(0, hits[0] - 1): hits[-1] + 1] if p["summary"])


def _problem(text: str) -> str | None:
    if has_cjk(text):
        return "contains Chinese characters"
    if looks_like_zawgyi(text):
        return "looks like Zawgyi"
    # Anything that is not Myanmar script, digits, spaces or punctuation: gemma sometimes leaks tokens
    # such as "vong" or a stray Hangul syllable, which the TTS would mangle.
    if m := _FOREIGN.search(text):
        return f"contains non-Burmese text ({m.group(0)!r})"
    if m := _LITERARY.search(text):
        return f"literary style ({m.group(0)!r})"
    return None


_FOREIGN = re.compile(r"[^က-႟ꧠ-꧿ꩠ-ꩿ0-9\s.,!?'\"()\-–—…“”‘’:;]")
# Written-register markers that make a recap sound like a newspaper: သည် ၏ ၌ ၍ ဟု ထို… သော…
# (lookarounds keep spoken words such as သည်း၊ ထိုင်၊ ထိုး၊ သောက်၊ သောင်း from matching).
# "တစ်ခုတည်းသော" is fine in speech too, so သော after တည်း is allowed; nouns ending in သည်
# (ဖောက်သည် customer, ဧည့်သည် guest, ခရီးသည် traveller, ကုန်သည် merchant, ရောဂါသည် patient)
# are not the particle.
_LITERARY = re.compile(r"(?<!ဖောက်)(?<!ဧည့်)(?<!ခရီး)(?<!ကုန်)(?<!ရောဂါ)သည်(?!း)|၏|၌|၍|ဟု(?=[\s၊။]|$)"
                       r"|ထို(?![င်း]|က်)(?=[က-အ])|(?<!တည်း)သော(?![က-အ]်)(?=[\sက-အ]|$)")


def gloss_beats(client: OllamaClient, beats: list[dict], context: dict,
                frames: Callable[[dict], list[Path]] | None = None, batch_size: int = 8) -> None:
    """Pass 1.5: an English note per beat (speakers, meaning, action), stored as beat['gloss'].

    The model reads Chinese and describes images far more reliably in English than it writes
    Burmese, so settling "who says what" first keeps the Burmese pass from swapping speakers.
    """
    header = f"Story so far:\n{context['summary']}\n\nGlossary:\n{_glossary_text(context['names'])}\n\n"
    total = math.ceil(len(beats) / batch_size)
    for bi in range(0, len(beats), batch_size):
        batch = beats[bi:bi + batch_size]
        log.info("Understanding the scenes: batch %d/%d", bi // batch_size + 1, total)
        images: list[Path] = []
        listing = []
        for b in batch:
            item = {"index": b["index"], "subtitles": b["source"] or "(no dialogue)"}
            if frames:
                shots = frames(b)
                item["images"] = list(range(len(images) + 1, len(images) + len(shots) + 1))
                images += shots
            listing.append(item)
        try:
            result = client.chat_json(client.rules(GLOSS_RULES), header + "Beats:\n" + json.dumps(listing, ensure_ascii=False, indent=1),
                                      GLOSS_SCHEMA, temperature=0.2, images=images)
        except LLMError as exc:
            log.warning("Scene notes failed for this batch; narrating from the subtitles alone: %s", exc)
            continue
        by_index = {b["index"]: b for b in batch}
        for item in result.get("items", []):
            if (b := by_index.get(item.get("index"))) and item.get("gloss"):
                b["gloss"] = str(item["gloss"]).strip()


def write_narration(client: OllamaClient, beats: list[dict], context: dict, rate: float,
                    base_tempo: float = 1.0, max_tempo: float = 1.2, batch_size: int = 8, max_retries: int = 3,
                    frames: Callable[[dict], list[Path]] | None = None) -> list[dict]:
    """Pass 2: write one Burmese narration line per beat, in batches, carrying recent lines forward.

    ``rate`` is the narrator's natural syllables per second; clips are played ``base_tempo`` times
    faster and may be sped up to ``max_tempo``, which sets each beat's target and ceiling.
    ``frames`` returns the images to show the model for a beat.
    """
    for b in beats:
        b["target_syllables"] = max(3, math.floor(b["slot"] * rate * base_tempo * 0.95))
        b["max_syllables"] = max(b["target_syllables"], math.floor(b["slot"] * rate * max_tempo * 0.9))
    header = f"Story summary:\n{context['summary']}\n\nGlossary:\n{_glossary_text(context['names'])}\n\n"
    done: dict[int, str] = {}
    fallback: dict[int, str] = {}
    total_batches = math.ceil(len(beats) / batch_size)

    for bi in range(0, len(beats), batch_size):
        batch = beats[bi:bi + batch_size]
        log.info("Writing narration: batch %d/%d (beats %d-%d)",
                 bi // batch_size + 1, total_batches, batch[0]["index"], batch[-1]["index"])
        pending = list(batch)
        for attempt in range(1, max_retries + 1):
            recent = [done[i] for i in sorted(done)[-3:] if done[i]]
            images: list[Path] = []
            listing = []
            for b in pending:
                item = {"index": b["index"], "seconds": round(b["slot"], 1),
                        "target_syllables": b["target_syllables"], "max_syllables": b["max_syllables"],
                        "subtitles": b["source"] or "(no dialogue: narrate the action in the images)"}
                if b.get("gloss"):
                    item["meaning"] = b["gloss"]
                if frames:
                    shots = frames(b)
                    item["images"] = list(range(len(images) + 1, len(images) + len(shots) + 1))
                    images += shots
                listing.append(item)
            user = (
                header
                + ("Previous narration (continue from here, do not repeat):\n" + "\n".join(recent) + "\n\n" if recent else "")
                + ("Images are attached in the order their numbers are listed.\n" if images else "")
                + "Beats to narrate:\n" + json.dumps(listing, ensure_ascii=False, indent=1)
            )
            try:
                result = client.chat_json(client.rules(NARRATOR_RULES), user, NARRATION_SCHEMA, images=images)
            except LLMError as exc:
                log.warning("Attempt %d failed: %s", attempt, exc)
                continue
            wanted = {b["index"] for b in pending}
            for item in result.get("items", []):
                idx, text = item.get("index"), _finish(str(item.get("narration", "")))
                if idx not in wanted:
                    continue
                issue = _problem(text)
                if issue:
                    log.warning("Beat %s rejected (%s): %s", idx, issue, text)
                    if issue.startswith("literary"):
                        fallback[idx] = text  # stiff but readable: better than silence if retries fail
                    continue
                done[idx] = text
            pending = [b for b in pending if b["index"] not in done]
            if not pending:
                break
            log.info("Retrying %d beat(s) the model skipped", len(pending))
        for b in pending:
            if b["index"] in fallback:
                log.warning("Beat %d: keeping a written-style line after %d attempts", b["index"], max_retries)
                done[b["index"]] = fallback[b["index"]]
            else:
                log.warning("Beat %d has no narration after %d attempts; leaving it silent", b["index"], max_retries)
                done[b["index"]] = ""

    for b in beats:
        b["narration"] = done.get(b["index"], "")
    return beats


def polish(client: OllamaClient, beats: list[dict], context: dict, batch_size: int = 12) -> int:
    """Editor pass: a second read of every line for natural Burmese, counts and speaker attribution.

    Returns how many lines changed. A rewrite is dropped if it breaks the script rules or the budget.
    """
    lines = [b for b in beats if b["narration"]]
    header = f"Glossary:\n{_glossary_text(context['names'])}\n\n"
    changed = 0
    for bi in range(0, len(lines), batch_size):
        batch = lines[bi:bi + batch_size]
        log.info("Editing narration: %d/%d lines", min(bi + batch_size, len(lines)), len(lines))
        listing = [{"index": b["index"], "subtitles": b["source"] or "(no dialogue)",
                    **({"meaning": b["gloss"]} if b.get("gloss") else {}),
                    "narration": b["narration"], "max_syllables": b["max_syllables"]} for b in batch]
        try:
            result = client.chat_json(client.rules(EDITOR_RULES), header + "Lines:\n" + json.dumps(listing, ensure_ascii=False,
                                                                                    indent=1),
                                      NARRATION_SCHEMA, temperature=0.3)
        except LLMError as exc:
            log.warning("Editor pass failed for this batch, keeping the drafts: %s", exc)
            continue
        by_index = {b["index"]: b for b in batch}
        for item in result.get("items", []):
            b = by_index.get(item.get("index"))
            text = _finish(str(item.get("narration", "")))
            if not b or not text or text == b["narration"]:
                continue
            if _problem(text) or count_syllables(text) > b["max_syllables"] * 1.1:
                continue
            b.setdefault("draft", b["narration"])  # kept for comparing the editor's work
            b["narration"] = text
            changed += 1
    return changed


def enforce_budget(client: OllamaClient, beats: list[dict], context: dict,
                   tolerance: float = 1.2, tries: int = 2) -> None:
    """Rewrite lines that blow well past their syllable budget, before any audio is made."""
    long = [b for b in beats if count_syllables(b["narration"]) > b["max_syllables"] * tolerance]
    if not long:
        return
    log.info("%d line(s) are over their syllable budget; shortening them", len(long))
    for b in long:
        for _ in range(tries):
            new = shorten(client, b, context, b["max_syllables"], b["slot"])
            if new:
                b["narration"] = new
            if count_syllables(b["narration"]) <= b["max_syllables"] * tolerance:
                break


def shorten(client: OllamaClient, beat: dict, context: dict, max_syllables: int, seconds: float) -> str | None:
    """Rewrite one narration line to fit a tighter syllable budget."""
    current = beat["narration"]
    user = (
        f"Glossary:\n{_glossary_text(context['names'])}\n\n"
        f"This narration line is too long to speak in {seconds:.1f} seconds.\n"
        f"Current line ({count_syllables(current)} syllables): {current}\n"
        f"Original subtitles: {beat['source']}\n\n"
        f"Rewrite it with at most {max_syllables} syllables. Keep every line's meaning and the storyteller "
        f'voice. Return JSON {{"narration": "..."}}.'
    )
    try:
        text = _finish(str(client.chat_json(client.rules(NARRATOR_RULES), user, SHORTEN_SCHEMA).get("narration", "")))
    except LLMError as exc:
        log.warning("Could not shorten beat %d: %s", beat["index"], exc)
        return None
    if not text or _problem(text):
        return None
    return text
