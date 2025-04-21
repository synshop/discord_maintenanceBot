import discord
from discord import app_commands
from discord.ext import tasks
import json
import os
import os.path
from datetime import datetime, timedelta
import asyncio
import logging
from dotenv import load_dotenv

# --- Load Environment Variables ---
load_dotenv()

# --- Configuration from Environment Variables ---
BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")

# Default reminder repeat days
DEFAULT_REMINDER_DAYS = 7
try:
    REMINDER_REPEAT_DAYS_DEFAULT_ENV = int(os.getenv("REMINDER_REPEAT_DAYS", str(DEFAULT_REMINDER_DAYS)))
    if REMINDER_REPEAT_DAYS_DEFAULT_ENV <= 0:
        logging.warning(f"REMINDER_REPEAT_DAYS in .env must be positive. Using default: {DEFAULT_REMINDER_DAYS}")
        REMINDER_REPEAT_DAYS_DEFAULT_ENV = DEFAULT_REMINDER_DAYS
except ValueError:
    logging.warning(f"Invalid REMINDER_REPEAT_DAYS in .env. Must be an integer. Using default: {DEFAULT_REMINDER_DAYS}")
    REMINDER_REPEAT_DAYS_DEFAULT_ENV = DEFAULT_REMINDER_DAYS

# Default check interval
DEFAULT_CHECK_INTERVAL = 60
try:
    CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL_SECONDS", str(DEFAULT_CHECK_INTERVAL)))
    if CHECK_INTERVAL_SECONDS <= 0:
        logging.warning(f"CHECK_INTERVAL_SECONDS in .env must be positive. Using default: {DEFAULT_CHECK_INTERVAL}")
        CHECK_INTERVAL_SECONDS = DEFAULT_CHECK_INTERVAL
except ValueError:
    logging.warning(f"Invalid CHECK_INTERVAL_SECONDS in .env. Must be an integer. Using default: {DEFAULT_CHECK_INTERVAL}")
    CHECK_INTERVAL_SECONDS = DEFAULT_CHECK_INTERVAL

# Data storage file path
DATA_FILE = "/bot/data/maintenancebot_data.json"

# Versioning info
VERSION_FILE = "/bot/version.json"
CURRENT_VERSION = "0.0.0"  # Default version if no version file exists
RELEASE_NOTES = ""
NEW_VERSION_DETECTED = False

# Configuration for version announcement channel
DEFAULT_ANNOUNCEMENT_CHANNEL = "general"  # Default channel if not specified
ANNOUNCEMENT_CHANNEL = os.getenv("ANNOUNCEMENT_CHANNEL", DEFAULT_ANNOUNCEMENT_CHANNEL)
# Check if the value is a numeric ID (for direct channel ID usage)
try:
    ANNOUNCEMENT_CHANNEL_ID = int(ANNOUNCEMENT_CHANNEL)
    ANNOUNCEMENT_CHANNEL_NAME = None
    logger.info(f"Will announce version updates to channel ID: {ANNOUNCEMENT_CHANNEL_ID}")
except ValueError:
    ANNOUNCEMENT_CHANNEL_ID = None
    ANNOUNCEMENT_CHANNEL_NAME = ANNOUNCEMENT_CHANNEL.lower()
    logger.info(f"Will announce version updates to channel named: {ANNOUNCEMENT_CHANNEL_NAME}")

# --- Validate Essential Config ---
if not BOT_TOKEN:
    logging.critical("CRITICAL ERROR: DISCORD_BOT_TOKEN not found in .env file or environment variables.")
    exit("Bot token is missing. Please check your .env file.")

# --- Global Settings ---
global_settings = {
    "reminder_repeat_days": REMINDER_REPEAT_DAYS_DEFAULT_ENV
}

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s:%(levelname)s:%(name)s: %(message)s')
logger = logging.getLogger('discord')

# --- Global Timers Dictionary ---
timers = {}

# --- Helper Functions ---
def save_data():
    """Saves the current state of timers and global settings to the JSON file."""
    global timers, global_settings
    try:
        serializable_timers = {}
        for guild_id, guild_timers in timers.items():
            serializable_timers[str(guild_id)] = {}
            for name, data in guild_timers.items():
                serializable_data = data.copy()
                if isinstance(serializable_data.get("next_due"), datetime):
                    serializable_data["next_due"] = serializable_data["next_due"].isoformat()
                if isinstance(serializable_data.get("last_reminded"), datetime):
                    serializable_data["last_reminded"] = serializable_data["last_reminded"].isoformat()
                serializable_data.pop("reminder_repeat_days", None)
                serializable_timers[str(guild_id)][name] = serializable_data
        data_to_save = {
            "global_settings": global_settings,
            "timers": serializable_timers
        }
        with open(DATA_FILE, 'w') as f:
            json.dump(data_to_save, f, indent=4)
        logger.info(f"Data saved to {DATA_FILE}")
    except IOError as e:
        logger.error(f"Error saving data to {DATA_FILE}: {e}")
    except Exception as e:
        logger.error(f"Unexpected error during data serialization: {e}")

