import asyncio
import os
import sys

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_THIS_DIR))
sys.path.insert(0, _THIS_DIR)

import aiohttp
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


class FakeWebhook:
    def __init__(self):
        self.calls = []

    async def send(self, content, username, allowed_mentions=None):
        self.calls.append({'content': content, 'username': username})


async def _run_gossip_webhook_test():
    client = _make_client()
    webhook = FakeWebhook()
    client.webhook = webhook
    bot_sent = []

    async def fake_bot_send(text):
        bot_sent.append(text)

    client._send_to_discord = fake_bot_send

    state = await client._handle_line("Bob gossips, 'hi there'", 0)

    assert state == 0
    assert bot_sent == []  # went through the webhook, not the bot
    assert len(webhook.calls) == 1
    assert webhook.calls[0]['username'] == 'Bob'
    # Surrounding single quotes stripped, but content itself preserved.
    assert webhook.calls[0]['content'] == 'hi there'


def test_gossip_uses_webhook_and_strips_quotes():
    asyncio.run(_run_gossip_webhook_test())


async def _run_gossip_no_quotes_test():
    client = _make_client()
    webhook = FakeWebhook()
    client.webhook = webhook

    await client._handle_line('Bob gossips, hi there', 0)

    assert webhook.calls[0]['content'] == 'hi there'  # unchanged, nothing to strip


def test_gossip_without_quotes_unaffected():
    asyncio.run(_run_gossip_no_quotes_test())


async def _run_disallowed_username_test(name, line):
    client = _make_client()
    webhook = FakeWebhook()
    client.webhook = webhook
    bot_sent = []

    async def fake_bot_send(text):
        bot_sent.append(text)

    client._send_to_discord = fake_bot_send

    await client._handle_line(line, 0)

    assert webhook.calls == []  # never used for a disallowed name
    assert bot_sent == [line]  # fell back to the plain bot message


def test_webhook_username_fallback_for_discord():
    asyncio.run(_run_disallowed_username_test('Discord', "Discord gossips, 'hi'"))


def test_webhook_username_fallback_for_clyde():
    asyncio.run(_run_disallowed_username_test('clyde', "clyde gossips, 'hi'"))


class _FakeResponse:
    status = 400
    reason = 'Bad Request'


async def _run_webhook_failure_falls_back_test():
    class FailingWebhook(FakeWebhook):
        async def send(self, content, username, allowed_mentions=None):
            raise discord.HTTPException(response=_FakeResponse(), message='boom')

    client = _make_client()
    client.webhook = FailingWebhook()
    bot_sent = []

    async def fake_bot_send(text):
        bot_sent.append(text)

    client._send_to_discord = fake_bot_send

    line = "Bob gossips, 'hi there'"
    await client._handle_line(line, 0)

    assert bot_sent == [line]


def test_webhook_send_failure_falls_back_to_bot():
    asyncio.run(_run_webhook_failure_falls_back_test())


async def _run_webhook_value_error_falls_back_test():
    # e.g. a partial/foreign webhook without a usable token slipping
    # through discovery: Webhook.send() raises ValueError in that case.
    class NoTokenWebhook(FakeWebhook):
        async def send(self, content, username, allowed_mentions=None):
            self.calls.append({'content': content, 'username': username})
            raise ValueError('This webhook does not have a token associated with it')

    client = _make_client()
    webhook = NoTokenWebhook()
    client.webhook = webhook
    bot_sent = []

    async def fake_bot_send(text):
        bot_sent.append(text)

    client._send_to_discord = fake_bot_send

    line = "Bob gossips, 'hi there'"
    await client._handle_line(line, 0)

    assert len(webhook.calls) == 1  # webhook was tried exactly once
    assert bot_sent == [line]  # ...then fell back to the bot exactly once


def test_webhook_value_error_falls_back_to_bot():
    asyncio.run(_run_webhook_value_error_falls_back_test())


async def _run_webhook_client_error_falls_back_test():
    class UnreachableWebhook(FakeWebhook):
        async def send(self, content, username, allowed_mentions=None):
            self.calls.append({'content': content, 'username': username})
            raise aiohttp.ClientError('connection reset')

    client = _make_client()
    webhook = UnreachableWebhook()
    client.webhook = webhook
    bot_sent = []

    async def fake_bot_send(text):
        bot_sent.append(text)

    client._send_to_discord = fake_bot_send

    line = "Bob gossips, 'hi there'"
    await client._handle_line(line, 0)

    assert len(webhook.calls) == 1
    assert bot_sent == [line]


def test_webhook_client_error_falls_back_to_bot():
    asyncio.run(_run_webhook_client_error_falls_back_test())
