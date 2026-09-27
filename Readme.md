# Discord <=> Mud portal bot
This is a bot that will connect to your mud and provide a portal between your game and a discord channel.  Allows people
to stay in touch when they're not logged in.

Will require some minor changes to make it work with other muds.

## Install Steps

1. Create a discord bot and invite to your server. [Instructions](https://discordpy.readthedocs.io/en/stable/discord.html)
1. Update .env with discord token (from prior step), guild (server) id, and channel id (in discord, right click on the server/channel and copy id)
1. Create a user on your mud. Update the .env file with the mud details.
1. Update the code to match strings within your own mud.  Primarily the connect string and capture.
(Todo: Make these configurable.)

## Configuration

Copy `env.default` to `.env` and fill in your values.

Required:
- `DISCORD_TOKEN` - your bot's token
- `DISCORD_GUILD` - the id of the Discord server the bot lives in
- `DISCORD_CHANNEL` - the id of the channel to relay to/from
- `MUD_HOST` / `MUD_PORT` - where the MUD listens
- `MUD_USER` / `MUD_PASS` - the bot's MUD login

The bot will refuse to start (with a message listing what's missing) if any
of the above are not set.

Optional:
- `WHO_INTERVAL` - seconds between automatic `who` refreshes (default `600`)
- `MUD_ROOM` - room number sent in the login `goto` command (default `1200`)
- `MUD_MAX_LINE` - max length, in characters, of any single line relayed to
  the MUD, including the `gossip name: ` prefix (default `250`)
- `MUD_RULES` - path to a TOML rules file describing the MUD's login
  sequence and line-matching rules (default: `mud.toml` next to `main.py`;
  see "MUD rules file" below)
- `DISCORD_WEBHOOK_URL` - a webhook URL to relay MUD gossip under the
  speaking player's name. If unset, the bot tries to find or create a
  webhook named "MUD Portal" in the target channel itself; if it can't
  (missing permission, etc.) gossip is relayed as plain bot messages instead
- `RATE_USER_MSGS` / `RATE_USER_SECONDS` - per-user rate limit on messages
  relayed to the MUD: at most `RATE_USER_MSGS` per `RATE_USER_SECONDS`
  seconds (defaults `5` / `10`)
- `RATE_GLOBAL_MSGS` / `RATE_GLOBAL_SECONDS` - rate limit across all users
  combined (default `15` per window; `RATE_GLOBAL_SECONDS` defaults to
  whatever `RATE_USER_SECONDS` is set to)
- `MUD_DOWN_ANNOUNCE_SECONDS` - how long the MUD must be continuously
  unreachable (including at process start) before the bot posts a
  "connection lost" notice (default `60`)
- `MUD_STABLE_SECONDS` - after a reconnect, how long the connection must
  stay up (or, if sooner, until the MUD replies with a real who-list)
  before a pending "connection lost" is cleared and "connection restored"
  is posted. Also the minimum uptime for the reconnect backoff to reset.
  This keeps an accept-then-immediately-drop crash loop from posting
  lost/restored on every cycle (default `30`)

## MUD rules file

`mud.toml` holds everything specific to one MUD's protocol, so adapting the
bot to a different MUD shouldn't require editing `main.py`:

- `[login]`: `sequence` (list of lines sent, in order, right after
  connecting - `{user}`, `{password}`, `{room}` are substituted),
  `prompt_end` (the line that marks the end of a `who` listing) and
  `quit_command` (sent on shutdown).
- `[who]`: `start_prefix` (the line that starts a `who` listing) and `entry`
  (a regex with a `name` named group matching one player per line).
- `[[events]]`: one entry per line pattern to react to, checked in file
  order (first match wins), each with:
  - `pattern` - a regex, optionally with named groups `name` and/or `text`.
  - `message` - a `str.format()` template using those named groups; if
    omitted, the whole (stripped) line is relayed as-is.
  - `action` - `"login"`/`"logout"` add/remove the `name` group from the
    online list; `"none"` (the default) leaves it untouched.
  - `kind` - `"gossip"` lines are eligible for the webhook (so they show up
    under the speaking player's name); anything else (`"event"`, the
    default) always goes through the bot.
- `[outbound]`: `gossip` - a `str.format()` template used to relay a
  Discord message to the MUD, with `{name}` (the sender's display name) and
  `{text}` (the message body) as the only allowed fields - both are already
  sanitized and MUD-color-code-escaped before being substituted in, so
  don't escape them again in the template itself. The default,
  `"gossip &b{name}&0&3: {text}"`, bolds the speaker's name (`&b`), clears
  bold (`&0`), then restores the gossip channel's own color (`&3`, yellow -
  the MUD colors the whole gossip line yellow server-side, and `&0` alone
  would leave the rest of the line in the default color instead). Color
  codes (see `src/screen.cpp`): `0` normal, `1`-`7`
  red/green/yellow/blue/magenta/cyan/white, `b` bold, `u` underline, `d`
  dim, `_` newline, `&&`/`@@` literal `&`/`@`. Drop `&b`/`&0`/`&3` from the
  template if you don't want any of that formatting.

A bad regex, an unknown `{field}` in a template, or a missing required key
in `mud.toml` makes the bot refuse to start, with a message pointing at the
offending entry.

### MUD color-code escaping

FieryMUD (and similar MUDs) treat `&` and `@` as color-code trigger
characters - for example `&_` is its own newline code. Any `&` or `@` in
text relayed *to* the MUD (a Discord display name or message body) is
doubled (`&` -> `&&`, `@` -> `@@`) so it can never be misread as a color
code or, worse, used to forge a second command/line (e.g. a message
containing literal `&_` text). This also means an `@mention` rendered by
Discord as `@Bob` arrives at the MUD as literal text (`@@Bob`) rather than
being eaten as a color code.

This matters more than it might seem: the MUD strips color codes out of
gossip lines spoken by ordinary (non-immortal) players, but *not* from
immortals, and the bot's MUD character is an immortal. Without the
escaping above, a player's raw `&` or `@` really would be interpreted as a
color code once relayed through the bot's line.

Discord also silently converts things like `:)` into an emoji before the
bot ever sees the message; `textnorm.to_ascii_friendly()` (used by
`sanitize()`) converts common emoji, smart punctuation, and accented
letters back to a plain-ASCII approximation (e.g. back to `:)`, or to
`:party_popper:` for an emoji with no direct ASCII equivalent) instead of
letting them all collapse to `?`.

## Slash commands

- `/who` - lists players currently online. If the MUD is connected, this
  asks it for a fresh list (falling back to the last known list, marked
  "(cached)", if the MUD doesn't answer within 10 seconds).
- `/status` - connection state, last MUD activity, reconnect count, queue
  size, and webhook status (ephemeral - only visible to the caller).
- `/reboot` - **Administrator only** (hidden from other members in the
  Discord UI, and also enforced at runtime). Cleanly shuts the bot down
  (sends `quit` to the MUD, closes the Discord client) and exits the
  process with code 0. This does nothing useful unless something restarts
  the process afterwards - see "Running as a service" below.

## Running as a service

`discord_mud_portal.service` is a sample systemd unit. `/reboot` relies on
the process manager to bring the bot back up after it exits, so the unit
needs `Restart=always` (or `Restart=on-success`) - copy it to
`/etc/systemd/system/`, fill in `User=`, adjust `WorkingDirectory`/
`ExecStart` to match your install path, then:

```
sudo systemctl daemon-reload
sudo systemctl enable --now discord_mud_portal
```

Still a work in progress.  Just currently thrown together.  Please send pull requests!
