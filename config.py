import os
import dotenv
from dataclasses import dataclass

dotenv.load_dotenv()


@dataclass
class Config:
    mud_host: str = os.getenv('MUD_HOST') or 'localhost'
    mud_port = int(os.getenv('MUD_PORT') or 9999)
    mud_user = os.getenv('MUD_USER') or 'user'
    mud_password = os.getenv('MUD_PASS') or 'password'
    discord_token = os.getenv('DISCORD_TOKEN') or 'token'
    discord_guild = int(os.getenv('DISCORD_GUILD') or 0)
    discord_channel = int(os.getenv('DISCORD_CHANNEL') or 0)
