import asyncio
import os
import sys

import pytest

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_THIS_DIR)
sys.path.insert(0, _REPO_ROOT)
sys.path.insert(0, _THIS_DIR)

import discord

from config import Config
from main import MudClient
from rules import Rules

MUD_TOML = os.path.join(_REPO_ROOT, 'mud.toml')


def _make_client() -> MudClient:
    config = Config(
        mud_host='h',
        mud_port=1,
        mud_user='discord',
        mud_password='pw',
        discord_token='x',
        discord_guild=1,
        discord_channel=1,
        who_interval=600,
        mud_room='1200',
        mud_max_line=250,
        mud_rules=MUD_TOML,
    )
    intents = discord.Intents.default()
    return MudClient(config, activity=discord.Game(name='test'), intents=intents)


def test_rules_toml_loads():
    rules = Rules.load(MUD_TOML)
    assert rules.login.prompt_end == '--END--'
    assert rules.login.quit_command == 'quit'
    assert rules.who.start_prefix == '*** Active'
    assert [e.name for e in rules.events] == [
        'gossip', 'login', 'logout', 'levelup', 'leveldown', 'death',
    ]
    assert rules.outbound.gossip == 'gossip &b{name}&0&3: {text}'


SAMPLE_CASES = [
    # (line, expected_sent_text, online_before, online_after)
    ("Bob gossips, 'hi there'", "Bob gossips, 'hi there'", [], []),
    ('The ground shakes slightly with the arrival of Bob.', 'Bob has logged in.', [], ['Bob']),
    ('The world seems to pause momentarily as Bob leaves.', 'Bob has logged out.', ['Bob'], []),
    ('Bob advanced to level 10!', 'Bob advanced to level 10!', [], []),
    ('Bob lost level', 'Bob lost level', [], []),
    ('[ Bob was killed by a goblin ]', 'Bob was killed by a goblin ]!', ['Bob'], []),
]


@pytest.mark.parametrize('line,expected_text,online_before,online_after', SAMPLE_CASES)
def test_sample_lines_match_previous_behaviour(line, expected_text, online_before, online_after):
    asyncio.run(_run_sample_case(line, expected_text, online_before, online_after))


async def _run_sample_case(line, expected_text, online_before, online_after):
    client = _make_client()
    client.online = list(online_before)
    sent = []

    async def fake_send(text):
        sent.append(text)

    client._send_to_discord = fake_send

    state = await client._handle_line(line, 0)

    assert state == 0
    assert sent == [expected_text]
    assert client.online == online_after


def test_bad_regex_raises_system_exit(tmp_path):
    bad_toml = tmp_path / 'bad.toml'
    bad_toml.write_text(
        '''
[login]
sequence = ["{user}"]
prompt_end = "--END--"
quit_command = "quit"

[who]
start_prefix = "*** Active"
entry = '^\\[[^]]+\\] (?P<name>\\w+)'

[[events]]
name = "broken"
pattern = "[unclosed("
'''
    )
    with pytest.raises(SystemExit):
        Rules.load(bad_toml)


def test_missing_key_raises_system_exit(tmp_path):
    bad_toml = tmp_path / 'missing.toml'
    bad_toml.write_text(
        '''
[login]
sequence = ["{user}"]
prompt_end = "--END--"
# quit_command missing

[who]
start_prefix = "*** Active"
entry = '^\\[[^]]+\\] (?P<name>\\w+)'
'''
    )
    with pytest.raises(SystemExit):
        Rules.load(bad_toml)


def _minimal_toml(outbound_gossip: str) -> str:
    return f'''
[login]
sequence = ["{{user}}"]
prompt_end = "--END--"
quit_command = "quit"

[who]
start_prefix = "*** Active"
entry = '^\\[[^]]+\\] (?P<name>\\w+)'

[outbound]
gossip = "{outbound_gossip}"
'''


def test_outbound_missing_raises_system_exit(tmp_path):
    bad_toml = tmp_path / 'no_outbound.toml'
    bad_toml.write_text(
        '''
[login]
sequence = ["{user}"]
prompt_end = "--END--"
quit_command = "quit"

[who]
start_prefix = "*** Active"
entry = '^\\[[^]]+\\] (?P<name>\\w+)'
'''
    )
    with pytest.raises(SystemExit):
        Rules.load(bad_toml)


def test_outbound_unknown_field_raises_system_exit(tmp_path):
    bad_toml = tmp_path / 'unknown_field.toml'
    bad_toml.write_text(_minimal_toml('gossip {name}: {text} {oops}'))
    with pytest.raises(SystemExit):
        Rules.load(bad_toml)


def test_outbound_known_fields_load_fine(tmp_path):
    good_toml = tmp_path / 'ok.toml'
    good_toml.write_text(_minimal_toml('gossip &b{name}&0: {text}'))
    rules = Rules.load(good_toml)
    assert rules.outbound.gossip == 'gossip &b{name}&0: {text}'
