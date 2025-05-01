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
import datetime
import pathlib
import re
import asyncio
import time

# Suppress SSL warnings (unifi console local access is self-signed)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Load environment variables
load_dotenv()

# Debug and silent mode configuration
DEBUG = os.getenv('DEBUG', 'false').lower() in ('true', '1', 't', 'yes')
SILENT_MODE = os.getenv('SILENT_MODE', 'false').lower() in ('true', '1', 't', 'yes')

# Channel name rate limiting
# Discord has a limit of 2 channel name changes per 10 minutes (per channel)
channel_update_history = {}  # Will store timestamps of recent updates
MAX_UPDATES_PER_PERIOD = 2  # Discord allows 2 updates
RATE_LIMIT_PERIOD = 60 * 10  # 10 minutes in seconds

# ntfy notification configuration
NTFY_URL = os.getenv('NTFY_URL', '')  # Empty string means no notifications
NTFY_TOPIC = os.getenv('NTFY_TOPIC', 'door-bot-alerts')

# Audit logging configuration
AUDIT_LOGGING = os.getenv('AUDIT_LOGGING', 'false').lower() in ('true', '1', 't', 'yes')
AUDIT_LOG_DIR = os.getenv('AUDIT_LOG_DIR', 'logs')

# Channel restriction configuration
ALLOWED_CHANNEL_IDS = os.getenv('ALLOWED_CHANNEL_IDS', '').strip()
ALLOWED_CHANNELS = [int(channel_id.strip()) for channel_id in ALLOWED_CHANNEL_IDS.split(',') if channel_id.strip()] if ALLOWED_CHANNEL_IDS else []

# Status channel configuration
STATUS_CHANNEL_ID = os.getenv('STATUS_CHANNEL_ID', '').strip()
STATUS_CHANNEL_ID = int(STATUS_CHANNEL_ID) if STATUS_CHANNEL_ID.isdigit() else None
STATUS_CHANNEL_NAME_PREFIX = os.getenv('STATUS_CHANNEL_NAME_PREFIX', 'doors').strip()
LOCKED_EMOJI = "🔐"
UNLOCKED_EMOJI = "🔓"

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


#Audit logging setup
# Create logs directory if it doesn't exist and audit logging is enabled
if AUDIT_LOGGING:
    os.makedirs(AUDIT_LOG_DIR, exist_ok=True)

# Global dictionary to track pending update tasks
pending_channel_updates = {}

# Function to check if a channel is allowed
def is_channel_allowed(channel_id):
    """
    Check if the channel is allowed to execute commands
    
    Args:
        channel_id: The channel ID to check
        
    Returns:
        bool: True if the channel is allowed, False otherwise
    """
    # If no channels are specified, all channels are allowed
    if not ALLOWED_CHANNELS:
        return True
    
    return channel_id in ALLOWED_CHANNELS

# Function to log command usage to audit log
def log_to_audit(username, command, details=None):
    """
    Log command usage to audit log file
    
    Args:
        username (str): The username of the user who ran the command
        command (str): The command that was run
        details (str, optional): Additional details about the command execution
    """
    if not AUDIT_LOGGING:
        return  # Skip if audit logging is disabled
        
    try:
        # Create a timestamp
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Get today's date for the log filename
        today = datetime.date.today().strftime("%Y-%m-%d")
        log_file = pathlib.Path(AUDIT_LOG_DIR) / f"{today}.log"
        
        # Format the log entry
        log_message = f"[{timestamp}] - [{username}] ran [{command}]"
        if details:
            log_message += f" - {details}"
        
        # Write to log file
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(log_message + "\n")
            
        if DEBUG:
            logger.debug(f"Audit log entry written: {log_message}")
    except Exception as e:
        # Don't let audit logging failures affect the main application
        if not SILENT_MODE:
            logger.error(f"Failed to write to audit log: {str(e)}")
            if DEBUG:
                logger.debug(f"Error details: {repr(e)}")

