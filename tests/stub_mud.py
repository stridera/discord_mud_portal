"""A tiny in-process stand-in for the MUD, used by tests to exercise
main.MudClient.mud_loop() (connect/login/read/reconnect) without a real
MUD or a real Discord connection."""
import asyncio


async def start_stub_server(on_connect):
    """Start a server on 127.0.0.1:0 (OS-assigned port).

    `on_connect(reader, writer)` is called for every accepted connection;
    it is responsible for reading the login lines and writing whatever
    lines the test wants the client to see, then closing the connection.

    Returns (server, port).
    """
    server = await asyncio.start_server(on_connect, '127.0.0.1', 0)
    port = server.sockets[0].getsockname()[1]
    return server, port


async def consume_login(reader: asyncio.StreamReader, expected_lines: int = 8) -> list[bytes]:
    """Read the lines the client sends during login: username, password,
    and the blind command block (6 lines: '', '1', '1', 'goto <room>',
    'prompt --END--%_', 'who')."""
    lines = []
    for _ in range(expected_lines):
        line = await reader.readline()
        if not line:
            break
        lines.append(line)
    return lines