def load_data():
    """Loads timers and global settings from the JSON file."""
    global timers, global_settings
    if not os.path.exists(DATA_FILE):
        timers = {}
        logger.warning(f"{DATA_FILE} not found. Using default global settings from .env/code.")
        return
    try:
        with open(DATA_FILE, 'r') as f:
            loaded_data = json.load(f)
        loaded_settings = loaded_data.get("global_settings", {})
        global_settings["reminder_repeat_days"] = loaded_settings.get(
            "reminder_repeat_days", global_settings["reminder_repeat_days"]
        )
        loaded_timers_data = loaded_data.get("timers", {})
        timers = {}
        for guild_id_str, guild_timers_data in loaded_timers_data.items():
            try:
                guild_id = int(guild_id_str)
                timers[guild_id] = {}
                for name, data in guild_timers_data.items():
                    deserialized_data = data.copy()
                    deserialized_data.pop("reminder_repeat_days", None)
                    if deserialized_data.get("next_due"):
                        try:
                            deserialized_data["next_due"] = datetime.fromisoformat(deserialized_data["next_due"])
                        except (ValueError, TypeError):
                            logger.warning(f"Could not parse next_due for timer '{name}' in guild {guild_id}. Setting to None.")
                            deserialized_data["next_due"] = None
                    if deserialized_data.get("last_reminded"):
                        try:
                            deserialized_data["last_reminded"] = datetime.fromisoformat(deserialized_data["last_reminded"])
                        except (ValueError, TypeError):
                            logger.warning(f"Could not parse last_reminded for timer '{name}' in guild {guild_id}. Setting to None.")
                            deserialized_data["last_reminded"] = None
                    timers[guild_id][name] = deserialized_data
            except ValueError:
                logger.error(f"Invalid guild ID '{guild_id_str}' found in {DATA_FILE}. Skipping.")
                continue
        logger.info(f"Data loaded successfully from {DATA_FILE}")
        logger.info(f"Using global reminder interval: {global_settings['reminder_repeat_days']} days")
    except (IOError, json.JSONDecodeError) as e:
        logger.error(f"Error loading data from {DATA_FILE}: {e}. Using default global settings.")
        timers = {}
    except Exception as e:
        logger.error(f"Unexpected error during data deserialization: {e}")
        timers = {}

def calculate_next_due(interval_value, interval_unit, start_time=None):
    if start_time is None:
        start_time = datetime.utcnow()
    
    # Normalize to start of day
    start_time = start_time.replace(hour=0, minute=0, second=0, microsecond=0)
    
    delta = None
    unit = interval_unit.lower()
    if unit == "days": delta = timedelta(days=interval_value)
    elif unit == "weeks": delta = timedelta(weeks=interval_value)
    elif unit == "months": delta = timedelta(days=interval_value * 30); logger.warning("Using approximation for 'months' (30 days).")
    else: raise ValueError("Invalid interval unit. Use 'days', 'weeks', or 'months'.")
    return start_time + delta if delta else None

def strfdelta(tdelta, fmt):
    d = {"days": tdelta.days}
    hours, rem = divmod(tdelta.seconds, 3600); minutes, seconds = divmod(rem, 60)
    d["days"] = max(0, d["days"]); d["hours"] = max(0, hours); d["minutes"] = max(0, minutes); d["seconds"] = max(0, seconds)
    try: return fmt.format(**d)
    except KeyError as e: logger.error(f"Error formatting timedelta: Invalid key {e} in format string '{fmt}'"); return f"{d['days']}d {d['hours']}h {d['minutes']}m"

def discord_date(dt, format_code="D"):
    """Convert a datetime object to Discord's date-only timestamp format.
    
    Format codes:
    d: Short Date (e.g., 04/20/2024)
    D: Long Date (e.g., April 20, 2024)
    R: Relative Time (e.g., 2 days ago, in a month)
    """
    if not dt:
        return "Not set"
    
    # Convert to Unix timestamp (seconds since epoch)
    unix_timestamp = int(dt.timestamp())
    
    # Return in Discord's timestamp format
    return f"<t:{unix_timestamp}:{format_code}>"

