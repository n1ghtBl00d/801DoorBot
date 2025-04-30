# 801DoorBot

A Discord bot that interacts with Unifi Access API to control door locks.

## Features

- `/unlock` - Unlocks all doors by enabling evacuation mode
- `/lock` - Locks all doors by disabling evacuation mode
- `/status` - Checks the current status of the doors

## Setup

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Create a `.env` file with the following variables:
```
DISCORD_TOKEN=your_discord_bot_token
UNIFI_TOKEN=your_unifi_api_token
UNIFI_HOST=your_unifi_controller_host
```

3. Run the bot:
```bash
python bot.py
```

## Security Notes

- Keep your `.env` file secure and never commit it to version control
- The bot requires appropriate permissions in your Discord server
- Ensure your Unifi API token has the necessary permissions for door control
- The Unifi API token should have access to the Access API endpoints 