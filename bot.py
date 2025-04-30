import os
import nextcord
from nextcord.ext import commands
import requests
from dotenv import load_dotenv
import json
import logging
from urllib.parse import urlparse
import urllib3
import warnings

# Suppress SSL warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Load environment variables
load_dotenv()

# Debug and silent mode configuration
DEBUG = os.getenv('DEBUG', 'false').lower() in ('true', '1', 't', 'yes')
SILENT_MODE = os.getenv('SILENT_MODE', 'false').lower() in ('true', '1', 't', 'yes')

# ntfy notification configuration
NTFY_URL = os.getenv('NTFY_URL', '')  # Empty string means no notifications
NTFY_TOPIC = os.getenv('NTFY_TOPIC', 'door-bot-alerts')

# Suppress nextcord warnings in silent mode
if SILENT_MODE:
    # Filter out all warnings from nextcord
    warnings.filterwarnings("ignore", module="nextcord")
    # Also suppress any other warnings
    if not DEBUG:
        warnings.filterwarnings("ignore")

# Configure logging
if SILENT_MODE:
    logging_level = logging.CRITICAL + 1  # Above critical to disable all logging
elif DEBUG:
    logging_level = logging.DEBUG
else:
    logging_level = logging.INFO

logging.basicConfig(
    level=logging_level,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Output startup mode information
if not SILENT_MODE:
    if DEBUG:
        logger.info("Starting in DEBUG mode (verbose logging enabled)")
    else:
        logger.info("Starting in normal mode")

# Function to send notifications via ntfy.sh
def send_notification(title, message, priority="default", tags=None):
    """
    Send a notification via ntfy.sh
    
    Args:
        title (str): Title of the notification
        message (str): Body of the notification
        priority (str): Priority level (default, low, high, urgent)
        tags (list): List of tags to apply (e.g. ["warning", "door"])
    """
    if not NTFY_URL:
        return  # Skip if ntfy is not configured
    
    try:
        headers = {
            "Title": title
        }
        
        if priority and priority != "default":
            headers["Priority"] = priority
            
        if tags:
            headers["Tags"] = ",".join(tags)
            
        # Construct the full URL
        url = f"{NTFY_URL.rstrip('/')}/{NTFY_TOPIC}"
        
        if DEBUG:
            logger.debug(f"Sending ntfy notification to {url}")
            logger.debug(f"Headers: {headers}")
            logger.debug(f"Message: {message}")
        
        response = requests.post(url, data=message, headers=headers)
        response.raise_for_status()
        if DEBUG:
            logger.debug(f"Notification sent successfully: {response.status_code}")
    except Exception as e:
        # Don't let notification failures affect the main application
        if not SILENT_MODE:
            logger.error(f"Failed to send notification: {str(e)}")
            if DEBUG:
                logger.debug(f"Error details: {repr(e)}")

# Unifi API configuration
UNIFI_TOKEN = os.getenv('UNIFI_TOKEN')
UNIFI_HOST = os.getenv('UNIFI_HOST')
# Parse the host URL to ensure it's in the correct format
parsed_url = urlparse(UNIFI_HOST)
if not parsed_url.netloc:
    # If no protocol was provided, use the host as is
    UNIFI_BASE_URL = f"https://{UNIFI_HOST}/api/v1"
else:
    # If protocol was provided, use the full URL
    UNIFI_BASE_URL = f"{UNIFI_HOST}/api/v1"

# Discord bot setup
intents = nextcord.Intents.default()
bot = commands.Bot(command_prefix='!', intents=intents)

class UnifiAPI:
    def __init__(self):
        if not UNIFI_TOKEN or not UNIFI_HOST:
            raise ValueError("Unifi API credentials not properly configured. Check your .env file.")
            
        self.session = requests.Session()
        self.session.headers.update({
            'Authorization': f'Bearer {UNIFI_TOKEN}',
            'Content-Type': 'application/json'
        })
        # Disable SSL verification for self-signed certificates
        self.session.verify = False

    def set_evacuation_mode(self, enabled: bool):
        """Set evacuation mode (unlock/lock all doors)"""
        url = f"{UNIFI_BASE_URL}/developer/doors/settings/emergency"
        payload = {
            "lockdown": False,  # Always keep lockdown disabled
            "evacuation": enabled
        }
        if DEBUG:
            logger.debug(f"Making PUT request to {url}")
            logger.debug(f"Headers: {self.session.headers}")
            logger.debug(f"Payload: {payload}")
        response = self.session.put(url, json=payload)
        try:
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            error_msg = f"HTTP Error: {e}"
            logger.error(error_msg)
            logger.error(f"Response status code: {response.status_code}")
            logger.error(f"Response body: {response.text}")
            if DEBUG:
                logger.error(f"Response headers: {response.headers}")
            # Send notification for API errors
            send_notification(
                "Door Control API Error", 
                f"Failed to set evacuation mode (enabled={enabled}): {str(e)}\nStatus code: {response.status_code}",
                priority="high",
                tags=["warning", "api-error"]
            )
            raise

    def get_door_status(self):
        """Get current status of all doors"""
        url = f"{UNIFI_BASE_URL}/developer/doors"
        if DEBUG:
            logger.debug(f"Making GET request to {url}")
            logger.debug(f"Headers: {self.session.headers}")
        response = self.session.get(url)
        try:
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            error_msg = f"HTTP Error: {e}"
            logger.error(error_msg)
            logger.error(f"Response status code: {response.status_code}")
            logger.error(f"Response body: {response.text}")
            if DEBUG:
                logger.error(f"Response headers: {response.headers}")
            # Send notification for API errors
            send_notification(
                "Door Status API Error", 
                f"Failed to get door status: {str(e)}\nStatus code: {response.status_code}",
                priority="high",
                tags=["warning", "api-error"]
            )
            raise

# Initialize Unifi API client
try:
    unifi = UnifiAPI()
    logger.info("Unifi API client initialized successfully")
except Exception as e:
    error_msg = f"Failed to initialize Unifi API client: {str(e)}"
    logger.error(error_msg)
    if DEBUG:
        logger.debug(f"Exception details: {repr(e)}")
    # Send notification for initialization error
    send_notification(
        "Door Bot Startup Error", 
        f"Failed to initialize Unifi API client: {str(e)}",
        priority="urgent",
        tags=["error", "startup"]
    )
    unifi = None

@bot.event
async def on_ready():
    logger.info(f'Logged in as {bot.user.name}')
    try:
        await bot.sync_application_commands()
        logger.info("Synced slash commands")
    except Exception as e:
        error_msg = f"Failed to sync commands: {e}"
        logger.error(error_msg)
        # Send notification for Discord API errors
        send_notification(
            "Discord Bot Error", 
            f"Failed to sync slash commands: {str(e)}",
            priority="high",
            tags=["warning", "discord-error"]
        )

@bot.slash_command(name="unlock", description="Unlock all doors by enabling evacuation mode")
async def unlock(interaction: nextcord.Interaction):
    try:
        if not unifi:
            error_msg = "Unifi API is not properly configured. Please check the bot's console for details."
            raise RuntimeError(error_msg)
            
        await interaction.response.defer()
        if DEBUG:
            logger.debug("Processing unlock command")
        result = unifi.set_evacuation_mode(True)
        logger.info("Successfully unlocked all doors")
        await interaction.followup.send("✅ All doors have been unlocked")
    except Exception as e:
        error_msg = f"Error in unlock command: {str(e)}"
        logger.error(error_msg)
        if DEBUG:
            logger.debug(f"Exception details: {repr(e)}")
        # Send notification for command errors
        send_notification(
            "Door Unlock Failed", 
            f"Error: {str(e)}\nCommand triggered by: {interaction.user.display_name}",
            priority="high",
            tags=["warning", "unlock-error"]
        )
        await interaction.followup.send("❌ Failed to unlock doors. Please check the bot's console for details.")

@bot.slash_command(name="lock", description="Lock all doors by disabling evacuation mode")
async def lock(interaction: nextcord.Interaction):
    try:
        if not unifi:
            error_msg = "Unifi API is not properly configured. Please check the bot's console for details."
            raise RuntimeError(error_msg)
            
        await interaction.response.defer()
        if DEBUG:
            logger.debug("Processing lock command")
        result = unifi.set_evacuation_mode(False)
        logger.info("Successfully locked all doors")
        await interaction.followup.send("✅ All doors have been locked")
    except Exception as e:
        error_msg = f"Error in lock command: {str(e)}"
        logger.error(error_msg)
        if DEBUG:
            logger.debug(f"Exception details: {repr(e)}")
        # Send notification for command errors
        send_notification(
            "Door Lock Failed", 
            f"Error: {str(e)}\nCommand triggered by: {interaction.user.display_name}",
            priority="high",
            tags=["warning", "lock-error"]
        )
        await interaction.followup.send("❌ Failed to lock doors. Please check the bot's console for details.")

@bot.slash_command(name="status", description="Check the current status of all doors")
async def status(interaction: nextcord.Interaction):
    try:
        if not unifi:
            error_msg = "Unifi API is not properly configured. Please check the bot's console for details."
            raise RuntimeError(error_msg)
            
        await interaction.response.defer()
        if DEBUG:
            logger.debug("Processing status command")
        response = unifi.get_door_status()
        
        # Create a formatted message with door statuses
        status_message = "**Door Status:**\n"
        
        # Check if response is a dictionary with 'data' field
        if isinstance(response, dict) and 'data' in response:
            doors = response['data']
            if DEBUG:
                logger.debug(f"Door data received: {doors}")
            for door in doors:
                door_status = door.get('door_lock_relay_status', 'lock')
                status = "🔓 Unlocked" if door_status == 'unlock' else "🔒 Locked"
                status_message += f"- {door.get('name', 'Unknown')}: {status}\n"
        else:
            if DEBUG:
                logger.debug(f"Unexpected response format: {response}")
            status_message += str(response)
        
        logger.info("Successfully retrieved door status")
        await interaction.followup.send(status_message)
    except Exception as e:
        error_msg = f"Error in status command: {str(e)}"
        logger.error(error_msg)
        if DEBUG:
            logger.debug(f"Exception details: {repr(e)}")
        # Send notification for command errors
        send_notification(
            "Door Status Check Failed", 
            f"Error: {str(e)}\nCommand triggered by: {interaction.user.display_name}",
            priority="high",
            tags=["warning", "status-error"]
        )
        await interaction.followup.send("❌ Failed to get door status. Please check the bot's console for details.")

# Run the bot
bot.run(os.getenv('DISCORD_TOKEN')) 