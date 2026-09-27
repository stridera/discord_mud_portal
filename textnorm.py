"""Best-effort conversion of Discord's Unicode niceties (emoji, smart
punctuation, accented letters) into a plain-ASCII approximation.

Discord silently converts things like ':)' into the Unicode emoji U+1F642
before the bot ever sees the message; without this step, sanitize()'s
ascii encode/replace turns every one of those into a bare '?', which reads
as noise on the MUD side. This module is pure (no asyncio, no I/O) so it's
trivially unit-testable.
"""
import re
import unicodedata

# Discord's custom-emoji shortcode form, e.g. <:pepe:123456> or an animated
# <a:dance:99>, rendered back to plain :pepe:/:dance:.
_CUSTOM_EMOJI_RE = re.compile(r'<a?:(\w+):\d+>')

# Variation selectors, zero-width joiner, and Fitzpatrick skin-tone
# modifiers - stripped so e.g. a heart-with-variation-selector sequence
# collapses to its base heart character, and a 'thumbs up + skin tone'
# sequence collapses to plain thumbs up, before the emoticon/name lookups
# below run.
_STRIP_RE = re.compile('[︎️‍\U0001F3FB-\U0001F3FF]')

# Reverse of Discord's automatic ASCII-emoticon -> emoji conversion, for the
# common cases. Order doesn't matter (each entry maps one code point).
_EMOTICON_MAP = {
    '\U0001F642': ':)',    # slightly smiling face
    '\U0001F603': ':D',    # smiling face with open mouth
    '\U0001F604': ':D',    # smiling face with open mouth & smiling eyes
    '\U0001F600': ':D',    # grinning face
    '\U0001F609': ';)',    # winking face
    '\U0001F622': ':(',    # crying face
    '\U0001F641': ':(',    # slightly frowning face
    '\U0001F61E': ':(',    # disappointed face
    '\U0001F61B': ':P',    # face with stuck-out tongue
    '\U0001F61C': ';P',    # face with stuck-out tongue & winking eye
    '\U0001F62E': ':O',    # face with open mouth
    '\U0001F632': ':O',    # astonished face
    '\U0001F610': ':|',    # neutral face
    '\U0001F615': ':/',    # confused face
    '\U0001F606': 'xD',    # smiling face with open mouth & closed eyes
    '\U0001F602': 'xD',    # face with tears of joy
    '\U0001F601': ':D',    # grinning face with smiling eyes
    '\U0001F618': ':*',    # face throwing a kiss
    '\U0001F60E': 'B)',    # smiling face with sunglasses
    '\U0001F620': '>:(',   # angry face
    '\U0001F607': 'O:)',   # smiling face with halo
    '\U0001F608': '>:)',   # smiling face with horns
    '❤': '<3',        # heavy black heart
    '\U0001F494': '</3',   # broken heart
    '\U0001F44D': '(y)',   # thumbs up sign
    '\U0001F44E': '(n)',   # thumbs down sign
    '\U0001F914': ':thinking:',  # thinking face
}

# Smart-punctuation -> plain-ASCII. NFKC (applied after this) doesn't touch
# these since they're canonical characters, not compatibility decompositions.
_SMART_PUNCT_MAP = {
    '‘': "'", '’': "'", '‚': "'",   # ' ' '
    '“': '"', '”': '"', '„': '"',   # " " "
    '–': '-', '—': '-',                  # - -
    '…': '...',                                # ...
    ' ': ' ',                                  # nbsp
    '×': 'x',                                  # multiplication sign
}

# Emoji code-point blocks (in addition to general category 'S*').
_EMOJI_RANGES = (
    (0x1F000, 0x1FAFF),
    (0x2600, 0x27BF),
)


def _is_emoji_like(ch: str) -> bool:
    if unicodedata.category(ch).startswith('S'):
        return True
    code = ord(ch)
    return any(start <= code <= end for start, end in _EMOJI_RANGES)


def to_ascii_friendly(text: str) -> str:
    """Convert emoji/smart-punctuation/accents in `text` to their closest
    plain-ASCII form. Anything left over afterwards is genuinely outside
    this function's scope and is left for the caller's own ascii
    encode/replace step."""
    if not text:
        return text

    # 1. Discord custom emoji shortcodes.
    text = _CUSTOM_EMOJI_RE.sub(r':\1:', text)

    # 2. Strip variation selectors / ZWJ / skin-tone modifiers.
    text = _STRIP_RE.sub('', text)

    # 3. Reverse the common emoji -> ASCII-emoticon conversions.
    text = ''.join(_EMOTICON_MAP.get(ch, ch) for ch in text)

    # 4. Any other symbol/emoji-ish character -> ':unicode_name:'.
    parts = []
    for ch in text:
        if ch.isascii():
            parts.append(ch)
        elif _is_emoji_like(ch):
            name = unicodedata.name(ch, 'emoji').lower().replace(' ', '_')
            parts.append(f':{name}:')
        else:
            parts.append(ch)
    text = ''.join(parts)

    # 5. Smart punctuation, then fold compatibility/fullwidth forms.
    text = ''.join(_SMART_PUNCT_MAP.get(ch, ch) for ch in text)
    text = unicodedata.normalize('NFKC', text)

    # 6. Decompose accented letters and drop the combining marks, so
    #    e.g. 'e with acute' -> 'e', 'n with tilde' -> 'n'.
    text = unicodedata.normalize('NFKD', text)
    text = ''.join(ch for ch in text if unicodedata.category(ch) != 'Mn')

    # 7. Whatever's still non-ASCII (rare) is left for the caller.
    return text
