import asyncio
import contextlib
import logging
import re
import signal
import sys
import time
from typing import Callable

import aiohttp
import discord
from discord import app_commands

from config import Config
from ratelimit import RateLimiter
from rules import Rules
from textnorm import to_ascii_friendly

logger = logging.getLogger('mud_portal')

ANSI_ESCAPE_RE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')

# Control characters that must never reach the MUD unescaped: C0 controls
# (including CR/LF) and DEL. The telnet IAC byte (0xFF) is not ASCII, so the
# ascii encode/replace step below already turns it (and any other non-ASCII
# character) into '?'.
_CONTROL_RE = re.compile(r'[\x00-\x1f\x7f]')
_WHITESPACE_RE = re.compile(r'\s+')

# Discord rejects webhook usernames containing 'clyde' or 'discord'
# (case-insensitive).
_DISALLOWED_WEBHOOK_NAME_RE = re.compile(r'clyde|discord', re.IGNORECASE)

WHO_TRUNCATE_AT = 1900
WHO_LIVE_TIMEOUT = 10


def escape_mud_colors(text: str) -> str:
    """Double the MUD's color-code trigger characters ('&' and '@') so
    relayed Discord text (e.g. a mention rendered as '@Bob') is never
    misinterpreted by the MUD as a color code."""
    return text.replace('&', '&&').replace('@', '@@')


def truncate_preserving_escapes(text: str, max_len: int) -> str:
    """Truncate to `max_len` characters without ever cutting a doubled
    '&&'/'@@' escape pair in half: if the truncated string ends with an odd
    run of '&' or '@', drop that last character too."""
    truncated = text[:max_len]
    if not truncated:
        return truncated
    last = truncated[-1]
    if last in ('&', '@'):
        run_len = len(truncated) - len(truncated.rstrip(last))
        if run_len % 2 == 1:
            truncated = truncated[:-1]
    return truncated


def sanitize(text: str, max_len: int = 250) -> str:
    """Make a string safe to send as a single line to the MUD.

    - Control characters (including CR/LF) are collapsed to a single space,
      so a message can never smuggle in a second MUD command via a newline.
    - Unicode niceties (emoji, smart punctuation, accents) are converted to
      a plain-ASCII approximation - e.g. Discord's auto-converted ':)' -
      before the ascii encode/replace step below, so they don't all just
      collapse to '?'. See textnorm.to_ascii_friendly().
    - Any remaining non-ASCII characters (including the telnet IAC byte,
      0xFF) are replaced (the MUD only speaks ASCII).
    - Runs of whitespace are collapsed to one space and the result is
      trimmed.
    - MUD color codes ('&' and '@') are doubled so they can never be
      misinterpreted as color codes by the MUD; this happens after the
      steps above and before truncation, so the doubled characters count
      toward `max_len`.
    - The result is truncated to `max_len` characters (never splitting a
      doubled color-code escape pair) and trimmed again, so truncation can
      never leave a trailing space. Callers should pass the *whole*
      outgoing line (including any "gossip name: " prefix) so the prefix
      counts against the limit too.
    """
    if not text:
        return ''
    cleaned = _CONTROL_RE.sub(' ', text)
    cleaned = to_ascii_friendly(cleaned)
    cleaned = cleaned.encode('ascii', errors='replace').decode('ascii')
    cleaned = _WHITESPACE_RE.sub(' ', cleaned).strip()
    cleaned = escape_mud_colors(cleaned)
    return truncate_preserving_escapes(cleaned, max_len).rstrip()


def is_valid_webhook_username(name: str) -> bool:
    """Discord rejects webhook usernames containing 'clyde' or 'discord'."""
    return not _DISALLOWED_WEBHOOK_NAME_RE.search(name)


def strip_surrounding_quotes(text: str) -> str:
    """Strip one pair of surrounding single quotes, if present."""
    if len(text) >= 2 and text[0] == "'" and text[-1] == "'":
        return text[1:-1]
    return text


def relative_ts(ts: float) -> str:
    """Discord markdown for a relative timestamp (renders client-side)."""
    return f'<t:{int(ts)}:R>'


