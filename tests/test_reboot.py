import asyncio
import os
import sys

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_THIS_DIR))
sys.path.insert(0, _THIS_DIR)

import discord

from config import Config
from main import MudClient


def _make_client(**overrides) -> MudClient:
    kwargs = dict(
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
    )
    kwargs.update(overrides)
    config = Config(**kwargs)
    intents = discord.Intents.default()
    return MudClient(config, activity=discord.Game(name='test'), intents=intents)


class FakePermissions:
    def __init__(self, administrator: bool):
        self.administrator = administrator


class FakeMember:
    """Member-like user: has guild_permissions, unlike a plain discord.User."""

    def __init__(self, administrator: bool, name='Alice', user_id=42):
        self.guild_permissions = FakePermissions(administrator)
        self.name = name
        self.id = user_id

    def __str__(self):
        return self.name


class FakeUser:
    """Plain (non-Member) user: no guild_permissions at all, e.g. in a DM."""

    def __init__(self, name='Bob', user_id=99):
        self.name = name
        self.id = user_id

    def __str__(self):
        return self.name


class FakeResponse:
    def __init__(self, sink):
        self._sink = sink

    async def send_message(self, message, ephemeral=False):
        self._sink.append({'message': message, 'ephemeral': ephemeral})


class FakeInteraction:
    def __init__(self, user):
        self.user = user
        self.sent = []
        self.response = FakeResponse(self.sent)


async def _run_denied_test(user):
    client = _make_client()
    shutdown_calls = []

    async def fake_shutdown():
        shutdown_calls.append(True)

    client._shutdown = fake_shutdown
    interaction = FakeInteraction(user)

    await client._reboot_command(interaction)

    assert len(interaction.sent) == 1
    assert interaction.sent[0]['message'] == 'Administrator permission required.'
    assert interaction.sent[0]['ephemeral'] is True
    assert shutdown_calls == []


def test_reboot_denied_for_non_admin_member():
    asyncio.run(_run_denied_test(FakeMember(administrator=False)))


def test_reboot_denied_for_non_member_user():
    asyncio.run(_run_denied_test(FakeUser()))


async def _run_allowed_test():
    client = _make_client()
    shutdown_calls = []

    async def fake_shutdown():
        shutdown_calls.append(True)

    client._shutdown = fake_shutdown
    interaction = FakeInteraction(FakeMember(administrator=True))

    await client._reboot_command(interaction)

    assert len(interaction.sent) == 1
    assert interaction.sent[0]['message'] == 'Rebooting...'
    assert interaction.sent[0]['ephemeral'] is True
    assert shutdown_calls == [True]


def test_reboot_allowed_for_admin_sends_reply_then_shuts_down():
    asyncio.run(_run_allowed_test())


def test_reboot_command_has_default_admin_permissions():
    client = _make_client()
    command = client.tree.get_command('reboot')
    assert command is not None
    assert command.default_permissions is not None
    assert command.default_permissions.administrator is True
