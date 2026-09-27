import asyncio
import contextlib
import os
import sys

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_THIS_DIR))
sys.path.insert(0, _THIS_DIR)

import discord

import main
from config import Config
from main import MudClient
from stub_mud import consume_login, start_stub_server


def _make_client(host: str, port: int) -> MudClient:
    config = Config(
        mud_host=host,
        mud_port=port,
        mud_user='discord',
        mud_password='pw',
        discord_token='x',
        discord_guild=1,
        discord_channel=1,
        who_interval=600,
        mud_room='1200',
        mud_max_line=250,
    )
    intents = discord.Intents.default()
    return MudClient(config, activity=discord.Game(name='test'), intents=intents)


class FakeResponse:
    async def defer(self):
        return


class FakeFollowup:
    def __init__(self, sink):
        self._sink = sink

    async def send(self, message, *args, **kwargs):
        self._sink.append(message)


class FakeInteraction:
    def __init__(self):
        self.sent = []
        self.response = FakeResponse()
        self.followup = FakeFollowup(self.sent)


async def _run_live_who_test():
    connections = []

    async def handle(reader, writer):
        connections.append(writer)
        await consume_login(reader)
        # Response to the login's own 'who'.
        writer.write(b'*** Active players\r\n[ 50 Hum Cl] Alice\r\n--END--\r\n')
        await writer.drain()
        while True:
            line = await reader.readline()
            if not line:
                return
            if line.strip() == b'who':
                # A "fresh" list, different from the one seen at login, so
                # the test can tell the /who reply was live, not cached.
                writer.write(b'*** Active players\r\n[ 50 Hum Cl] Alice\r\n[ 50 Hum Cl] Bob\r\n--END--\r\n')
                await writer.drain()

    server, port = await start_stub_server(handle)
    try:
        client = _make_client('127.0.0.1', port)
        task = asyncio.create_task(client.mud_loop())
        try:
            for _ in range(50):
                if 'Alice' in client.online:
                    break
                await asyncio.sleep(0.1)
            assert 'Alice' in client.online

            interaction = FakeInteraction()
            await client._who_command(interaction)

            assert len(interaction.sent) == 1
            message = interaction.sent[0]
            assert 'Bob' in message
            assert '(cached)' not in message
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
    finally:
        # The stub connection's readline() loop is left open (it never
        # closes on its own) - force it shut so the server doesn't hang
        # waiting for it.
        for w in connections:
            with contextlib.suppress(Exception):
                w.close()
        server.close()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(server.wait_closed(), timeout=2)


def test_who_command_resolves_with_fresh_list():
    asyncio.run(_run_live_who_test())


async def _run_who_timeout_test(monkeypatch):
    connections = []

    # A server that logs in but never answers a subsequent 'who'.
    async def handle(reader, writer):
        connections.append(writer)
        await consume_login(reader)
        writer.write(b'*** Active players\r\n[ 50 Hum Cl] Alice\r\n--END--\r\n')
        await writer.drain()
        await asyncio.sleep(3600)  # never respond again

    server, port = await start_stub_server(handle)
    try:
        client = _make_client('127.0.0.1', port)
        monkeypatch.setattr(main, 'WHO_LIVE_TIMEOUT', 0.2)

        task = asyncio.create_task(client.mud_loop())
        try:
            for _ in range(50):
                if 'Alice' in client.online:
                    break
                await asyncio.sleep(0.1)
            assert 'Alice' in client.online

            interaction = FakeInteraction()
            await client._who_command(interaction)

            assert len(interaction.sent) == 1
            message = interaction.sent[0]
            assert 'Alice' in message
            assert '(cached)' in message
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
    finally:
        # The stub deliberately never closes (asyncio.sleep(3600)) - force
        # it shut so the server doesn't hang waiting for it.
        for w in connections:
            with contextlib.suppress(Exception):
                w.close()
        server.close()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(server.wait_closed(), timeout=2)


def test_who_command_falls_back_to_cached_on_timeout(monkeypatch):
    asyncio.run(_run_who_timeout_test(monkeypatch))
