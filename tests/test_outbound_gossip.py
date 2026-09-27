import asyncio
import contextlib
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


class StubWriter:
    def __init__(self):
        self.written = []

    def write(self, data: bytes) -> None:
        self.written.append(data)

    async def drain(self) -> None:
        return


class FakeAuthor:
    def __init__(self, display_name, user_id=1):
        self.display_name = display_name
        self.id = user_id


class FakeChannel:
    def __init__(self, channel_id):
        self.id = channel_id


class FakeMessage:
    def __init__(self, author, channel_id, clean_content, webhook_id=None):
        self.author = author
        self.channel = FakeChannel(channel_id)
        self.clean_content = clean_content
        self.webhook_id = webhook_id


async def _run_on_message_test():
    client = _make_client()
    message = FakeMessage(FakeAuthor('Strider'), channel_id=1, clean_content='@Bob hi')

    await client.on_message(message)

    stub = StubWriter()
    task = asyncio.create_task(client._writer(stub))
    try:
        for _ in range(50):
            if stub.written:
                break
            await asyncio.sleep(0.01)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert stub.written == [b'gossip &bStrider&0&3: @@Bob hi\n']


def test_on_message_default_template_escapes_and_formats():
    asyncio.run(_run_on_message_test())


async def _run_forged_newline_code_test():
    # '&_' is the MUD's own newline code. Without escaping, a message like
    # this would forge a second, attacker-controlled MUD command/gossip
    # line. Doubling '&' (-> '&&_') neutralises it: the MUD sees a literal
    # '&' followed by '_', not its newline code.
    client = _make_client()
    message = FakeMessage(
        FakeAuthor('Strider'), channel_id=1, clean_content="hi &_Chinok gossips, 'x'",
    )

    await client.on_message(message)

    stub = StubWriter()
    task = asyncio.create_task(client._writer(stub))
    try:
        for _ in range(50):
            if stub.written:
                break
            await asyncio.sleep(0.01)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert len(stub.written) == 1  # exactly one line reached the MUD
    payload = stub.written[0]
    assert payload.count(b'\n') == 1  # only the trailing newline we add
    assert payload == b"gossip &bStrider&0&3: hi &&_Chinok gossips, 'x'\n"


def test_on_message_escapes_forged_mud_newline_code():
    asyncio.run(_run_forged_newline_code_test())
