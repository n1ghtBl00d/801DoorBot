import os
import nextcord
from nextcord.ext import commands
import requests
from dotenv import load_dotenv
import json
import logging
from urllib.parse import urlparse
import urllib3

# Suppress SSL warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

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
        logger.debug(f"Making PUT request to {url}")
        logger.debug(f"Headers: {self.session.headers}")
        logger.debug(f"Payload: {payload}")
        response = self.session.put(url, json=payload)
        try:
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            logger.error(f"HTTP Error: {e}")
            logger.error(f"Response status code: {response.status_code}")
            logger.error(f"Response headers: {response.headers}")
            logger.error(f"Response body: {response.text}")
            raise

    def get_door_status(self):
        """Get current status of all doors"""
        url = f"{UNIFI_BASE_URL}/developer/doors"
        logger.debug(f"Making GET request to {url}")
        logger.debug(f"Headers: {self.session.headers}")
        response = self.session.get(url)
        try:
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            logger.error(f"HTTP Error: {e}")
            logger.error(f"Response status code: {response.status_code}")
            logger.error(f"Response headers: {response.headers}")
            logger.error(f"Response body: {response.text}")
            raise

# Initialize Unifi API client
try:
    unifi = UnifiAPI()
    logger.info("Unifi API client initialized successfully")
except Exception as e:
    logger.error(f"Failed to initialize Unifi API client: {str(e)}")
    unifi = None

@bot.event
async def on_ready():
    logger.info(f'Logged in as {bot.user.name}')
    try:
        await bot.sync_application_commands()
        logger.info("Synced slash commands")
    except Exception as e:
        logger.error(f"Failed to sync commands: {e}")

@bot.slash_command(name="unlock", description="Unlock all doors by enabling evacuation mode")
async def unlock(interaction: nextcord.Interaction):
    try:
        if not unifi:
            raise RuntimeError("Unifi API is not properly configured. Please check the bot's console for details.")
            
        await interaction.response.defer()
        result = unifi.set_evacuation_mode(True)
        logger.info("Successfully unlocked all doors")
        await interaction.followup.send("✅ All doors have been unlocked")
    except Exception as e:
        logger.error(f"Error in unlock command: {str(e)}")
        await interaction.followup.send("❌ Failed to unlock doors. Please check the bot's console for details.")

@bot.slash_command(name="lock", description="Lock all doors by disabling evacuation mode")
async def lock(interaction: nextcord.Interaction):
    try:
        if not unifi:
            raise RuntimeError("Unifi API is not properly configured. Please check the bot's console for details.")
            
        await interaction.response.defer()
        result = unifi.set_evacuation_mode(False)
        logger.info("Successfully locked all doors")
        await interaction.followup.send("✅ All doors have been locked")
    except Exception as e:
        logger.error(f"Error in lock command: {str(e)}")
        await interaction.followup.send("❌ Failed to lock doors. Please check the bot's console for details.")

@bot.slash_command(name="status", description="Check the current status of all doors")
async def status(interaction: nextcord.Interaction):
    try:
        if not unifi:
            raise RuntimeError("Unifi API is not properly configured. Please check the bot's console for details.")
            
        await interaction.response.defer()
        response = unifi.get_door_status()
        
        # Create a formatted message with door statuses
        status_message = "**Door Status:**\n"
        
        # Check if response is a dictionary with 'data' field
        if isinstance(response, dict) and 'data' in response:
            doors = response['data']
            for door in doors:
                door_status = door.get('door_lock_relay_status', 'lock')
                status = "🔓 Unlocked" if door_status == 'unlock' else "🔒 Locked"
                status_message += f"- {door.get('name', 'Unknown')}: {status}\n"
        else:
            status_message += str(response)
        
        logger.info("Successfully retrieved door status")
        await interaction.followup.send(status_message)
    except Exception as e:
        logger.error(f"Error in status command: {str(e)}")
        await interaction.followup.send("❌ Failed to get door status. Please check the bot's console for details.")

# Run the bot
bot.run(os.getenv('DISCORD_TOKEN')) 