import asyncio
import discord
from discord import app_commands
import re

from config import Config


class MudClient(discord.Client):
    def __init__(self, config: Config, activity: discord.BaseActivity, intents: discord.Intents):
        super().__init__(intents=intents, activity=activity)
        self.tree = app_commands.CommandTree(self)
        self.guild = discord.Object(id=config.discord_guild)

        self.mud_host = config.mud_host
        self.mud_port = config.mud_port
        self.mud_user = config.mud_user
        self.mud_password = config.mud_password
        self.mud_channel = config.discord_channel
        self.to_mud = None
        self.from_mud = None
        self.mud_input = ""
        self.online = []
        self.reconnecting = False

        self.ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')

    async def setup_hook(self):
        self.tree.copy_global_to(guild=self.guild)
        await self.tree.sync(guild=self.guild)


def main():
    print("Starting bot")
    config = Config()
    intents = discord.Intents.default()
    intents.message_content = True
    client = MudClient(config,
                       activity=discord.Game(name=f'{config.mud_host}:{config.mud_port}'),
                       intents=intents)

    @client.event
    async def on_ready():
        print(f'Logged in as {client.user} (ID: {client.user.id})')
        channel = client.get_channel(client.mud_channel)
        if channel:
            await channel.send('Bot is online')
        else:
            print(f'Warning: Channel {client.mud_channel} not found')
        print('------')
        await mud_connect()
        asyncio.create_task(mud_manager())
        asyncio.create_task(periodic_who())

    @client.event
    async def on_message(message):
        if message.author == client.user or message.channel.id != client.mud_channel:
            return
        if client.to_mud is None:
            return
        try:
            client.to_mud.write(f'gossip {message.author.name}: {message.content}\n'.encode())
            await client.to_mud.drain()
        except ConnectionError as e:
            print(f'Connection error sending message: {e}')
            await mud_connect()

    async def mud_connect():
        if client.reconnecting:
            return
        client.reconnecting = True
        try:
            if client.to_mud:
                client.to_mud.close()
            client.from_mud, client.to_mud = await asyncio.open_connection(config.mud_host, config.mud_port)
            client.to_mud.write(str.encode(config.mud_user) + b'\n')
            client.to_mud.write(str.encode(config.mud_password) + b'\n')
            client.to_mud.write(b'\n1\n1\ngoto 1200\nprompt --END--%_\nwho\n')
            await client.to_mud.drain()
            print('Connected to MUD')
        except Exception as e:
            print(f'Failed to connect to MUD: {e}')
            client.from_mud = None
            client.to_mud = None
        finally:
            client.reconnecting = False

    async def mud_manager():
        state = 0
        while True:
            try:
                if client.from_mud is None:
                    await asyncio.sleep(1)
                    continue
                channel = client.get_channel(client.mud_channel)
                if channel is None:
                    print(f'Channel {client.mud_channel} not found')
                    await asyncio.sleep(5)
                    continue
                data = await client.from_mud.readline()
                if not data:
                    print('MUD connection closed, reconnecting...')
                    await mud_connect()
                    continue
                line = data.decode('ascii', 'ignore')
                escaped = client.ansi_escape.sub('', line).strip()
                if escaped:
                    if state == 0:
                        if re.match(r'^\w+ gossips, ', escaped):
                            print(f'From MUD Line: {escaped}')
                            await channel.send(escaped)
                        elif escaped.startswith('The world seems to pause momentarily as'):
                            username = escaped.split()[7]
                            if username in client.online:
                                client.online.remove(username)
                            await channel.send(f'{username} has logged out.')
                        elif escaped.startswith('The ground shakes slightly with the arrival of'):
                            username = escaped.split()[8].rstrip('.')
                            if username not in client.online:
                                client.online.append(username)
                            await channel.send(f'{username} has logged in.')
                        elif re.match(r'^\w+ advanced to level \d+!', escaped):
                            await channel.send(escaped)
                        elif re.match(r'^\w+ lost level', escaped):
                            await channel.send(escaped)
                        elif match := re.match(r'^\[ (\w+) (was killed by [^[]+)', escaped):
                            username = match.group(1)
                            if username in client.online:
                                client.online.remove(username)
                            await channel.send(f'{username} {match.group(2)}!')
                        elif escaped.startswith('*** Active'):
                            state = 1
                            client.online = []
                    elif state == 1:
                        if escaped == '--END--':
                            state = 0
                        elif matches := re.match(r'^\[[^]]+\] (\w+)', escaped):
                            username = matches.group(1)
                            if username not in client.online and username.lower() != client.mud_user.lower():
                                client.online.append(matches.group(1))

            except ConnectionError as e:
                print(f'Connection error: {e}, reconnecting...')
                await mud_connect()
            except Exception as e:
                print(f'Error in mud_manager: {e}')
                await asyncio.sleep(1)

    async def periodic_who():
        while True:
            await asyncio.sleep(600)  # 10 minutes
            if client.to_mud:
                try:
                    client.to_mud.write(b'who\n')
                    await client.to_mud.drain()
                except ConnectionError:
                    pass  # mud_manager will handle reconnection

    @client.tree.command()
    async def who(interaction: discord.Interaction):
        """Lists currently online players."""
        if client.online:
            await interaction.response.send_message('\n'.join(client.online))
        else:
            await interaction.response.send_message('No one is online')

    client.run(config.discord_token)


if __name__ == '__main__':
    main()