# Load version information
def load_version_info():
    """Loads version information from the version file."""
    global CURRENT_VERSION, RELEASE_NOTES, NEW_VERSION_DETECTED
    
    if not os.path.exists(VERSION_FILE):
        logger.warning(f"{VERSION_FILE} not found. Using default version {CURRENT_VERSION}.")
        # Create the initial version file
        save_version_info(CURRENT_VERSION, "", False)
        return
    
    try:
        with open(VERSION_FILE, 'r') as f:
            version_data = json.load(f)
            
        stored_version = version_data.get("last_seen_version", "0.0.0")
        CURRENT_VERSION = version_data.get("current_version", CURRENT_VERSION)
        RELEASE_NOTES = version_data.get("release_notes", "")
        
        # Check if this is a new version
        NEW_VERSION_DETECTED = CURRENT_VERSION != stored_version
        
        if NEW_VERSION_DETECTED:
            logger.info(f"New version detected! Previous: {stored_version}, Current: {CURRENT_VERSION}")
        else:
            logger.info(f"Current version: {CURRENT_VERSION}")
            
    except (IOError, json.JSONDecodeError) as e:
        logger.error(f"Error loading version data: {e}")
        # Create the file with defaults if there was an error
        save_version_info(CURRENT_VERSION, "", False)

# Save version information
def save_version_info(version, notes, seen=True):
    """Saves version information to the version file."""
    try:
        version_data = {
            "current_version": version,
            "release_notes": notes,
            "last_seen_version": version if seen else "0.0.0"
        }
        
        # Create directory if it doesn't exist
        os.makedirs(os.path.dirname(VERSION_FILE), exist_ok=True)
        
        with open(VERSION_FILE, 'w') as f:
            json.dump(version_data, f, indent=4)
            
        logger.info(f"Version data saved: {version}")
        
    except IOError as e:
        logger.error(f"Error saving version data: {e}")

# --- Bot Setup ---
intents = discord.Intents.default()
intents.guilds = True
intents.messages = True
intents.message_content = True

class MaintenanceBot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.synced = False

    async def on_ready(self):
        """Called when the bot is ready and connected."""
        if not self.synced:
            # This syncs the commands to Discord - only need to do it once
            await self.tree.sync()
            self.synced = True
            
        logger.info(f'Logged in as {self.user.name} ({self.user.id})')
        logger.info('Loading data...')
        load_data()
        load_version_info()  # Load version info
        
        # Announce new version if detected
        if NEW_VERSION_DETECTED:
            await self.announce_new_version()
        
        logger.info('Starting timer check loop...')
        check_timers_task.change_interval(seconds=CHECK_INTERVAL_SECONDS)
        check_timers_task.start()
        logger.info(f"Timer check interval: {CHECK_INTERVAL_SECONDS} seconds.")
        logger.info('Bot is ready.')

    async def announce_new_version(self):
        """Announces a new version to all servers where the bot has permission."""
        global CURRENT_VERSION, RELEASE_NOTES, NEW_VERSION_DETECTED
        
        logger.info(f"Announcing new version {CURRENT_VERSION} to all servers")
        
        for guild in self.guilds:
            # Find the appropriate channel based on configuration
            channel = None
            
            # First try to find by ID if configured that way
            if ANNOUNCEMENT_CHANNEL_ID is not None:
                channel = guild.get_channel(ANNOUNCEMENT_CHANNEL_ID)
                if channel:
                    logger.info(f"Found configured channel by ID {ANNOUNCEMENT_CHANNEL_ID} in {guild.name}")
            
            # If no channel by ID found, try by name
            if channel is None and ANNOUNCEMENT_CHANNEL_NAME is not None:
                for ch in guild.text_channels:
                    if ch.name.lower() == ANNOUNCEMENT_CHANNEL_NAME:
                        channel = ch
                        logger.info(f"Found configured channel '{ANNOUNCEMENT_CHANNEL_NAME}' in {guild.name}")
                        break
            
            # Fallback to searching for common channel names if configured channel not found
            if channel is None:
                common_channels = ["general", "bot-updates", "announcements", "bot", "bot-commands"]
                for name in common_channels:
                    for ch in guild.text_channels:
                        if ch.name.lower() == name:
                            channel = ch
                            logger.info(f"Using fallback channel '{name}' in {guild.name}")
                            break
                    if channel:
                        break
            
            # Last resort: use first writable channel
            if channel is None:
                for ch in guild.text_channels:
                    permissions = ch.permissions_for(guild.me)
                    if permissions.send_messages:
                        channel = ch
                        logger.info(f"Using first available channel '{ch.name}' in {guild.name}")
                        break
            
            # If we found a channel, send the announcement
            if channel:
                try:
                    embed = discord.Embed(
                        title=f"🚀 Maintenance Timer Bot Updated to v{CURRENT_VERSION}",
                        description="The bot has been updated with new features and improvements!",
                        color=discord.Color.blue()
                    )
                    
                    if RELEASE_NOTES:
                        embed.add_field(name="What's New", value=RELEASE_NOTES, inline=False)
                    
                    embed.add_field(
                        name="Commands",
                        value="Use `/help` to see all available commands and features.",
                        inline=False
                    )
                    
                    embed.set_footer(text=f"Version {CURRENT_VERSION}")
                    
                    await channel.send(embed=embed)
                    logger.info(f"Sent update announcement to {guild.name} in {channel.name}")
                except Exception as e:
                    logger.error(f"Failed to send update announcement to {guild.name}: {e}")
        
        # Mark this version as seen
        save_version_info(CURRENT_VERSION, RELEASE_NOTES, True)
        NEW_VERSION_DETECTED = False

