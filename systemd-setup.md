# Running 801DoorBot as a System Service

This guide explains how to set up the 801DoorBot as a systemd service on Linux systems for reliable background operation.

## Prerequisites

- A Linux system with systemd (most modern distributions)
- Python 3.8 or newer
- Root or sudo access on the server

## Setup Steps

### 1. Create a dedicated user (optional but recommended)

Creating a dedicated user improves security by isolating the bot's permissions:

```bash
sudo useradd -r -s /bin/false doorbot
```

### 2. Copy your project to a suitable location

```bash
sudo mkdir -p /opt/801DoorBot
sudo cp -r * /opt/801DoorBot/
sudo chown -R doorbot:doorbot /opt/801DoorBot
```

### 3. Set up a virtual environment

```bash
cd /opt/801DoorBot
sudo -u doorbot python -m venv venv
sudo -u doorbot venv/bin/pip install -r requirements.txt
```

### 4. Create and configure your .env file

```bash
sudo -u doorbot cp .env.example .env
sudo nano /opt/801DoorBot/.env  # Edit with your configuration
```

For headless server deployment, you may want to enable silent mode and audit logging:

```
SILENT_MODE=true
AUDIT_LOGGING=true
```

### 5. Install the systemd service

```bash
sudo cp 801DoorBot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable 801DoorBot.service
sudo systemctl start 801DoorBot.service
```

### 6. Check service status

```bash
sudo systemctl status 801DoorBot.service
```

### 7. View logs

```bash
sudo journalctl -u 801DoorBot.service -f
```

## Troubleshooting

### Service fails to start

Check the logs for detailed error messages:

```bash
sudo journalctl -u 801DoorBot.service -e
```

Common issues include:
- Missing dependencies
- Invalid .env configuration
- Permission problems

### Bot keeps restarting

If the bot keeps crashing and restarting:
1. Enable DEBUG mode in .env
2. Check the logs for error messages
3. Fix any issues in the configuration

## Service Management Commands

- **Start the service**: `sudo systemctl start 801DoorBot.service`
- **Stop the service**: `sudo systemctl stop 801DoorBot.service`
- **Restart the service**: `sudo systemctl restart 801DoorBot.service`
- **Check status**: `sudo systemctl status 801DoorBot.service`
- **Enable at boot**: `sudo systemctl enable 801DoorBot.service`
- **Disable at boot**: `sudo systemctl disable 801DoorBot.service` 