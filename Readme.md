# Discord <=> Mud portal bot
This is a bot that will connect to your mud and provide a portal between your game and a discord channel.  Allows people
to stay in touch when they're not logged in.

Will require some minor changes to make it work with other muds.

## Install Steps

1. Create a discord bot and invite to your server. [Instructions](https://discordpy.readthedocs.io/en/stable/discord.html)
1. Update .env with discord token (from prior step) and channel token (in discord, right click on the channel and copy id)
1. Create a user on your mud. Update the .env file with the mud details.
1. Update the code to match strings within your own mud.  Primarily the connect string and capture.
(Todo: Make these configurable.)

Still a work in progress.  Just currently thrown together.  Please send pull requests!