# Function to schedule a delayed channel name update
async def schedule_delayed_update(guild, is_unlocked, delay_seconds):
    """
    Schedule a delayed channel name update to occur after rate limit cooldown
    
    Args:
        guild: The Discord guild object
        is_unlocked (bool): Whether doors are unlocked
        delay_seconds (int): Delay in seconds before trying update
    """
    channel_key = f"{guild.id}-{STATUS_CHANNEL_ID}"
    
    # If there's already a pending task, keep it and don't create a new one
    if channel_key in pending_channel_updates and not pending_channel_updates[channel_key].done():
        logger.debug(f"Task already pending for channel {STATUS_CHANNEL_ID}, keeping existing schedule")
        return
    
    # Add extra buffer to ensure we're past the rate limit
    delay_with_buffer = delay_seconds + 10
    
    logger.info(f"Scheduling channel name update for {delay_with_buffer} seconds from now")
    
    # Define the delayed task
    async def delayed_update_task():
        try:
            logger.debug(f"Waiting {delay_with_buffer} seconds before updating channel name")
            await asyncio.sleep(delay_with_buffer)
            logger.info(f"Executing delayed channel name update")
            await update_status_channel(guild, is_unlocked, force_check=True)
        except asyncio.CancelledError:
            logger.debug("Delayed channel update was cancelled")
        except Exception as e:
            logger.error(f"Error in delayed channel update: {str(e)}")
            if DEBUG:
                logger.debug(f"Error details: {repr(e)}")
        finally:
            # Remove the task from tracking once it's complete
            if channel_key in pending_channel_updates and pending_channel_updates[channel_key].done():
                del pending_channel_updates[channel_key]
    
    # Create and store the task
    task = asyncio.create_task(delayed_update_task())
    pending_channel_updates[channel_key] = task

# Function to update status channel name based on door status
async def update_status_channel(guild, is_unlocked=False, force_check=False):
    """
    Update the status channel name based on door status
    
    Args:
        guild: The Discord guild object
        is_unlocked (bool): Whether doors are unlocked
        force_check (bool): Whether to force a status check from API instead of using provided status
    """
    if not STATUS_CHANNEL_ID:
        if DEBUG:
            logger.debug("Status channel updates disabled (STATUS_CHANNEL_ID not set)")
        return
    
    # If force_check is True, get the current door status from the API
    if force_check and unifi:
        try:
            logger.debug("Forcing check of current door status from API")
            response = unifi.get_door_status()
            doors_unlocked = False
            
            if isinstance(response, dict) and 'data' in response:
                doors = response['data']
                # Check if any door is unlocked
                for door in doors:
                    if door.get('door_lock_relay_status', 'lock') == 'unlock':
                        doors_unlocked = True
                        break
            
            # Update the is_unlocked parameter with fresh data
            is_unlocked = doors_unlocked
            logger.debug(f"Updated door status: doors are {'unlocked' if is_unlocked else 'locked'}")
        except Exception as e:
            logger.error(f"Failed to check door status for delayed update: {str(e)}")
            if DEBUG:
                logger.debug(f"Error details: {repr(e)}")
    
    # Get current time for rate limiting
    current_time = datetime.datetime.now().timestamp()
    channel_key = None
    
    try:
        # Get the channel
        channel = guild.get_channel(STATUS_CHANNEL_ID)
        if not channel:
            logger.warning(f"Status channel with ID {STATUS_CHANNEL_ID} not found")
            return
            
        # Create channel key for rate limiting
        channel_key = f"{guild.id}-{channel.id}"
            
        # Determine the emoji to use
        emoji = UNLOCKED_EMOJI if is_unlocked else LOCKED_EMOJI
        
        # Strip any existing emojis from the channel name
        # This regex removes any emoji characters at the end of the name
        base_name = re.sub(r'[-\s]*[\U00010000-\U0010ffff]$', '', STATUS_CHANNEL_NAME_PREFIX)
        
        # Create the new name
        new_name = f"{base_name}-{emoji}"
        
        # Check if the name is already correct
        if channel.name == new_name:
            if DEBUG:
                logger.debug(f"Channel name is already {new_name}, skipping update")
            return
        
        # Check rate limit - allow 2 changes per 10 minutes
        if channel_key in channel_update_history:
            # Get list of recent updates and filter out old ones
            recent_updates = [timestamp for timestamp in channel_update_history[channel_key] 
                             if current_time - timestamp < RATE_LIMIT_PERIOD]
            
            # If we already have made MAX_UPDATES_PER_PERIOD changes, don't allow more
            if len(recent_updates) >= MAX_UPDATES_PER_PERIOD:
                # Calculate when the oldest update will expire
                oldest_update = min(recent_updates)
                time_until_available = int(RATE_LIMIT_PERIOD - (current_time - oldest_update))
                logger.warning(f"Skipping immediate channel name update due to rate limiting. Reached limit of {MAX_UPDATES_PER_PERIOD} updates per {RATE_LIMIT_PERIOD/60} minutes.")
                
                # Schedule a delayed update
                await schedule_delayed_update(guild, is_unlocked, time_until_available)
                return
                
            # Update the list with only recent updates
            channel_update_history[channel_key] = recent_updates
        else:
            # First update for this channel
            channel_update_history[channel_key] = []
            
        # Update the channel name
        if DEBUG:
            logger.debug(f"Updating channel name from '{channel.name}' to '{new_name}'")
            
        await channel.edit(name=new_name)
        
        # Add this update to the history
        if channel_key in channel_update_history:
            channel_update_history[channel_key].append(current_time)
        else:
            channel_update_history[channel_key] = [current_time]
            
        logger.info(f"Updated status channel name to {new_name}")
        
    except nextcord.errors.Forbidden as e:
        logger.error(f"Failed to update status channel: Missing permissions. Make sure the bot has 'Manage Channels' permission")
        if DEBUG:
            logger.debug(f"Error details: {repr(e)}")
    except nextcord.errors.HTTPException as e:
        if e.status == 429:  # Rate limit error
            logger.warning(f"Rate limit hit when updating channel name. Discord limits channel name changes to {MAX_UPDATES_PER_PERIOD} per {RATE_LIMIT_PERIOD/60} minutes")
            # Track this failed attempt too to avoid hitting rate limits again
            if channel_key:
                if channel_key in channel_update_history:
                    channel_update_history[channel_key].append(current_time)
                else:
                    channel_update_history[channel_key] = [current_time]
                
                # Calculate when we can try again
                recent_updates = channel_update_history[channel_key]
                if len(recent_updates) > 0:
                    oldest_update = min(recent_updates)
                    time_until_available = int(RATE_LIMIT_PERIOD - (current_time - oldest_update))
                    
                    # Schedule a delayed update
                    await schedule_delayed_update(guild, is_unlocked, time_until_available)
        else:
            logger.error(f"HTTP error when updating status channel: {str(e)}")
        if DEBUG:
            logger.debug(f"Error details: {repr(e)}")
    except Exception as e:
        logger.error(f"Failed to update status channel: {str(e)}")
        if DEBUG:
            logger.debug(f"Error details: {repr(e)}")