def format_who(names: list[str], cached: bool = False, header: str = '') -> str:
    """Render a who-list for Discord: markdown-escaped, truncated to
    WHO_TRUNCATE_AT characters with an '... and N more' suffix, optionally
    prefixed with a header and/or marked as a cached (stale) list."""
    if not names:
        body = 'No one is online'
    else:
        escaped = [discord.utils.escape_markdown(n) for n in names]
        body = '\n'.join(escaped)
        if len(body) > WHO_TRUNCATE_AT:
            kept: list[str] = []
            length = 0
            for name in escaped:
                extra = len(name) + (1 if kept else 0)
                if length + extra > WHO_TRUNCATE_AT:
                    break
                kept.append(name)
                length += extra
            body = '\n'.join(kept) + f'\n... and {len(escaped) - len(kept)} more'
    suffix = ' (cached)' if cached else ''
    return f'{header}{body}{suffix}'


class MudClient(discord.Client):
    def __init__(
        self,
        config: Config,
        activity: discord.BaseActivity,
        intents: discord.Intents,
        rules: Rules | None = None,
    ):
        super().__init__(intents=intents, activity=activity, allowed_mentions=discord.AllowedMentions.none())
        self.tree = app_commands.CommandTree(self)
        self.guild = discord.Object(id=config.discord_guild)

        self.mud_host = config.mud_host
        self.mud_port = config.mud_port
        self.mud_user = config.mud_user
        self.mud_password = config.mud_password
        self.mud_room = config.mud_room
        self.mud_max_line = config.mud_max_line
        self.who_interval = config.who_interval
        self.mud_channel = config.discord_channel
        self.down_announce_seconds = config.mud_down_announce_seconds
        self.stable_seconds = config.mud_stable_seconds
        # Exposed as instance attributes (rather than local constants inside
        # mud_loop) so tests can shrink them to exercise several
        # connect/drop cycles quickly.
        self.backoff_initial = 2
        self.backoff_max = 60

        self.rules = rules if rules is not None else Rules.load(config.mud_rules)

        self.to_mud: asyncio.StreamWriter | None = None
        self.from_mud: asyncio.StreamReader | None = None
        self.online: list[str] = []
        self.outbound: asyncio.Queue[str] = asyncio.Queue(maxsize=100)
        self.connected = False

        self._announced = False
        self.mud_task: asyncio.Task | None = None
        self.who_task: asyncio.Task | None = None
        self.down_watcher_task: asyncio.Task | None = None

        # Live /who: resolved by _handle_line when a fresh who-list finishes.
        self._who_future: asyncio.Future | None = None

        # Status/announcement bookkeeping. The MUD is considered "down"
        # from process start (not just from the first observed drop) so a
        # MUD that's already unreachable at startup still gets announced
        # after the threshold.
        self.connected_since: float | None = None
        self.disconnected_since: float | None = time.time()
        self._disconnect_monotonic: float | None = time.monotonic()
        self._down_announced = False
        self._connection_stable = False
        self._ever_connected = False
        self.reconnect_count = 0
        self.last_mud_line_at: float | None = None
        self.start_time = time.time()

        # Discord -> MUD rate limiting.
        self.user_rate_limiter = RateLimiter(config.rate_user_msgs, config.rate_user_seconds)
        self.global_rate_limiter = RateLimiter(config.rate_global_msgs, config.rate_global_seconds)

        # Optional webhook so MUD gossip can appear under the speaking
        # player's name instead of the bot's.
        self.webhook: discord.Webhook | None = None
        if config.discord_webhook_url:
            self.webhook = discord.Webhook.from_url(config.discord_webhook_url, client=self)
        self._webhook_discovery_done = self.webhook is not None

        # Wired by run_bot to the same stop_event SIGTERM/SIGINT use, so
        # /reboot triggers an identical, single, well-understood shutdown
        # path. Falls back to closing directly if unset (e.g. a client
        # built without run_bot, such as in a script or test).
        self._trigger_shutdown: Callable[[], None] | None = None

        self.tree.command(name='who')(self._who_command)
        self.tree.command(name='status')(self._status_command)
        reboot_command = self.tree.command(name='reboot')(self._reboot_command)
        app_commands.default_permissions(administrator=True)(reboot_command)

    # ---- slash commands -------------------------------------------------

    async def _who_command(self, interaction: discord.Interaction) -> None:
        """Lists currently online players, live if the MUD is connected."""
        try:
            await interaction.response.defer()
        except discord.HTTPException as e:
            logger.info('Failed to defer /who interaction: %s', e)
            return
        if not self.connected:
            message = format_who(self.online, header='MUD is not connected.\n')
            await self._safe_followup(interaction, message, 'who')
            return

        future = self._who_future
        if future is None or future.done():
            future = asyncio.get_running_loop().create_future()
            self._who_future = future
        try:
            self.outbound.put_nowait('who')
        except asyncio.QueueFull:
            logger.warning('Outbound queue full; dropping /who request')

        try:
            # shield() so a timeout here (or in a second concurrent /who)
            # doesn't cancel the future for other callers awaiting the same
            # one.
            fresh = await asyncio.wait_for(asyncio.shield(future), timeout=WHO_LIVE_TIMEOUT)
            message = format_who(fresh)
        except (asyncio.TimeoutError, TimeoutError):
            message = format_who(self.online, cached=True)
        await self._safe_followup(interaction, message, 'who')

    async def _safe_followup(self, interaction: discord.Interaction, message: str, command: str) -> None:
        try:
            await interaction.followup.send(message)
        except Exception as e:
            logger.warning('Failed to respond to /%s: %s', command, e)

    async def _status_command(self, interaction: discord.Interaction) -> None:
        """Reports connection/queue/webhook status. Ephemeral."""
        lines = [f'MUD connected: {"yes" if self.connected else "no"}']
        if self.connected and self.connected_since:
            lines.append(f'Connected since: {relative_ts(self.connected_since)}')
        elif self.disconnected_since:
            lines.append(f'Disconnected since: {relative_ts(self.disconnected_since)}')
        if self.last_mud_line_at:
            lines.append(f'Last MUD line received: {relative_ts(self.last_mud_line_at)}')
        else:
            lines.append('Last MUD line received: never')
        lines.append(f'Reconnects since start: {self.reconnect_count}')
        lines.append(f'Process started: {relative_ts(self.start_time)}')
        lines.append(f'Outbound queue size: {self.outbound.qsize()}')
        lines.append(f'Online players: {len(self.online)}')
        lines.append(f'Webhook active: {"yes" if self.webhook is not None else "no"}')
        try:
            await interaction.response.send_message('\n'.join(lines), ephemeral=True)
        except Exception as e:
            logger.warning('Failed to respond to /status: %s', e)

    async def _reboot_command(self, interaction: discord.Interaction) -> None:
        """Admin-only: cleanly shuts down (quits the MUD, closes the
        client) so a process supervisor with Restart=always brings the bot
        back up. Visible/usable only to Administrators (both the Discord
        UI hint via default_permissions and a runtime check, since the UI
        hint is not enforced by Discord)."""
        user = interaction.user
        # interaction.user is a plain discord.User (no guild_permissions at
        # all) rather than a Member when the command is used outside a
        # guild (e.g. a DM) - getattr(..., None) treats that as denied too.
        guild_permissions = getattr(user, 'guild_permissions', None)
        is_admin = bool(guild_permissions is not None and guild_permissions.administrator)
        if not is_admin:
            await self._reply_ephemeral(interaction, 'Administrator permission required.', 'reboot')
            return

        await self._reply_ephemeral(interaction, 'Rebooting...', 'reboot')
        logger.warning('Reboot requested by %s (ID: %s)', user, user.id)
        await self._shutdown()

    async def _reply_ephemeral(self, interaction: discord.Interaction, message: str, command: str) -> None:
        try:
            await interaction.response.send_message(message, ephemeral=True)
        except Exception as e:
            logger.warning('Failed to respond to /%s: %s', command, e)

    async def _shutdown(self) -> None:
        """Trigger the exact same clean-shutdown path SIGTERM/SIGINT use
        (see run_bot), rather than calling close() directly here, so the
        process's exit code bookkeeping stays correct."""
        if self._trigger_shutdown is not None:
            self._trigger_shutdown()
        else:
            # No run_bot wiring (e.g. a client built directly in a script
            # or test) - fall back to closing directly.
            await self.close()

    # ---- discord.Client hooks --------------------------------------------

    async def setup_hook(self) -> None:
        self.tree.copy_global_to(guild=self.guild)
        await self.tree.sync(guild=self.guild)
        # Started once here (setup_hook only ever runs once per client
        # lifetime), rather than in on_ready, which can fire again after a
        # session-invalidating gateway reconnect.
        self.mud_task = asyncio.create_task(self.mud_loop())
        self.who_task = asyncio.create_task(self.periodic_who())
        self.down_watcher_task = asyncio.create_task(self._down_watcher())

    async def on_ready(self) -> None:
        logger.info('Logged in as %s (ID: %s)', self.user, self.user.id)
        if self._announced:
            return
        channel = self.get_channel(self.mud_channel)
        if channel is None:
            # Don't latch _announced yet - the cache may just not be
            # populated on this particular ready event; a later on_ready
            # (e.g. after a gateway reconnect) will retry.
            logger.warning('Channel %s not found; will retry on a later ready event', self.mud_channel)
            return
        self._announced = True
        if not self._webhook_discovery_done:
            await self._discover_or_create_webhook(channel)
        try:
            await channel.send('Bot is online')
        except Exception as e:
            logger.warning('Failed to send startup message: %s', e)

    async def _discover_or_create_webhook(self, channel) -> None:
        self._webhook_discovery_done = True
        try:
            webhooks = await channel.webhooks()
            # A webhook we don't own, or one without a usable token (e.g. a
            # channel-follow webhook), can't be used to send() - only
            # reuse one that's actually ours.
            webhook = discord.utils.find(
                lambda wh: wh.name == 'MUD Portal' and wh.token is not None and wh.user == self.user,
                webhooks,
            )
            if webhook is None:
                webhook = await channel.create_webhook(name='MUD Portal')
            self.webhook = webhook
            logger.info('Using webhook %r for MUD gossip', webhook.name)
        except discord.Forbidden as e:
            logger.warning('Missing permission to manage webhooks; relaying gossip as the bot: %s', e)
        except discord.HTTPException as e:
            logger.warning('Failed to set up a webhook; relaying gossip as the bot: %s', e)

    async def on_message(self, message: discord.Message) -> None:
        if message.channel.id != self.mud_channel:
            return
        # Ignore our own posts, including webhook posts made on behalf of MUD players,
        # otherwise MUD gossip relayed to Discord would echo straight back into the MUD.
        if message.author == self.user or message.webhook_id is not None:
            return
        text = message.clean_content.strip()
        if not text:
            return  # attachment/sticker/embed-only message, nothing to relay

        # Peek both limits before recording either, so a message that's
        # over the global limit doesn't still burn one of the user's slots.
        if not self.global_rate_limiter.peek() or not self.user_rate_limiter.peek(message.author.id):
            logger.info('Rate limit exceeded for %s; dropping message', message.author.display_name)
            with contextlib.suppress(Exception):
                await message.add_reaction('⏳')  # hourglass
            return
        self.global_rate_limiter.allow()
        self.user_rate_limiter.allow(message.author.id)

        # Escape each field individually (never the template itself - its
        # literal '&b'/'&0' color codes are intentional), then assemble and
        # do the final length truncation on the whole result.
        escaped_name = sanitize(message.author.display_name, self.mud_max_line)
        escaped_text = sanitize(text, self.mud_max_line)
        line = self.rules.outbound.gossip.format(name=escaped_name, text=escaped_text)
        line = truncate_preserving_escapes(line, self.mud_max_line).rstrip()
        if not line:
            return
        try:
            self.outbound.put_nowait(line)
        except asyncio.QueueFull:
            logger.warning('Outbound queue full; dropping message from %s', message.author.display_name)

    async def periodic_who(self) -> None:
        while True:
            await asyncio.sleep(self.who_interval)
            if not self.connected:
                # Not connected right now - the login sequence already sends
                # a 'who' as soon as we reconnect, so don't queue a stale
                # one up that would just get sent redundantly on top of it.
                continue
            try:
                self.outbound.put_nowait('who')
            except asyncio.QueueFull:
                logger.warning('Outbound queue full; dropping periodic who request')

    async def _down_watcher(self) -> None:
        """Announce an extended MUD outage once, and its recovery once,
        without double-announcing across short-lived flapping."""
        poll_interval = 0.1
        while True:
            await asyncio.sleep(poll_interval)
            if (
                not self.connected
                and self._disconnect_monotonic is not None
                and not self._down_announced
                and time.monotonic() - self._disconnect_monotonic >= self.down_announce_seconds
            ):
                self._down_announced = True
                logger.info('MUD has been down for >= %ss; announcing', self.down_announce_seconds)
                await self._send_to_discord('MUD connection lost, retrying...')

    async def _mark_stable(self) -> None:
        """Called once a connection is judged genuinely healthy (either it
        stayed up for self.stable_seconds, or the MUD sent a real reply -
        the first who-list's prompt_end line). Only then do we clear a
        pending down-announcement and post the recovery notice; this keeps
        an accept-then-immediately-drop crash loop from posting
        lost/restored on every cycle."""
        if self._connection_stable:
            return
        self._connection_stable = True
        if self._down_announced:
            self._down_announced = False
            self.disconnected_since = None
            self._disconnect_monotonic = None
            await self._send_to_discord('MUD connection restored.')

    async def _delayed_stable(self) -> None:
        await asyncio.sleep(self.stable_seconds)
        await self._mark_stable()

    async def mud_loop(self) -> None:
        """Own the MUD connection end to end: connect, log in, read until
        EOF/error, then reconnect with exponential backoff. Nothing else
        touches self.to_mud/self.from_mud directly."""
        backoff = self.backoff_initial
        while True:
            reader = writer = None
            connected_at = None
            became_connected = False
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(self.mud_host, self.mud_port), timeout=10
                )
                self.from_mud, self.to_mud = reader, writer
                await self._login(writer)
                logger.info('Connected to MUD %s:%s', self.mud_host, self.mud_port)
                connected_at = time.monotonic()
                self.connected = True
                became_connected = True
                self.connected_since = time.time()
                self._connection_stable = False
                if self._ever_connected:
                    self.reconnect_count += 1
                self._ever_connected = True

                reader_task = asyncio.create_task(self._read_loop(reader))
                writer_task = asyncio.create_task(self._writer(writer))
                stable_task = asyncio.create_task(self._delayed_stable())
                try:
                    _, pending = await asyncio.wait(
                        {reader_task, writer_task}, return_when=asyncio.FIRST_COMPLETED
                    )
                    for task in pending:
                        task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await task
                    # Surface (and log) any exception from whichever task finished.
                    for task in (reader_task, writer_task):
                        if task.done() and not task.cancelled() and task.exception():
                            logger.info('MUD connection error: %s', task.exception())
                finally:
                    # However we got here (including this coroutine itself
                    # being cancelled), make sure both child tasks are
                    # stopped rather than leaking a reader/writer bound to a
                    # stream we're about to close.
                    for task in (reader_task, writer_task, stable_task):
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(reader_task, writer_task, stable_task, return_exceptions=True)
            except (ConnectionError, OSError, TimeoutError) as e:
                logger.info('Failed to connect to MUD: %s', e)
            finally:
                self.connected = False
                self.online = []
                self.from_mud = None
                self.to_mud = None
                if became_connected:
                    self.disconnected_since = time.time()
                    self._disconnect_monotonic = time.monotonic()
                if writer is not None:
                    writer.close()
                    with contextlib.suppress(Exception):
                        await asyncio.wait_for(writer.wait_closed(), timeout=5)
                # Only treat this as a healthy connection (and reset the
                # backoff) if it actually stayed up for a while - otherwise
                # a MUD that accepts then immediately drops would flap at
                # the minimum backoff forever.
                if connected_at is not None and (time.monotonic() - connected_at) >= self.stable_seconds:
                    backoff = self.backoff_initial

            logger.info('Reconnecting to MUD in %ss', backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, self.backoff_max)

    async def _login(self, writer: asyncio.StreamWriter) -> None:
        for line in self.rules.login.sequence:
            formatted = line.format(user=self.mud_user, password=self.mud_password, room=self.mud_room)
            writer.write(formatted.encode() + b'\n')
        await asyncio.wait_for(writer.drain(), timeout=5)

    async def _writer(self, writer: asyncio.StreamWriter) -> None:
        while True:
            line = await self.outbound.get()
            try:
                writer.write(line.encode('ascii', errors='replace') + b'\n')
                await asyncio.wait_for(writer.drain(), timeout=5)
            except (ConnectionError, OSError, TimeoutError) as e:
                logger.info('Error writing to MUD: %s', e)
                raise

    async def _read_loop(self, reader: asyncio.StreamReader) -> None:
        state = 0
        while True:
            data = await reader.readline()
            if not data:
                logger.info('MUD connection closed by remote')
                return
            line = data.decode('ascii', 'ignore')
            escaped = ANSI_ESCAPE_RE.sub('', line).strip()
            if escaped:
                self.last_mud_line_at = time.time()
                try:
                    state = await self._handle_line(escaped, state)
                except Exception as e:
                    # A malformed/unexpected line should never tear down the
                    # connection - log it and keep reading.
                    logger.warning('Error handling MUD line %r: %s', escaped, e)

    async def _handle_line(self, escaped: str, state: int) -> int:
        # A new who-list can start at any time; restart it rather than
        # ignoring it or getting confused mid-parse.
        if escaped.startswith(self.rules.who.start_prefix):
            self.online = []
            return 1

        if state == 1:
            if escaped == self.rules.login.prompt_end:
                if self._who_future is not None and not self._who_future.done():
                    self._who_future.set_result(list(self.online))
                # A real reply from the MUD (the login's own who-list
                # finishing) is as good a sign of health as staying up for
                # a while - don't make a fast, working MUD wait out the
                # full stability window before a recovery is announced.
                await self._mark_stable()
                return 0
            if m := self.rules.who.entry.match(escaped):
                username = m.group('name')
                if username not in self.online and username.lower() != self.mud_user.lower():
                    self.online.append(username)
            return 1

        for rule in self.rules.events:
            m = rule.pattern.match(escaped)
            if not m:
                continue
            groups = m.groupdict()
            name = groups.get('name')
            if rule.action == 'login' and name:
                if name not in self.online:
                    self.online.append(name)
            elif rule.action == 'logout' and name:
                if name in self.online:
                    self.online.remove(name)

            if rule.kind == 'gossip':
                await self._send_gossip(escaped, name, groups.get('text'))
            else:
                text = rule.message.format(**groups) if rule.message else escaped
                await self._send_to_discord(text)
            break
        return 0

    async def _send_gossip(self, escaped_line: str, name: str | None, text: str | None) -> None:
        if self.webhook is not None and name and text is not None and is_valid_webhook_username(name):
            stripped = strip_surrounding_quotes(text)
            try:
                await self.webhook.send(
                    content=discord.utils.escape_markdown(stripped),
                    username=name,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                return
            except (discord.HTTPException, ValueError, aiohttp.ClientError, asyncio.TimeoutError) as e:
                # ValueError: e.g. a webhook without a token (partial/foreign
                # webhook that slipped through discovery). Fall back exactly
                # once, via the bot, for this one line.
                logger.warning('Webhook send failed, falling back to bot message: %s', e)
        logger.info('From MUD: %s', escaped_line)
        await self._send_to_discord(escaped_line)

    async def _send_to_discord(self, text: str) -> None:
        channel = self.get_channel(self.mud_channel)
        if channel is None:
            logger.warning('Channel %s not found; dropping message: %s', self.mud_channel, text)
            return
        try:
            await channel.send(discord.utils.escape_markdown(text))
        except Exception as e:
            logger.warning('Failed to send message to Discord: %s', e)

    async def close(self) -> None:
        # Capture the writer once - mud_loop's finally block can null out
        # self.to_mud concurrently, and we must not let that turn into an
        # AttributeError that skips super().close().
        writer = self.to_mud
        if writer is not None:
            try:
                writer.write(self.rules.login.quit_command.encode() + b'\n')
                await asyncio.wait_for(writer.drain(), timeout=5)
                writer.close()
                await asyncio.wait_for(writer.wait_closed(), timeout=5)
            except Exception as e:
                logger.warning('Error while closing MUD connection: %s', e)
        for task in (self.mud_task, self.who_task, self.down_watcher_task):
            if task is not None:
                task.cancel()
        await super().close()


def build_client(config: Config) -> MudClient:
    intents = discord.Intents.default()
    intents.message_content = True
    return MudClient(
        config,
        activity=discord.Game(name=f'{config.mud_host}:{config.mud_port}'),
        intents=intents,
    )


async def run_bot(client: MudClient, token: str) -> None:
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _request_stop(sig_name: str) -> None:
        logger.info('Received %s, shutting down...', sig_name)
        stop_event.set()

    for sig, name in ((signal.SIGINT, 'SIGINT'), (signal.SIGTERM, 'SIGTERM')):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, _request_stop, name)

    # Let /reboot request the exact same shutdown as SIGTERM/SIGINT.
    client._trigger_shutdown = lambda: _request_stop('reboot command')

    start_task = asyncio.create_task(client.start(token))
    stop_task = asyncio.create_task(stop_event.wait())
    try:
        await asyncio.wait({start_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        if not client.is_closed():
            await client.close()
        stop_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await stop_task

    # Surface any error client.start() ended with (bad token, tree.sync
    # Forbidden, etc.) instead of silently exiting 0.
    try:
        await start_task
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.exception('Bot exited with an error')
        sys.exit(1)


def main():
    discord.utils.setup_logging(level=logging.INFO)
    logger.info('Starting bot')
    config = Config.from_env()
    client = build_client(config)
    asyncio.run(run_bot(client, config.discord_token))


if __name__ == '__main__':
    main()