# Create bot instance
bot = MaintenanceBot()

# --- Background Task ---
@tasks.loop(seconds=DEFAULT_CHECK_INTERVAL)
async def check_timers_task():
    """Periodically checks all timers and sends reminders if due."""
    global timers, global_settings
    now = datetime.utcnow()
    data_to_save = False
    reminder_interval_days = global_settings.get("reminder_repeat_days", REMINDER_REPEAT_DAYS_DEFAULT_ENV)
    guild_ids = list(timers.keys())
    for guild_id in guild_ids:
        if guild_id not in timers: continue
        timer_names = list(timers.get(guild_id, {}).keys())
        for name in timer_names:
            if guild_id not in timers or name not in timers.get(guild_id, {}): continue
            timer_data = timers[guild_id][name]
            timer_channel_id = timer_data.get("channel_id")
            channel = bot.get_channel(timer_channel_id)
            if not channel:
                logger.warning(f"Timer '{name}' in guild {guild_id}: Channel {timer_channel_id} not found. Skipping.")
                continue
            next_due_dt = timer_data.get("next_due")
            is_pending = timer_data.get("is_pending", False)
            last_reminded_dt = timer_data.get("last_reminded")
            try:
                # Check 1: Initial Due Date
                if not is_pending and next_due_dt and now >= next_due_dt:
                    logger.info(f"Timer '{name}' in guild {guild_id} is due.")
                    embed = discord.Embed(
                        title=f"🚨 Maintenance Due: {name}",
                        description=f"**Task:** {timer_data['description']}\n**Owner:** {timer_data['owner']}\n**Due:** {discord_date(next_due_dt)}\n\nPlease complete the task and use `/done {name}`",
                        color=discord.Color.orange(),
                        timestamp=now
                    )
                    await channel.send(embed=embed)
                    timers[guild_id][name]["is_pending"] = True
                    timers[guild_id][name]["last_reminded"] = now
                    data_to_save = True
                    logger.info(f"Sent initial reminder for '{name}' in guild {guild_id}, channel {channel.id}.")
                # Check 2: Pending Reminder Repeat
                elif is_pending and last_reminded_dt:
                    next_reminder_time = last_reminded_dt + timedelta(days=reminder_interval_days)
                    if now >= next_reminder_time:
                        logger.info(f"Timer '{name}' in guild {guild_id} is pending - sending repeat reminder.")
                        embed = discord.Embed(
                            title=f"🔁 Maintenance Still Pending: {name}",
                            description=f"**Task:** {timer_data['description']}\n**Owner:** {timer_data['owner']}\n**Originally Due:** {discord_date(next_due_dt)}\n\nThis is a reminder (repeats every {reminder_interval_days} days until done).\nPlease complete the task and use `/done {name}`",
                            color=discord.Color.red(),
                            timestamp=now
                        )
                        await channel.send(f"cc: {timer_data['owner']}", embed=embed)
                        timers[guild_id][name]["last_reminded"] = now
                        data_to_save = True
                        logger.info(f"Sent repeat reminder for '{name}' in guild {guild_id}, channel {channel.id}.")
            except discord.errors.Forbidden:
                logger.error(f"Bot lacks permissions to send message in channel {timer_channel_id} for timer '{name}', guild {guild_id}.")
            except Exception as e:
                logger.error(f"Error processing timer '{name}' in guild {guild_id}: {e}", exc_info=True)
    if data_to_save:
        save_data()