# Output startup mode information
if not SILENT_MODE:
    if DEBUG:
        logger.info("Starting in DEBUG mode (verbose logging enabled)")
    else:
        logger.info("Starting in normal mode")
    
    if AUDIT_LOGGING:
        logger.info(f"Audit logging enabled (directory: {AUDIT_LOG_DIR})")
        
    if NTFY_URL:
        logger.info(f"ntfy notifications enabled (topic: {NTFY_TOPIC})")
        
    if ALLOWED_CHANNELS:
        logger.info(f"Channel restrictions enabled. Allowed channels: {ALLOWED_CHANNELS}")
    else:
        logger.info("No channel restrictions. Commands can be used in any channel.")
        
    if STATUS_CHANNEL_ID:
        logger.info(f"Status channel updates enabled. Channel ID: {STATUS_CHANNEL_ID}")
    else:
        logger.info("Status channel updates disabled (STATUS_CHANNEL_ID not set)")

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
        
        # Check door status and update channel name on startup
        if STATUS_CHANNEL_ID and unifi:
            try:
                response = unifi.get_door_status()
                doors_unlocked = False
                
                if isinstance(response, dict) and 'data' in response:
                    doors = response['data']
                    # Check if any door is unlocked
                    for door in doors:
                        if door.get('door_lock_relay_status', 'lock') == 'unlock':
                            doors_unlocked = True
                            break
                            
                # Log initial status
                logger.info(f"Initial door status check completed. Doors are {'unlocked' if doors_unlocked else 'locked'}")
                
                # Update all guild channels (bot might be in multiple servers)
                for guild in bot.guilds:
                    # Wait a moment between updates to avoid rate limits when in multiple guilds
                    await update_status_channel(guild, doors_unlocked)
                    await asyncio.sleep(1)  # Small sleep to avoid bunching requests
                    
            except Exception as e:
                logger.error(f"Failed to update status channel on startup: {str(e)}")
                if DEBUG:
                    logger.debug(f"Exception details: {repr(e)}")
        
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
        # Get username for logging before any checks
        username = interaction.user.display_name
        
        # Validate that this is not a DM
        if not interaction.guild:
            await interaction.response.send_message("❌ This command cannot be used in DMs.", ephemeral=True)
            logger.warning(f"User {username} attempted to use unlock command in DM")
            # Log the rejected DM attempt to audit log
            log_to_audit(username, "unlock", "Rejected - Command used in DM")
            return
            
        # Validate the channel
        if not is_channel_allowed(interaction.channel.id):
            await interaction.response.send_message("❌ This command can only be used in designated channels.", ephemeral=True)
            logger.warning(f"User {username} attempted to use unlock command in unauthorized channel {interaction.channel.name} ({interaction.channel.id})")
            # Log the rejected channel attempt to audit log
            log_to_audit(username, "unlock", f"Rejected - Unauthorized channel: {interaction.channel.name}")
            return
        
        # Log command usage to audit log
        log_to_audit(username, "unlock")
        
        if not unifi:
            error_msg = "Unifi API is not properly configured. Please check the bot's console for details."
            raise RuntimeError(error_msg)
            
        await interaction.response.defer()
        if DEBUG:
            logger.debug("Processing unlock command")
        result = unifi.set_evacuation_mode(True)
        logger.info("Successfully unlocked all doors")
        
        # Log success to audit log
        log_to_audit(username, "unlock", "Success - All doors unlocked")
        
        # Send response to Discord - do this before channel updates which might fail
        await interaction.followup.send("✅ All doors have been unlocked")
        
        # Update status channel name for all guilds (moved to end with better error handling)
        if STATUS_CHANNEL_ID:
            for guild in bot.guilds:
                await update_status_channel(guild, is_unlocked=True)
                
    except Exception as e:
        error_msg = f"Error in unlock command: {str(e)}"
        logger.error(error_msg)
        if DEBUG:
            logger.debug(f"Exception details: {repr(e)}")
            
        # Log failure to audit log
        username = interaction.user.display_name
        log_to_audit(username, "unlock", "Failed")
            
        # Send notification for command errors
        send_notification(
            "Door Unlock Failed", 
            f"Error: {str(e)}\nCommand triggered by: {interaction.user.display_name}",
            priority="high",
            tags=["warning", "unlock-error"]
        )
        
        # Only send error response if we haven't already responded with a validation error
        if interaction.response.is_done():
            await interaction.followup.send("❌ Failed to unlock doors. Please check the bot's console for details.")
        else:
            await interaction.response.send_message("❌ Failed to unlock doors. Please check the bot's console for details.")

