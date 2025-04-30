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

2. Create a `.env` file based on the provided `.env.example`:


3. Run the bot:
```bash
python bot.py
```

## Configuration Options in `.env`

### Required Configuration
- `DISCORD_TOKEN`: Your Discord bot token
- `UNIFI_HOST`: URL of your Unifi Access controller (including port)
- `UNIFI_TOKEN`: API token for Unifi Access

### Debugging and Logging
- `DEBUG`: Set to `true` to enable verbose logging
- `SILENT_MODE`: Set to `true` to disable all console output (useful for headless deployment)

### External Error Push Notifications
- `NTFY_URL`: URL of ntfy.sh server (leave empty to disable notifications)
- `NTFY_TOPIC`: Topic name for ntfy.sh notifications

### Audit Logging
- `AUDIT_LOGGING`: Set to `true` to enable command usage logging
- `AUDIT_LOG_DIR`: Directory where audit logs will be stored (default: `logs`)

## Using ntfy for Error Notifications

The bot supports sending push notifications for errors using [ntfy.sh](https://ntfy.sh) - a simple HTTP-based pub-sub notification service.

You can use ntfy's public server at ntfy.sh or [self-host your own ntfy server](https://docs.ntfy.sh/install/) for additional privacy.

### How it Works

When enabled, the bot will send push notifications in these scenarios:
- API errors when communicating with the Unifi Access controller
- Command failures (unlock, lock, status)
- Bot initialization errors

### Setup Instructions

1. Configure the bot by setting these values in your `.env` file:
   ```
   NTFY_URL=https://ntfy.sh
   NTFY_TOPIC=your-unique-topic-name
   ```

2. Download the ntfy app:
   - [Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy)
   - [iOS](https://apps.apple.com/us/app/ntfy/id1625396347)
   - or use the [web app](https://ntfy.sh/app)

3. Subscribe to your topic in the app (use something unique and hard to guess)

4. When errors occur, you'll receive push notifications with:
   - Descriptive titles
   - Error details
   - Priority levels based on error severity
   - Relevant tags 