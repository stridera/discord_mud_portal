import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import sanitize, truncate_preserving_escapes


def test_newline_injection_removed():
    result = sanitize('hello\nkill self')
    assert '\n' not in result
    assert result == 'hello kill self'


def test_carriage_return_and_newline_removed():
    result = sanitize('hello\r\nworld')
    assert '\r' not in result and '\n' not in result
    assert result == 'hello world'


def test_control_chars_removed():
    result = sanitize('a\x07b\x1bc\x00d')
    assert result == 'a b c d'


def test_iac_byte_removed():
    # 0xFF (as a character, U+00FF 'y with diaeresis') is not a C0 control
    # char; it's handled by textnorm's accent-folding (-> 'y') rather than
    # the ascii encode/replace step, since it happens to be an accented
    # letter - either way it must never survive as raw non-ASCII.
    result = sanitize('a\xffb')
    assert '\xff' not in result
    assert result.isascii()
    assert result == 'ayb'


def test_del_char_removed():
    result = sanitize('a\x7fb')
    assert result == 'a b'


def test_truncation_to_limit():
    result = sanitize('x' * 300, max_len=250)
    assert len(result) == 250


def test_accented_letters_folded_to_ascii():
    # textnorm.to_ascii_friendly() decomposes and drops the accent, so this
    # no longer falls through to the '?' replacement.
    result = sanitize('café')
    assert result.isascii()
    assert result == 'cafe'


def test_genuinely_unconvertable_non_ascii_replaced():
    # A CJK character has no ASCII-friendly mapping in textnorm, so it
    # still falls through to the ascii encode/replace step.
    result = sanitize('中')
    assert result.isascii()
    assert result == '?'


def test_empty_input():
    assert sanitize('') == ''
    assert sanitize(None) == ''


def test_prefix_counts_toward_limit():
    line = sanitize('gossip ' + 'a' * 300 + ': hi', max_len=250)
    assert len(line) == 250
    assert line.startswith('gossip ')


def test_no_trailing_space_after_truncation():
    # 'a ' repeated lands exactly on a space at the truncation boundary;
    # the result must not keep that trailing space.
    result = sanitize('a ' * 200, max_len=10)
    assert not result.endswith(' ')
    assert len(result) <= 10


def test_mud_color_codes_escaped():
    result = sanitize('@Bob & co')
    assert result == '@@Bob && co'


def test_color_code_escaping_happens_before_truncation():
    # Escaping happens before truncation, so the doubled characters count
    # toward max_len (i.e. '&' -> '&&' can itself get cut in a way that's
    # still pair-safe).
    result = sanitize('&&&&', max_len=3)
    assert result == '&&'  # 4 literal '&' -> 8 chars '&&&&&&&&', cut to 3 -> pair-safe 2


def test_truncate_preserving_escapes_drops_dangling_half_pair():
    # An even run of '&' at the cut point is left alone...
    assert truncate_preserving_escapes('ab&&cd', 4) == 'ab&&'
    # ...but an odd run (i.e. the cut lands mid-pair) drops the last char.
    assert truncate_preserving_escapes('ab&&&cd', 5) == 'ab&&'
    assert truncate_preserving_escapes('ab@@@cd', 5) == 'ab@@'


def test_truncate_preserving_escapes_no_trailing_escape_chars():
    assert truncate_preserving_escapes('abc', 3) == 'abc'
    assert truncate_preserving_escapes('', 5) == ''
    # A lone '&' at the very end is a dangling half pair -> dropped.
    assert truncate_preserving_escapes('a&', 2) == 'a'


def test_emoji_normalized_before_color_escaping():
    # Text-normalisation (emoji -> ':)' etc.) runs before color-code
    # escaping, so a mention rendered as '@Bob' still gets doubled.
    result = sanitize('hi \U0001F642 @Bob')
    assert result == 'hi :) @@Bob'