@check_timers_task.before_loop
async def before_check_timers():
    await bot.wait_until_ready()
    logger.info("Bot is ready, preparing background timer check loop.")

# --- Slash Commands ---
@bot.tree.command(name="create_timer", description="Creates a new maintenance timer")
@app_commands.describe(
    name="A unique name for this timer (no spaces)",
    interval_value="How often the task repeats (e.g., 7, 2, 1)",
    interval_unit="The unit for the interval. (Days/Weeks/Months in plural)",
    owner="The primary person responsible. Including an @ will tag this person/group.",
    description="What needs to be done (can include spaces)"
)
@app_commands.choices(interval_unit=[
    app_commands.Choice(name="Days", value="days"),
    app_commands.Choice(name="Weeks", value="weeks"),
    app_commands.Choice(name="Months", value="months")
])
async def create_timer(interaction: discord.Interaction, name: str, interval_value: int, 
                       interval_unit: str, owner: str, description: str):
    """Creates a recurring maintenance task reminder."""
    if not interaction.guild:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return
        
    guild_id = interaction.guild.id
    channel_id = interaction.channel.id
    
    if interval_value <= 0:
        await interaction.response.send_message("❌ Error: Interval value must be positive.", ephemeral=True)
        return
        
    valid_units = ["days", "weeks", "months"]
    if interval_unit.lower() not in valid_units:
        await interaction.response.send_message(f"❌ Error: Interval unit must be one of: {', '.join(valid_units)}.", ephemeral=True)
        return
        
    if guild_id not in timers:
        timers[guild_id] = {}
        
    if name in timers[guild_id]:
        await interaction.response.send_message(f"❌ Error: A timer with the name '{name}' already exists in this server.", ephemeral=True)
        return
        
    try:
        next_due_time = calculate_next_due(interval_value, interval_unit)
        if not next_due_time:
            await interaction.response.send_message(f"❌ Error: Could not calculate next due date with unit '{interval_unit}'.", ephemeral=True)
            return
            
        new_timer = {
            "name": name,
            "interval_value": interval_value,
            "interval_unit": interval_unit.lower(),
            "description": description,
            "owner": owner,
            "channel_id": channel_id,
            "next_due": next_due_time,
            "is_pending": False,
            "last_reminded": None
        }
        
        timers[guild_id][name] = new_timer
        save_data()
        
        await interaction.response.send_message(
            f"✅ Timer '{name}' created successfully!\nIt will first trigger on {discord_date(next_due_time)}.\n"
            f"Pending reminders will repeat every {global_settings['reminder_repeat_days']} days (global setting)."
        )
        logger.info(f"Timer '{name}' created in guild {guild_id} by {interaction.user.name}.")
        
    except ValueError as e:
        await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message("❌ An unexpected error occurred.", ephemeral=True)
        logger.error(f"Error creating timer '{name}' in guild {guild_id}: {e}", exc_info=True)


