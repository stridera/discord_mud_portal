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
from stub_mud import start_stub_server


def _make_client(host: str = 'h', port: int = 1, **overrides) -> MudClient:
    kwargs = dict(
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
    kwargs.update(overrides)
    config = Config(**kwargs)
    intents = discord.Intents.default()
    return MudClient(config, activity=discord.Game(name='test'), intents=intents)


class StubWriter:
    """Records writes; drain/close/wait_closed are all no-ops."""

    def __init__(self):
        self.written = []
        self.closed = False

    def write(self, data: bytes) -> None:
        self.written.append(data)

    async def drain(self) -> None:
        return

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return


class RacyWriter:
    """Simulates the mud_loop finally block nulling out client.to_mud while
    close() is still awaiting drain() on it."""

    def __init__(self, client: MudClient):
        self.client = client
        self.written = []
        self.closed = False

    def write(self, data: bytes) -> None:
        self.written.append(data)

    async def drain(self) -> None:
        self.client.to_mud = None
        raise ConnectionResetError('connection lost mid-drain')

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return


# --- (a) _writer drains the outbound queue onto the stream -----------------

async def _run_writer_test() -> None:
    client = _make_client()
    stub = StubWriter()
    client.outbound.put_nowait('who')

    task = asyncio.create_task(client._writer(stub))
    try:
        for _ in range(50):
            if stub.written:
                break
            await asyncio.sleep(0.02)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert stub.written == [b'who\n']


def test_writer_writes_queued_line():
    asyncio.run(_run_writer_test())


# --- (b) close() survives self.to_mud being nulled out mid-drain -----------

async def _run_close_race_test(monkeypatch) -> None:
    client = _make_client()
    writer = RacyWriter(client)
    client.to_mud = writer

    called = []

    async def fake_super_close(self):
        called.append(True)

    monkeypatch.setattr(discord.Client, 'close', fake_super_close)

    await client.close()  # must not raise despite the drain() race

    assert called == [True]
    assert writer.written == [b'quit\n']


def test_close_survives_to_mud_race(monkeypatch):
    asyncio.run(_run_close_race_test(monkeypatch))


# --- (c) a truncated MUD line must not raise / break the read loop ---------

async def _run_handle_line_truncated_logout_test() -> None:
    client = _make_client()
    sent = []

    async def fake_send(text):
        sent.append(text)

    client._send_to_discord = fake_send

    # Far fewer whitespace-separated tokens than the real line has.
    state = await client._handle_line('The world seems to pause', 0)

    assert state == 0
    assert sent == []


def test_handle_line_truncated_logout_does_not_raise():
    asyncio.run(_run_handle_line_truncated_logout_test())


async def _run_handle_line_truncated_login_test() -> None:
    client = _make_client()
    sent = []

    async def fake_send(text):
        sent.append(text)

    client._send_to_discord = fake_send

    state = await client._handle_line('The ground shakes slightly', 0)

    assert state == 0
    assert sent == []


def test_handle_line_truncated_login_does_not_raise():
    asyncio.run(_run_handle_line_truncated_login_test())


# --- (e) the exact bytes sent during login ----------------------------------

async def _run_login_bytes_test() -> None:
    expected = (
        'discord' + '\n' +
        'pw' + '\n' +
        '\n1\n1\ngoto 1200\nprompt --END--%_\nwho\n'
    ).encode()

    received = {}
    got_it = asyncio.Event()

    async def handle(reader, writer):
        data = await reader.readexactly(len(expected))
        received['data'] = data
        got_it.set()
        writer.close()
        await writer.wait_closed()

    server, port = await start_stub_server(handle)
    async with server:
        client = _make_client('127.0.0.1', port)
        task = asyncio.create_task(client.mud_loop())
        try:
            await asyncio.wait_for(got_it.wait(), timeout=5)
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    assert received['data'] == expected


def test_login_sends_exact_bytes():
    asyncio.run(_run_login_bytes_test())
