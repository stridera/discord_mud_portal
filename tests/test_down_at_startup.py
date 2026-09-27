import asyncio
import contextlib
import os
import socket
import sys

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_THIS_DIR))
sys.path.insert(0, _THIS_DIR)

import discord

from config import Config
from main import MudClient
from stub_mud import consume_login


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


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
    # Nothing is listening on this port yet - the MUD is "down" from
    # process start, before mud_loop ever makes a single connection
    # attempt.
    port = _free_port()
    client = _make_client('127.0.0.1', port)
    client.down_announce_seconds = 0.2
    client.stable_seconds = 0.2
    client.backoff_initial = 0.1
    sent = []

    async def fake_send(text):
        sent.append(text)

    client._send_to_discord = fake_send

    mud_task = asyncio.create_task(client.mud_loop())
    watcher_task = asyncio.create_task(client._down_watcher())
    server = None
    connections = []
    try:
        # "lost" must fire even though we've never once connected.
        for _ in range(50):
            if any('lost' in s for s in sent):
                break
            await asyncio.sleep(0.1)
        assert any('lost' in s for s in sent), sent
        assert sum('lost' in s for s in sent) == 1

        # Now bring the MUD up. Accept the connection but don't send
        # anything back, so "restored" can only come from the
        # stable_seconds timer, not the prompt_end shortcut.
        async def handle(reader, writer):
            connections.append(writer)
            await consume_login(reader)
            await asyncio.sleep(3600)

        server = await asyncio.start_server(handle, '127.0.0.1', port)

        for _ in range(50):
            if any('restored' in s for s in sent):
                break
            await asyncio.sleep(0.1)
        assert any('restored' in s for s in sent), sent
        assert sum('restored' in s for s in sent) == 1
    finally:
        for t in (mud_task, watcher_task):
            t.cancel()
        for t in (mud_task, watcher_task):
            with contextlib.suppress(asyncio.CancelledError):
                await t
        for w in connections:
            with contextlib.suppress(Exception):
                w.close()
        if server is not None:
            server.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(server.wait_closed(), timeout=2)


def test_mud_down_at_startup_announces_lost_then_restored():
    asyncio.run(_run_test())