@bot.tree.command(name="done", description="Marks a pending maintenance timer as completed")
@app_commands.describe(name="The name of the timer to mark as done")
async def done_timer(interaction: discord.Interaction, name: str):
    """Marks a specific maintenance task as done for its current cycle."""
    if not interaction.guild:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return
        
    guild_id = interaction.guild.id
    
    if guild_id not in timers or name not in timers[guild_id]:
        await interaction.response.send_message(f"❌ Error: Timer '{name}' not found.", ephemeral=True)
        return
        
    timer_data = timers[guild_id][name]
    
    if not timer_data.get("is_pending", False):
        await interaction.response.send_message(f"ℹ️ Timer '{name}' was not pending completion.", ephemeral=True)
        return
        
    try:
        interval_value = timer_data["interval_value"]
        interval_unit = timer_data["interval_unit"]
        next_due_time = calculate_next_due(interval_value, interval_unit)
        
        if not next_due_time:
            await interaction.response.send_message(f"❌ Error: Could not recalculate next due date for timer '{name}'.", ephemeral=True)
            return
            
        timers[guild_id][name]["is_pending"] = False
        timers[guild_id][name]["last_reminded"] = None
        timers[guild_id][name]["next_due"] = next_due_time
        save_data()
        
        await interaction.response.send_message(
            f"✅ Timer '{name}' marked as done by {interaction.user.mention}!\n"
            f"It will trigger again around {discord_date(next_due_time)}."
        )
        logger.info(f"Timer '{name}' marked done in guild {guild_id} by {interaction.user.name}.")
        
    except ValueError as e:
        await interaction.response.send_message(f"❌ Error resetting timer: {e}", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message("❌ An unexpected error occurred.", ephemeral=True)
        logger.error(f"Error marking timer '{name}' done in guild {guild_id}: {e}", exc_info=True)

@bot.tree.command(name="list_timers", description="Lists all active maintenance timers")
async def list_timers_cmd(interaction: discord.Interaction):
    """Displays the status of all configured maintenance timers for this server."""
    if not interaction.guild:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return
    
    guild_id = interaction.guild.id
    
    if guild_id not in timers or not timers[guild_id]:
        await interaction.response.send_message("ℹ️ No maintenance timers are set up.", ephemeral=True)
        return
    
    embed = discord.Embed(title=f"Maintenance Timers for {interaction.guild.name}", color=discord.Color.blue())
    embed.set_footer(text=f"Global pending reminder interval: {global_settings['reminder_repeat_days']} days")
    
    now = datetime.utcnow()
    sorted_timers = sorted(timers[guild_id].values(), key=lambda t: t.get('next_due') or datetime.max.replace(tzinfo=None))
    
    for timer_data in sorted_timers:
        name = timer_data['name']
        next_due_dt = timer_data.get('next_due')
        is_pending = timer_data.get('is_pending', False)
        owner = timer_data.get('owner', 'N/A')
        description = timer_data.get('description', 'N/A')
        channel = bot.get_channel(timer_data.get("channel_id", 0))
        channel_mention = channel.mention if channel else f"ID: {timer_data.get('channel_id', 'Unknown')}"
        
        status_emoji = "🚨 PENDING" if is_pending else "⏳ Active"
        
        if next_due_dt:
            if is_pending:
                time_display = f"Originally due: {discord_date(next_due_dt)} ({discord_date(next_due_dt, 'R')})"
            else:
                time_display = f"Next due: {discord_date(next_due_dt)} ({discord_date(next_due_dt, 'R')})"
        else:
            time_display = "Next due date not set."
        
        embed.add_field(
            name=f"{status_emoji} - {name}",
            value=f"**Owner:** {owner}\n**Task:** {description}\n**When:** {time_display}\n**Channel:** {channel_mention}",
            inline=False
        )
    
    if not embed.fields:
        await interaction.response.send_message("ℹ️ No maintenance timers found.", ephemeral=True)
        return
    
    MAX_EMBED_LENGTH = 5900
    if len(str(embed.to_dict())) > MAX_EMBED_LENGTH:
        await interaction.response.send_message("⚠️ Too many timers to display. Showing first few...")
        while len(str(embed.to_dict())) > MAX_EMBED_LENGTH and len(embed.fields) > 1:
            embed.remove_field(-1)
    
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="delete_timer", description="Permanently deletes a maintenance timer")
@app_commands.describe(name="The name of the timer to delete")
async def delete_timer(interaction: discord.Interaction, name: str):
    """Removes a maintenance timer permanently. Requires 'Manage Server' permission."""
    if not interaction.guild:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return
    
    # Check permissions
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("❌ You need 'Manage Server' permission to use this command.", ephemeral=True)
        return
    
    guild_id = interaction.guild.id
    
    if guild_id not in timers or name not in timers.get(guild_id, {}):
        await interaction.response.send_message(f"❌ Error: Timer '{name}' not found.", ephemeral=True)
        return
    
    try:
        del timers[guild_id][name]
        if not timers[guild_id]:
            del timers[guild_id]
        save_data()
        await interaction.response.send_message(f"🗑️ Timer '{name}' has been deleted.")
        logger.info(f"Timer '{name}' deleted in guild {guild_id} by {interaction.user.name}.")
    except Exception as e:
        await interaction.response.send_message("❌ An unexpected error occurred.", ephemeral=True)
        logger.error(f"Error deleting timer '{name}' in guild {guild_id}: {e}", exc_info=True)


@bot.tree.command(name="get_reminder_interval", description="Shows the global interval for pending reminders")
async def get_reminder_interval(interaction: discord.Interaction):
    """Displays the current global setting for how often pending tasks are re-notified."""
    current_interval = global_settings.get("reminder_repeat_days", "Not Set")
    await interaction.response.send_message(f"ℹ️ Pending tasks are reminded every **{current_interval}** days globally.")


