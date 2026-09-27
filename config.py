import os
from dataclasses import dataclass
from pathlib import Path

import dotenv

dotenv.load_dotenv()

# Environment variable name -> Config field name, for the values that are
# required at startup (no sane default exists for any of these).
_REQUIRED = {
    'DISCORD_TOKEN': 'discord_token',
    'DISCORD_GUILD': 'discord_guild',
    'DISCORD_CHANNEL': 'discord_channel',
    'MUD_HOST': 'mud_host',
    'MUD_PORT': 'mud_port',
    'MUD_USER': 'mud_user',
    'MUD_PASS': 'mud_password',
}

# MUD_RULES defaults to mud.toml next to this file (i.e. next to main.py).
_DEFAULT_RULES_PATH = str(Path(__file__).resolve().parent / 'mud.toml')


@dataclass
class Config:
    mud_host: str
    mud_port: int
    mud_user: str
    mud_password: str
    discord_token: str
    discord_guild: int
    discord_channel: int
    who_interval: int = 600
    mud_room: str = '1200'
    mud_max_line: int = 250
    mud_rules: str = _DEFAULT_RULES_PATH
    discord_webhook_url: str | None = None
    rate_user_msgs: int = 5
    rate_user_seconds: float = 10
    rate_global_msgs: int = 15
    rate_global_seconds: float = 10
    mud_down_announce_seconds: float = 60
    mud_stable_seconds: float = 30

    @classmethod
    def from_env(cls) -> 'Config':
        """Build a Config from the environment, raising SystemExit listing
        any required variables that are missing rather than silently
        falling back to a placeholder value."""
        raw = {env_name: os.getenv(env_name) for env_name in _REQUIRED}
        missing = [env_name for env_name, value in raw.items() if not value]
        if missing:
            raise SystemExit(
                'Missing required environment variable(s): ' + ', '.join(sorted(missing))
            )

        rate_user_seconds = float(os.getenv('RATE_USER_SECONDS') or 10)

        return cls(
            mud_host=raw['MUD_HOST'],
            mud_port=int(raw['MUD_PORT']),
            mud_user=raw['MUD_USER'],
            mud_password=raw['MUD_PASS'],
            discord_token=raw['DISCORD_TOKEN'],
            discord_guild=int(raw['DISCORD_GUILD']),
            discord_channel=int(raw['DISCORD_CHANNEL']),
            who_interval=int(os.getenv('WHO_INTERVAL') or 600),
            mud_room=os.getenv('MUD_ROOM') or '1200',
            mud_max_line=int(os.getenv('MUD_MAX_LINE') or 250),
            mud_rules=os.getenv('MUD_RULES') or _DEFAULT_RULES_PATH,
            discord_webhook_url=os.getenv('DISCORD_WEBHOOK_URL') or None,
            rate_user_msgs=int(os.getenv('RATE_USER_MSGS') or 5),
            rate_user_seconds=rate_user_seconds,
            rate_global_msgs=int(os.getenv('RATE_GLOBAL_MSGS') or 15),
            # Defaults to whatever RATE_USER_SECONDS resolved to, unless set explicitly.
            rate_global_seconds=float(os.getenv('RATE_GLOBAL_SECONDS') or rate_user_seconds),
            mud_down_announce_seconds=float(os.getenv('MUD_DOWN_ANNOUNCE_SECONDS') or 60),
            mud_stable_seconds=float(os.getenv('MUD_STABLE_SECONDS') or 30),
        )
