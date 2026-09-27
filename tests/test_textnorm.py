import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from textnorm import to_ascii_friendly


def test_slightly_smiling_face_becomes_smiley():
    assert to_ascii_friendly('\U0001F642') == ':)'


def test_emoji_inline_in_text():
    assert to_ascii_friendly('hi \U0001F642') == 'hi :)'


def test_heart_with_variation_selector_becomes_heart_emoticon():
    assert to_ascii_friendly('❤️') == '<3'


def test_unmapped_emoji_uses_unicode_name():
    assert to_ascii_friendly('\U0001F389') == ':party_popper:'  # party popper


def test_custom_emoji_shortcode():
    assert to_ascii_friendly('<:pepe:123456>') == ':pepe:'


def test_animated_custom_emoji_shortcode():
    assert to_ascii_friendly('<a:dance:99>') == ':dance:'


def test_smart_punctuation_and_accents():
    assert to_ascii_friendly('café “quoted” – ok…') == 'cafe "quoted" - ok...'


def test_thumbs_up_with_skin_tone_modifier_strips_modifier():
    # U+1F44D THUMBS UP SIGN + U+1F3FD EMOJI MODIFIER FITZPATRICK TYPE-4
    assert to_ascii_friendly('\U0001F44D\U0001F3FD') == '(y)'


def test_plain_ascii_unchanged():
    assert to_ascii_friendly('plain ascii, nothing to see here!') == 'plain ascii, nothing to see here!'


def test_empty_input():
    assert to_ascii_friendly('') == ''