@bot.tree.command(name="set_reminder_interval", description="Sets the global interval (days) for pending reminders")
@app_commands.describe(days="The number of days between reminders for pending tasks")
async def set_reminder_interval(interaction: discord.Interaction, days: int):
    """Sets the global reminder interval for pending tasks. Requires 'Manage Server' permission."""
    if not interaction.guild:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return
    
    # Check permissions
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("❌ You need 'Manage Server' permission to use this command.", ephemeral=True)
        return
    
    if days <= 0:
        await interaction.response.send_message("❌ Error: Interval must be positive.", ephemeral=True)
        return
    
    try:
        global_settings["reminder_repeat_days"] = days
        save_data()
        await interaction.response.send_message(f"✅ Global reminder interval set to **{days}** days.")
        logger.info(f"Global reminder interval changed to {days} days by {interaction.user.name} ({interaction.user.id}).")
    except Exception as e:
        await interaction.response.send_message("❌ Error saving interval.", ephemeral=True)
        logger.error(f"Error setting global reminder interval: {e}", exc_info=True)

@bot.tree.command(name="edit_timer", description="Edits an existing maintenance timer")
@app_commands.describe(
    name="The name of the timer to edit",
    interval_value="New interval value (e.g., 7, 2, 1)",
    interval_unit="New interval unit (Days/Weeks/Months)",
    owner="New owner (including @ will tag this person/group)",
    description="New task description"
)
@app_commands.choices(interval_unit=[
    app_commands.Choice(name="Days", value="days"),
    app_commands.Choice(name="Weeks", value="weeks"),
    app_commands.Choice(name="Months", value="months")
])
async def edit_timer(interaction: discord.Interaction, name: str, interval_value: int = None, 
                     interval_unit: str = None, owner: str = None, description: str = None):
    """Edits an existing maintenance timer."""
    if not interaction.guild:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return
        
    guild_id = interaction.guild.id
    
    # Check if the timer exists
    if guild_id not in timers or name not in timers[guild_id]:
        await interaction.response.send_message(f"❌ Error: Timer '{name}' not found.", ephemeral=True)
        return
    
    # Get the current timer data
    timer_data = timers[guild_id][name]
    
    # Track if any changes were made
    changes_made = False
    changes_list = []
    
    try:
        # Update interval_value if provided
        if interval_value is not None:
            if interval_value <= 0:
                await interaction.response.send_message("❌ Error: Interval value must be positive.", ephemeral=True)
                return
            timer_data["interval_value"] = interval_value
            changes_made = True
            changes_list.append(f"interval value to {interval_value}")
        
        # Update interval_unit if provided
        if interval_unit is not None:
            valid_units = ["days", "weeks", "months"]
            if interval_unit.lower() not in valid_units:
                await interaction.response.send_message(f"❌ Error: Interval unit must be one of: {', '.join(valid_units)}.", ephemeral=True)
                return
            timer_data["interval_unit"] = interval_unit.lower()
            changes_made = True
            changes_list.append(f"interval unit to {interval_unit.lower()}")
        
        # Update owner if provided
        if owner is not None:
            timer_data["owner"] = owner
            changes_made = True
            changes_list.append(f"owner to {owner}")
        
        # Update description if provided
        if description is not None:
            timer_data["description"] = description
            changes_made = True
            changes_list.append(f"description")
        
        # If interval was changed, update the next_due date unless it's pending
        if (interval_value is not None or interval_unit is not None) and not timer_data.get("is_pending", False):
            next_due_time = calculate_next_due(
                timer_data["interval_value"], 
                timer_data["interval_unit"],
                # Use the original due date as the starting point if available
                start_time=datetime.utcnow()
            )
            if not next_due_time:
                await interaction.response.send_message(f"❌ Error: Could not calculate next due date with unit '{timer_data['interval_unit']}'.", ephemeral=True)
                return
            
            timer_data["next_due"] = next_due_time
            changes_list.append(f"next due date to {discord_date(next_due_time)}")
        
        # Save changes if any were made
        if changes_made:
            save_data()
            await interaction.response.send_message(
                f"✅ Timer '{name}' updated successfully!\n"
                f"Changes: {', '.join(changes_list)}."
            )
            logger.info(f"Timer '{name}' edited in guild {guild_id} by {interaction.user.name}.")
        else:
            await interaction.response.send_message(f"ℹ️ No changes were made to timer '{name}'.", ephemeral=True)
            
    except ValueError as e:
        await interaction.response.send_message(f"❌ Error updating timer: {e}", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message("❌ An unexpected error occurred.", ephemeral=True)
        logger.error(f"Error editing timer '{name}' in guild {guild_id}: {e}", exc_info=True)

@bot.tree.command(name="help", description="Shows available commands and how to use them")
async def help_command(interaction: discord.Interaction):
    """Displays help information about all available timer commands."""
    
    embed = discord.Embed(
        title="Maintenance Timer Bot Help",
        description="This bot helps you track and manage recurring maintenance tasks.",
        color=discord.Color.blue()
    )
    
    # Timer Management
    embed.add_field(
        name="⏱️ Timer Management",
        value=(
            "**`/create_timer`** - Create a new maintenance timer\n"
            "**`/edit_timer`** - Edit an existing timer's properties\n"
            "**`/delete_timer`** - Delete a timer permanently (requires 'Manage Server')\n"
            "**`/done`** - Mark a timer as completed for this cycle\n"
        ),
        inline=False
    )
    
    # Viewing Timers
    embed.add_field(
        name="📋 Viewing Timers",
        value=(
            "**`/list_timers`** - Show all active timers and their status\n"
        ),
        inline=False
    )
    
    # Settings
    embed.add_field(
        name="⚙️ Settings",
        value=(
            "**`/get_reminder_interval`** - Show how often pending reminders repeat\n"
            "**`/set_reminder_interval`** - Change how often pending reminders repeat (requires 'Manage Server')\n"
            "**`/version`** - List current running version\n"
        ),
        inline=False
    )
    
    # How to use example
    embed.add_field(
        name="📝 Example Usage",
        value=(
            "1. Create a timer: `/create_timer name:server-backup interval_value:7 interval_unit:Days owner:@Admin description:Backup the server`\n"
            "2. Edit a timer: `/edit_timer name:server-backup interval_value:14 owner:@NewAdmin`\n"
            "3. Mark as done: `/done name:server-backup`\n"
        ),
        inline=False
    )
    
    embed.set_footer(text="All timers use UTC time zone.")
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="version", description="Shows the current bot version")
async def version_command(interaction: discord.Interaction):
    """Displays the current version of the bot."""
    embed = discord.Embed(
        title=f"Maintenance Timer Bot v{CURRENT_VERSION}",
        color=discord.Color.blue()
    )
    
    if RELEASE_NOTES:
        embed.add_field(name="What's New", value=RELEASE_NOTES, inline=False)
    
    embed.set_footer(text=f"Version {CURRENT_VERSION}")
    
    await interaction.response.send_message(embed=embed)

