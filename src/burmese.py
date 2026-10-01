"""Burmese text helpers: syllable counting and script sanity checks."""
from __future__ import annotations

import re

_ASAT = "်"
_VIRAMA = "္"
_DOT_BELOW = "့"

_CJK = re.compile(r"[㐀-鿿豈-﫿]")
# In Unicode Burmese the vowel sign E (U+1031) follows its consonant. Zawgyi stores it
# first, so an E at the start of a word is the most reliable Zawgyi tell.
_ZAWGYI_E = re.compile(r"(^|[\s၊။])ေ")


def _is_base(ch: str) -> bool:
    """Consonants, independent vowels and digits that can start a syllable."""
    cp = ord(ch)
    return 0x1000 <= cp <= 0x1021 or 0x1023 <= cp <= 0x102A or cp == 0x103F or 0x1040 <= cp <= 0x1049


def count_syllables(text: str) -> int:
    """Approximate spoken syllables in Burmese text.

    A consonant starts a syllable unless it is killed by asat (final consonant) or is the
    upper half of a stacked pair. The lower half of a stack starts a new syllable.
    Latin words count roughly one syllable per three letters.
    """
    count = 0
    n = len(text)
    for i, ch in enumerate(text):
        if not _is_base(ch):
            continue
        nxt = text[i + 1] if i + 1 < n else ""
        if nxt == _DOT_BELOW and i + 2 < n:
            nxt = text[i + 2]
        if nxt in (_ASAT, _VIRAMA):
            continue
        count += 1
    for word in re.findall(r"[A-Za-z]+", text):
        count += max(1, round(len(word) / 3))
    return count


_DIGITS = ["သုည", "တစ်", "နှစ်", "သုံး", "လေး", "ငါး", "ခြောက်", "ခုနစ်", "ရှစ်", "ကိုး"]
# (value, word, form used when more digits follow) — e.g. နှစ်ဆယ် but နှစ်ဆယ့်သုံး
_UNITS = [(10**6, "သန်း", "သန်း"), (10**5, "သိန်း", "သိန်း"), (10**4, "သောင်း", "သောင်း"),
          (1000, "ထောင်", "ထောင့်"), (100, "ရာ", "ရာ့"), (10, "ဆယ်", "ဆယ့်")]
_NUMBER = re.compile(r"[0-9၀-၉]+(?:,[0-9၀-၉]{3})*")


def number_words(n: int) -> str:
    """Spell an integer the way it is said in Burmese: 23 -> နှစ်ဆယ့်သုံး, 13 -> ဆယ့်သုံး."""
    if n == 0:
        return _DIGITS[0]
    if n >= 10**7:
        return "".join(_DIGITS[int(d)] for d in str(n))
    words = []
    for value, word, joined in _UNITS:
        q, n = divmod(n, value)
        if q:
            lead = "" if value == 10 and q == 1 and not words and n else _DIGITS[q]
            words.append(lead + (joined if n else word))
    if n:
        words.append(_DIGITS[n])
    return "".join(words)


def spell_numbers(text: str) -> str:
    """Replace digits (Latin or Myanmar) with Burmese number words, so the TTS reads them right."""
    def repl(m: re.Match) -> str:
        digits = m.group(0).replace(",", "").translate({0x1040 + i: str(i) for i in range(10)})
        return number_words(int(digits))
    return _NUMBER.sub(repl, text)


_DIGIT_WORD = "(?:တစ်|နှစ်|သုံး|လေး|ငါး|ခြောက်|ခုနစ်|ရှစ်|ကိုး)"
# Sound words the TTS stumbles over ("အဲ…" hesitations) when they open a line: အို… ဟိုး… ဟွန်း…
_INTERJECTION = re.compile(
    r"^\s*(?:(?:အို|ဟိုး|ဟို|အာ|အား|ဟွန်း|ဟမ်|ဟဟ|ဟဲဟဲ|ဟားဟား|အဲ|အင်း|ဟင်း|ဟေ|ဟေ့|ဟယ်|အော်|ဟာ|အိုး|ဟုတ်လား)"
    r"\s*[.…၊,!?]+\s*)+"
)
_QUOTE_PARTICLE = re.compile(r"^(?:လို့|ဆိုပြီး|တဲ့|ဆိုတဲ့)")


def clean_narration(text: str) -> str:
    """Tidy LLM narration for the TTS: no opening sound words, no ellipses, correct number forms."""
    stripped = _INTERJECTION.sub("", text)
    if stripped and not _QUOTE_PARTICLE.match(stripped):  # keep "ဟွန်း… လို့ အသံပြုတယ်"
        text = stripped
    text = re.sub(r"\s*(?:\.{2,}|…+)\s*", "၊ ", text)          # ellipses read as hesitations
    text = re.sub(r"\s*[—–]+\s*", "၊ ", text)
    text = re.sub(r"\s*[?!]+", "။", text)
    # Spoken number forms: နှစ်ဆယ်သုံး -> နှစ်ဆယ့်သုံး, တစ်ရာနှစ်ဆယ် -> တစ်ရာ့နှစ်ဆယ်, ထောင် likewise.
    # ဆယ်နှစ် is left alone: it usually means "ten years", not a misspelt 12.
    text = re.sub(rf"ဆယ်(?={_DIGIT_WORD})(?!နှစ်)", "ဆယ့်", text)
    text = re.sub(rf"(?<=[်း])ရာ(?={_DIGIT_WORD}(?:ဆယ်|ဆယ့်))", "ရာ့", text)
    text = re.sub(rf"ထောင်(?={_DIGIT_WORD}(?:ရာ|ဆယ်|ဆယ့်))", "ထောင့်", text)
    text = re.sub(r"(?:၊\s*){2,}", "၊ ", text)
    text = re.sub(r"^[၊\s]+|[၊\s]+(?=။)", "", text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    return text


def has_cjk(text: str) -> bool:
    return bool(_CJK.search(text))


def looks_like_zawgyi(text: str) -> bool:
    return bool(_ZAWGYI_E.search(text))
