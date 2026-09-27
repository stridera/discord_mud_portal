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


async def _run_test():
    connections = []

    async def handle(reader, writer):
        connections.append(writer)
        await consume_login(reader)
        if len(connections) == 1:
            # First connection: drop immediately after login, simulating a
            # short-lived connection that then stays down for a while.
            writer.close()
            await writer.wait_closed()
        else:
            # Reconnect: stays up so a "restored" announcement can fire.
            writer.write(b'*** Active players\r\n--END--\r\n')
            await writer.drain()
            await asyncio.sleep(3600)

    server, port = await start_stub_server(handle)
    try:
        client = _make_client('127.0.0.1', port)
        client.down_announce_seconds = 0.2
        sent = []

        async def fake_send(text):
            sent.append(text)

        client._send_to_discord = fake_send

        mud_task = asyncio.create_task(client.mud_loop())
        watcher_task = asyncio.create_task(client._down_watcher())
        try:
            for _ in range(150):
                lost = [s for s in sent if 'lost' in s]
                restored = [s for s in sent if 'restored' in s]
                if lost and restored:
                    break
                await asyncio.sleep(0.1)
        finally:
            for t in (mud_task, watcher_task):
                t.cancel()
            for t in (mud_task, watcher_task):
                with contextlib.suppress(asyncio.CancelledError):
                    await t

        lost = [s for s in sent if 'lost' in s]
        restored = [s for s in sent if 'restored' in s]
        assert len(lost) == 1, sent
        assert len(restored) == 1, sent
        assert len(connections) >= 2
    finally:
        # At least one connection (the second) is deliberately left open
        # (asyncio.sleep(3600)) to simulate a stable reconnect - force it
        # closed so the server doesn't hang waiting for it.
        for w in connections:
            with contextlib.suppress(Exception):
                w.close()
        server.close()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(server.wait_closed(), timeout=2)


def test_down_and_recovery_announced_exactly_once():
    asyncio.run(_run_test())


async def _run_short_flap_never_announces():
    # A drop shorter than the threshold must never announce anything.
    connections = []

    async def handle(reader, writer):
        connections.append(writer)
        await consume_login(reader)
        writer.close()
        await writer.wait_closed()

    server, port = await start_stub_server(handle)
    try:
        client = _make_client('127.0.0.1', port)
        client.down_announce_seconds = 5  # never reached within the test window
        # Speed up the backoff floor so several connect/drop cycles happen
        # inside the test's wall-clock budget (the default 2s floor would
        # only allow ~1 cycle in that time).
        client.backoff_initial = 0.05
        sent = []

        async def fake_send(text):
            sent.append(text)

        client._send_to_discord = fake_send

        mud_task = asyncio.create_task(client.mud_loop())
        watcher_task = asyncio.create_task(client._down_watcher())
        try:
            # Comfortably enough time for several fast flap/reconnect
            # cycles, still well under the 5s down_announce_seconds.
            for _ in range(30):
                if len(connections) >= 3:
                    break
                await asyncio.sleep(0.1)
        finally:
            for t in (mud_task, watcher_task):
                t.cancel()
            for t in (mud_task, watcher_task):
                with contextlib.suppress(asyncio.CancelledError):
                    await t

        assert sent == []
        assert len(connections) >= 3, connections
    finally:
        for w in connections:
            with contextlib.suppress(Exception):
                w.close()
        server.close()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(server.wait_closed(), timeout=2)


def test_short_flap_never_announces():
    asyncio.run(_run_short_flap_never_announces())