@bot.slash_command(name="lock", description="Lock all doors by disabling evacuation mode")
async def lock(interaction: nextcord.Interaction):
    try:
        # Get username for logging before any checks
        username = interaction.user.display_name
        
        # Validate that this is not a DM
        if not interaction.guild:
            await interaction.response.send_message("❌ This command cannot be used in DMs.", ephemeral=True)
            logger.warning(f"User {username} attempted to use lock command in DM")
            # Log the rejected DM attempt to audit log
            log_to_audit(username, "lock", "Rejected - Command used in DM")
            return
            
        # Validate the channel
        if not is_channel_allowed(interaction.channel.id):
            await interaction.response.send_message("❌ This command can only be used in designated channels.", ephemeral=True)
            logger.warning(f"User {username} attempted to use lock command in unauthorized channel {interaction.channel.name} ({interaction.channel.id})")
            # Log the rejected channel attempt to audit log
            log_to_audit(username, "lock", f"Rejected - Unauthorized channel: {interaction.channel.name}")
            return
        
        # Log command usage to audit log
        log_to_audit(username, "lock")
        
        if not unifi:
            error_msg = "Unifi API is not properly configured. Please check the bot's console for details."
            raise RuntimeError(error_msg)
            
        await interaction.response.defer()
        if DEBUG:
            logger.debug("Processing lock command")
        result = unifi.set_evacuation_mode(False)
        logger.info("Successfully locked all doors")
        
        # Log success to audit log
        log_to_audit(username, "lock", "Success - All doors locked")
        
        # Send response to Discord - do this before channel updates which might fail
        await interaction.followup.send("✅ All doors have been locked")
        
        # Update status channel name for all guilds (moved to end with better error handling)
        if STATUS_CHANNEL_ID:
            for guild in bot.guilds:
                await update_status_channel(guild, is_unlocked=False)
                
    except Exception as e:
        error_msg = f"Error in lock command: {str(e)}"
        logger.error(error_msg)
        if DEBUG:
            logger.debug(f"Exception details: {repr(e)}")
            
        # Log failure to audit log
        username = interaction.user.display_name
        log_to_audit(username, "lock", "Failed")
            
        # Send notification for command errors
        send_notification(
            "Door Lock Failed", 
            f"Error: {str(e)}\nCommand triggered by: {interaction.user.display_name}",
            priority="high",
            tags=["warning", "lock-error"]
        )
        
        # Only send error response if we haven't already responded with a validation error
        if interaction.response.is_done():
            await interaction.followup.send("❌ Failed to lock doors. Please check the bot's console for details.")
        else:
            await interaction.response.send_message("❌ Failed to lock doors. Please check the bot's console for details.")