# --- Error Handling for App Commands ---
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    """Global error handler for application commands."""
    if isinstance(error, app_commands.CommandOnCooldown):
        await interaction.response.send_message(f"⏳ Command is on cooldown. Try again in {error.retry_after:.2f} seconds.", ephemeral=True)
        return
    
    if isinstance(error, app_commands.MissingPermissions):
        missing_perms = getattr(error, 'missing_perms', ['Unknown Permission'])
        await interaction.response.send_message(f"❌ You lack required permissions: `{', '.join(missing_perms)}`", ephemeral=True)
        return
        
    if isinstance(error, app_commands.BotMissingPermissions):
        missing_perms = getattr(error, 'missing_perms', ['Unknown Permission'])
        await interaction.response.send_message(f"❌ Bot lacks required permissions: `{', '.join(missing_perms)}`", ephemeral=True)
        return
    
    # Handle check failures
    if isinstance(error, app_commands.CheckFailure):
        await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
        return
    
    # Handle App Command errors that wrap other exceptions
    if isinstance(error, app_commands.CommandInvokeError):
        original = error.original
        logger.error(f"Command error: {str(original)}", exc_info=original)
        await interaction.response.send_message(f"❌ An error occurred while executing the command: {str(original)}", ephemeral=True)
        return
    
    # Log other errors
    logger.error(f"Unhandled command error: {str(error)}", exc_info=error)
    
    # Try to respond if we haven't already
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message("❌ An unexpected error occurred.", ephemeral=True)
    except Exception as e:
        logger.error(f"Failed to send error response: {str(e)}")


# --- Bot Startup ---
if __name__ == "__main__":
    # Validation for BOT_TOKEN already happened earlier
    try:
        # Create necessary directories if they don't exist
        data_dir = os.path.dirname(DATA_FILE)
        if not os.path.exists(data_dir):
            os.makedirs(data_dir)
            logger.info(f"Created data directory: {data_dir}")
            
        logger.info("Starting bot...")
        bot.run(BOT_TOKEN)
    except KeyboardInterrupt:
        logger.info("Bot shutdown requested.")
    except Exception as e:
        logger.critical(f"A critical error occurred: {e}", exc_info=True)