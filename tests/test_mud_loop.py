import asyncio
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


async def _run_test() -> None:
    connections = []

    async def handle(reader, writer):
        connections.append(writer)
        await consume_login(reader)
        writer.write(b'*** Active players\r\n')
        writer.write(b'[ 50 Hum Cl] Bob\r\n')
        writer.write(b'--END--\r\n')
        writer.write(b"Alice gossips, 'hello'\r\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server, port = await start_stub_server(handle)

    async with server:
        client = _make_client('127.0.0.1', port)
        sent = []
        online_snapshots = []

        async def fake_send(text):
            # This fires for the gossip line, which the stub sends right
            # after the who-list - i.e. after client.online has been
            # populated but before the socket close (and mud_loop's
            # finally, which clears client.online) is observed. This is a
            # reliable synchronization point; polling client.online on a
            # wall-clock timer is not, since a local, already-buffered
            # connection can run the whole who-list -> EOF sequence in well
            # under a millisecond.
            online_snapshots.append(list(client.online))
            sent.append(text)

        # Bypass Discord entirely: exercise only the MUD-facing logic.
        client._send_to_discord = fake_send

        task = asyncio.create_task(client.mud_loop())
        try:
            # Wait for a first connection, disconnect, and a reconnect
            # (mud_loop's backoff starts at 2s).
            for _ in range(150):
                if len(connections) >= 2:
                    break
                await asyncio.sleep(0.1)
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert len(connections) >= 2, 'mud_loop did not reconnect after the MUD closed the socket'
        assert any('Bob' in snap for snap in online_snapshots), 'who-list line was never parsed into client.online'
        assert any('hello' in line for line in sent)


def test_mud_loop_reconnects_and_parses_who():
    asyncio.run(_run_test())