@bot.slash_command(name="status", description="Check the current status of all doors")
async def status(interaction: nextcord.Interaction):
    try:
        # Get username for logging before any checks
        username = interaction.user.display_name
        
        # Validate that this is not a DM
        if not interaction.guild:
            await interaction.response.send_message("❌ This command cannot be used in DMs.", ephemeral=True)
            logger.warning(f"User {username} attempted to use status command in DM")
            # Log the rejected DM attempt to audit log
            log_to_audit(username, "status", "Rejected - Command used in DM")
            return
            
        # Validate the channel
        if not is_channel_allowed(interaction.channel.id):
            await interaction.response.send_message("❌ This command can only be used in designated channels.", ephemeral=True)
            logger.warning(f"User {username} attempted to use status command in unauthorized channel {interaction.channel.name} ({interaction.channel.id})")
            # Log the rejected channel attempt to audit log
            log_to_audit(username, "status", f"Rejected - Unauthorized channel: {interaction.channel.name}")
            return
        
        # Log command usage to audit log
        log_to_audit(username, "status")
        
        if not unifi:
            error_msg = "Unifi API is not properly configured. Please check the bot's console for details."
            raise RuntimeError(error_msg)
            
        await interaction.response.defer()
        if DEBUG:
            logger.debug("Processing status command")
        response = unifi.get_door_status()
        
        # Create a formatted message with door statuses
        status_message = "**Door Status:**\n"
        door_statuses = []
        doors_unlocked = False
        
        # Check if response is a dictionary with 'data' field
        if isinstance(response, dict) and 'data' in response:
            doors = response['data']
            if DEBUG:
                logger.debug(f"Door data received: {doors}")
            for door in doors:
                door_status = door.get('door_lock_relay_status', 'lock')
                status = "🔓 Unlocked" if door_status == 'unlock' else "🔒 Locked"
                door_name = door.get('name', 'Unknown')
                status_message += f"- {door_name}: {status}\n"
                door_statuses.append(f"{door_name}: {status}")
                
                # Check if any door is unlocked
                if door_status == 'unlock':
                    doors_unlocked = True
        else:
            if DEBUG:
                logger.debug(f"Unexpected response format: {response}")
            status_message += str(response)
            door_statuses.append("Unexpected response format")
        
        logger.info("Successfully retrieved door status")
        
        # Log success to audit log with door statuses
        log_to_audit(username, "status", f"Success - {', '.join(door_statuses)}")
        
        # Send response to Discord first - do this before channel updates
        await interaction.followup.send(status_message)
        
        # Update status channel name for all guilds (moved to end with better error handling)
        if STATUS_CHANNEL_ID:
            for guild in bot.guilds:
                await update_status_channel(guild, is_unlocked=doors_unlocked)
                
    except Exception as e:
        error_msg = f"Error in status command: {str(e)}"
        logger.error(error_msg)
        if DEBUG:
            logger.debug(f"Exception details: {repr(e)}")
            
        # Log failure to audit log
        username = interaction.user.display_name
        log_to_audit(username, "status", "Failed")
            
        # Send notification for command errors
        send_notification(
            "Door Status Check Failed", 
            f"Error: {str(e)}\nCommand triggered by: {interaction.user.display_name}",
            priority="high",
            tags=["warning", "status-error"]
        )
        
        # Only send error response if we haven't already responded with a validation error
        if interaction.response.is_done():
            await interaction.followup.send("❌ Failed to get door status. Please check the bot's console for details.")
        else:
            await interaction.response.send_message("❌ Failed to get door status. Please check the bot's console for details.")

# Run the bot
bot.run(os.getenv('DISCORD_TOKEN')) 