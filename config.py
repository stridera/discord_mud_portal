import os
import dotenv
from dataclasses import dataclass

dotenv.load_dotenv()


@dataclass
class Config:
    mud_host: str = os.getenv('MUD_HOST') or 'localhost'
    mud_port: int = int(os.getenv('MUD_PORT') or 9999)
    mud_user: str = os.getenv('MUD_USER') or 'user'
    mud_password: str = os.getenv('MUD_PASS') or 'password'
    discord_token: str = os.getenv('DISCORD_TOKEN') or 'token'
    discord_guild: int = int(os.getenv('DISCORD_GUILD') or 0)
    discord_channel: int = int(os.getenv('DISCORD_CHANNEL') or 0)
