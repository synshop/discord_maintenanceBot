import discord
from discord import app_commands
from discord.ext import tasks
import json
import os
from datetime import datetime, timedelta, timezone
import logging
from dotenv import load_dotenv

# --- Load Environment Variables ---
load_dotenv()

# --- Configuration ---
BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
if not BOT_TOKEN:
    logging.critical("CRITICAL ERROR: DISCORD_BOT_TOKEN not found in .env file or environment variables.")
    exit("Bot token is missing. Please check your .env file.")

# Default reminder repeat days
DEFAULT_REMINDER_DAYS = 7
try:
    REMINDER_REPEAT_DAYS = int(os.getenv("REMINDER_REPEAT_DAYS", str(DEFAULT_REMINDER_DAYS)))
    if REMINDER_REPEAT_DAYS <= 0:
        REMINDER_REPEAT_DAYS = DEFAULT_REMINDER_DAYS
except ValueError:
    REMINDER_REPEAT_DAYS = DEFAULT_REMINDER_DAYS

# Default check interval
DEFAULT_CHECK_INTERVAL = 60
try:
    CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL_SECONDS", str(DEFAULT_CHECK_INTERVAL)))
    if CHECK_INTERVAL_SECONDS <= 0:
        CHECK_INTERVAL_SECONDS = DEFAULT_CHECK_INTERVAL
except ValueError:
    CHECK_INTERVAL_SECONDS = DEFAULT_CHECK_INTERVAL

# Data storage file path
DATA_FILE = "/bot/data/maintenancebot_data.json"

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s:%(levelname)s:%(name)s: %(message)s')
logger = logging.getLogger('discord')

# --- Global Data ---
global_settings = {"reminder_repeat_days": REMINDER_REPEAT_DAYS}
timers = {}

# --- Helper Functions ---
def datetime_serialize(obj):
    """Helper for datetime serialization when saving timers"""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")

def save_data():
    """Saves the current state of timers and global settings to the JSON file."""
    # Create serializable copy of timers
    serializable_timers = {}
    for guild_id, guild_timers in timers.items():
        serializable_timers[str(guild_id)] = {}
        for name, data in guild_timers.items():
            timer_copy = data.copy()
            # Remove unnecessary fields deprecated from old versions
            timer_copy.pop("reminder_repeat_days", None)
            serializable_timers[str(guild_id)][name] = timer_copy
    
    data_to_save = {
        "global_settings": global_settings,
        "timers": serializable_timers
    }
    
    # Ensure data directory exists
    data_dir = os.path.dirname(DATA_FILE)
    if not os.path.exists(data_dir):
        os.makedirs(data_dir)
    
    try:
        with open(DATA_FILE, 'w') as f:
            json.dump(data_to_save, f, indent=4, default=datetime_serialize)
        logger.info(f"Data saved to {DATA_FILE}")
    except Exception as e:
        logger.error(f"Error saving data: {e}")

def load_data():
    """Loads timers and global settings from the JSON file."""
    global timers, global_settings
    
    if not os.path.exists(DATA_FILE):
        timers = {}
        logger.warning(f"{DATA_FILE} not found. Using default settings.")
        return
    
    try:
        with open(DATA_FILE, 'r') as f:
            loaded_data = json.load(f)
        
        # Load global settings
        loaded_settings = loaded_data.get("global_settings", {})
        global_settings["reminder_repeat_days"] = loaded_settings.get(
            "reminder_repeat_days", global_settings["reminder_repeat_days"]
        )
        
        # Load timers with datetime parsing
        timers = {}
        loaded_timers = loaded_data.get("timers", {})
        
        for guild_id_str, guild_timers in loaded_timers.items():
            try:
                guild_id = int(guild_id_str)
                timers[guild_id] = {}
                
                for name, timer_data in guild_timers.items():
                    # Parse datetime fields
                    for dt_field in ["next_due", "last_reminded"]:
                        if timer_data.get(dt_field):
                            try:
                                timer_data[dt_field] = datetime.fromisoformat(timer_data[dt_field])
                            except ValueError:
                                timer_data[dt_field] = None
                    
                    timers[guild_id][name] = timer_data
                    
            except ValueError:
                logger.error(f"Invalid guild ID '{guild_id_str}' in data file. Skipping.")
                
        logger.info(f"Data loaded successfully from {DATA_FILE}")
        logger.info(f"Using global reminder interval: {global_settings['reminder_repeat_days']} days")
    except Exception as e:
        logger.error(f"Error loading data: {e}")
        timers = {}

def calculate_next_due(interval_value, interval_unit, start_time=None):
    """Calculate the next due date based on interval."""
    if start_time is None:
        start_time = datetime.now(timezone.utc)
    
    unit = interval_unit.lower()
    if unit == "days": 
        return start_time + timedelta(days=interval_value)
    elif unit == "weeks": 
        return start_time + timedelta(weeks=interval_value)
    elif unit == "months": 
        # Approximate months as 30 days
        logger.warning("Using approximation for 'months' (30 days).")
        return start_time + timedelta(days=interval_value * 30)
    else: 
        raise ValueError("Invalid interval unit. Use 'days', 'weeks', or 'months'.")

def strfdelta(tdelta, fmt):
    """Format a timedelta object to a string."""
    d = {
        "days": max(0, tdelta.days),
        "hours": max(0, tdelta.seconds // 3600),
        "minutes": max(0, (tdelta.seconds % 3600) // 60),
        "seconds": max(0, tdelta.seconds % 60)
    }
    
    try:
        return fmt.format(**d)
    except KeyError:
        return f"{d['days']}d {d['hours']}h {d['minutes']}m"

def create_timer_embed(timer_name, timer_data, status=None):
    """Create a standardized embed for timer notifications."""
    now = datetime.now(timezone.utc)
    
    if status == "due":
        return discord.Embed(
            title=f"🚨 Maintenance Due: {timer_name}",
            description=f"**Task:** {timer_data['description']}\n**Owner:** {timer_data['owner']}\n\nPlease complete the task and use `/done {timer_name}`",
            color=discord.Color.orange(),
            timestamp=now
        )
    elif status == "reminder":
        reminder_days = global_settings["reminder_repeat_days"]
        return discord.Embed(
            title=f"🔁 Maintenance Still Pending: {timer_name}",
            description=f"**Task:** {timer_data['description']}\n**Owner:** {timer_data['owner']}\n\nThis is a reminder (repeats every {reminder_days} days until done).\nPlease complete the task and use `/done {timer_name}`",
            color=discord.Color.red(),
            timestamp=now
        )
    else:
        return discord.Embed(
            title=f"Maintenance Timer: {timer_name}",
            description=f"**Task:** {timer_data['description']}\n**Owner:** {timer_data['owner']}",
            color=discord.Color.blue(),
            timestamp=now
        )

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
            await self.tree.sync()
            self.synced = True
            
        logger.info(f'Logged in as {self.user.name} ({self.user.id})')
        load_data() 
        check_timers_task.change_interval(seconds=CHECK_INTERVAL_SECONDS)
        check_timers_task.start()
        logger.info(f"Timer check interval: {CHECK_INTERVAL_SECONDS} seconds")
        logger.info('Bot is ready')

# Create bot instance
bot = MaintenanceBot()

# --- Background Task ---
@tasks.loop(seconds=DEFAULT_CHECK_INTERVAL)
async def check_timers_task():
    """Periodically checks all timers and sends reminders if due."""
    now = datetime.now(timezone.utc)
    data_changed = False
    reminder_days = global_settings["reminder_repeat_days"]
    
    # Use list copies to avoid modification during iteration
    for guild_id in list(timers.keys()):
        for name in list(timers.get(guild_id, {}).keys()):
            # Skip if timer was deleted during iterations
            if guild_id not in timers or name not in timers[guild_id]:
                continue
                
            timer_data = timers[guild_id][name]
            channel = bot.get_channel(timer_data.get("channel_id"))
            
            if not channel:
                logger.warning(f"Channel not found for timer '{name}' in guild {guild_id}")
                continue
                
            is_pending = timer_data.get("is_pending", False)
            next_due = timer_data.get("next_due")
            last_reminded = timer_data.get("last_reminded")
            
            try:
                # Check if timer is newly due
                if not is_pending and next_due and now >= next_due:
                    await channel.send(embed=create_timer_embed(name, timer_data, "due"))
                    timers[guild_id][name]["is_pending"] = True
                    timers[guild_id][name]["last_reminded"] = now
                    data_changed = True
                    logger.info(f"Timer '{name}' in guild {guild_id} is now due")
                
                # Check if reminder should be sent for pending timer
                elif is_pending and last_reminded:
                    next_reminder = last_reminded + timedelta(days=reminder_days)
                    if now >= next_reminder:
                        await channel.send(
                            f"cc: {timer_data['owner']}", 
                            embed=create_timer_embed(name, timer_data, "reminder")
                        )
                        timers[guild_id][name]["last_reminded"] = now
                        data_changed = True
                        logger.info(f"Sent reminder for pending timer '{name}' in guild {guild_id}")
            except discord.errors.Forbidden:
                logger.error(f"Lacking permissions to send message for timer '{name}'")
            except Exception as e:
                logger.error(f"Error processing timer '{name}': {e}")
    
    if data_changed:
        save_data()

@check_timers_task.before_loop
async def before_check_timers():
    await bot.wait_until_ready()
    logger.info("Bot ready, starting timer check loop")

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
    
    # Basic validation
    if interval_value <= 0:
        await interaction.response.send_message("❌ Error: Interval value must be positive.", ephemeral=True)
        return
        
    if interval_unit.lower() not in ["days", "weeks", "months"]:
        await interaction.response.send_message("❌ Error: Interval unit must be days, weeks, or months.", ephemeral=True)
        return
        
    # Initialize guild timers if needed
    if guild_id not in timers:
        timers[guild_id] = {}
        
    # Check for duplicates
    if name in timers[guild_id]:
        await interaction.response.send_message(f"❌ Error: A timer with name '{name}' already exists.", ephemeral=True)
        return
        
    try:
        """ Calculate the first due date"""
        next_due = calculate_next_due(interval_value, interval_unit)
        
        """" Create the timer."""
        timers[guild_id][name] = {
            "name": name,
            "interval_value": interval_value,
            "interval_unit": interval_unit.lower(),
            "description": description,
            "owner": owner,
            "channel_id": interaction.channel.id,
            "next_due": next_due,
            "is_pending": False,
            "last_reminded": None
        }
        
        save_data()
        
        await interaction.response.send_message(
            f"✅ Timer '{name}' created successfully!\n"
            f"It will first trigger on {next_due.strftime('%Y-%m-%d %H:%M:%S UTC')}.\n"
            f"Pending reminders will repeat every {global_settings['reminder_repeat_days']} days (global setting)."
        )
        logger.info(f"Timer '{name}' created in guild {guild_id} by {interaction.user.name}")
        
    except Exception as e:
        await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)
        logger.error(f"Error creating timer '{name}': {e}")


@bot.tree.command(name="done", description="Marks a pending maintenance timer as completed")
@app_commands.describe(name="The name of the timer to mark as done")
async def done_timer(interaction: discord.Interaction, name: str):
    """Marks a specific maintenance task as done for its current cycle."""
    if not interaction.guild:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return
        
    guild_id = interaction.guild.id
    
    # Verify timer exists
    if guild_id not in timers or name not in timers[guild_id]:
        await interaction.response.send_message(f"❌ Error: Timer '{name}' not found.", ephemeral=True)
        return
        
    timer_data = timers[guild_id][name]
    
    # Check if timer is pending
    if not timer_data.get("is_pending", False):
        await interaction.response.send_message(f"ℹ️ Timer '{name}' was not pending completion.", ephemeral=True)
        return
        
    try:
        # Calculate next due date based on interval
        next_due = calculate_next_due(timer_data["interval_value"], timer_data["interval_unit"])
        
        # Reset timer
        timers[guild_id][name]["is_pending"] = False
        timers[guild_id][name]["last_reminded"] = None
        timers[guild_id][name]["next_due"] = next_due
        save_data()
        
        await interaction.response.send_message(
            f"✅ Timer '{name}' marked as done by {interaction.user.mention}!\n"
            f"It will trigger again around {next_due.strftime('%Y-%m-%d %H:%M:%S UTC')}."
        )
        logger.info(f"Timer '{name}' marked done in guild {guild_id} by {interaction.user.name}")
        
    except Exception as e:
        await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)
        logger.error(f"Error marking timer done: {e}")


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
    
    now = datetime.now(timezone.utc)
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
            time_str = next_due_dt.strftime('%Y-%m-%d %H:%M UTC')
            if is_pending:
                due_since = now - (next_due_dt if next_due_dt <= now else now)
                due_since_str = strfdelta(due_since, "{days}d {hours}h {minutes}m ago")
                time_display = f"Originally due: {time_str} ({due_since_str})"
            else:
                time_delta = next_due_dt - now
                time_display = f"Next due: {time_str} (in {strfdelta(time_delta, '{days}d {hours}h {minutes}m')})" if time_delta.total_seconds() > 0 else f"Due: {time_str} (Overdue!)"
        else:
            time_display = "Next due date not set."
        
        embed.add_field(
            name=f"{status_emoji} - {name}",
            value=f"**Owner:** {owner}\n**Task:** {description}\n**When:** {time_display}\n**Channel:** {channel_mention}",
            inline=False
        )
    
    # Handle oversized embed
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
    
    """Check for 'Manage Server' permissions"""
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("❌ You need 'Manage Server' permission to use this command.", ephemeral=True)
        return
    
    guild_id = interaction.guild.id
    
    """Validate if timer exists"""
    if guild_id not in timers or name not in timers[guild_id]:
        await interaction.response.send_message(f"❌ Error: Timer '{name}' not found.", ephemeral=True)
        return
    
    """Delete timer."""
    del timers[guild_id][name]
    if not timers[guild_id]:
        del timers[guild_id]
    save_data()
    
    await interaction.response.send_message(f"🗑️ Timer '{name}' has been deleted.")
    logger.info(f"Timer '{name}' deleted in guild {guild_id} by {interaction.user.name}")


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
    
    """ Checks the permissions."""
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("❌ You need 'Manage Server' permission to use this command.", ephemeral=True)
        return
    
    if days <= 0:
        await interaction.response.send_message("❌ Error: Interval must be positive.", ephemeral=True)
        return
    
    global_settings["reminder_repeat_days"] = days
    save_data()
    await interaction.response.send_message(f"✅ Global reminder interval set to **{days}** days.")
    logger.info(f"Global reminder interval changed to {days} days by {interaction.user.name}")


# --- Error Handling for App Commands ---
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    """Global error handler for application commands."""
    try:
        # Handle specific error types
        if isinstance(error, app_commands.CommandOnCooldown):
            await interaction.response.send_message(f"⏳ Command is on cooldown. Try again in {error.retry_after:.2f} seconds.", ephemeral=True)
        elif isinstance(error, app_commands.MissingPermissions):
            perms = getattr(error, 'missing_perms', ['Unknown Permission'])
            await interaction.response.send_message(f"❌ You lack required permissions: `{', '.join(perms)}`", ephemeral=True)
        elif isinstance(error, app_commands.BotMissingPermissions):
            perms = getattr(error, 'missing_perms', ['Unknown Permission'])
            await interaction.response.send_message(f"❌ Bot lacks required permissions: `{', '.join(perms)}`", ephemeral=True)
        elif isinstance(error, app_commands.CheckFailure):
            await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
        elif isinstance(error, app_commands.CommandInvokeError):
            original = error.original
            logger.error(f"Command error: {original}", exc_info=original)
            await interaction.response.send_message(f"❌ Error executing command: {original}", ephemeral=True)
        else:
            logger.error(f"Unhandled command error: {error}", exc_info=error)
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ An unexpected error occurred.", ephemeral=True)
    except Exception as e:
        logger.error(f"Failed to send error response: {e}")


# --- Bot Startup ---
if __name__ == "__main__":
    try:
        """ Creates the data directory if missing"""
        data_dir = os.path.dirname(DATA_FILE)
        if not os.path.exists(data_dir):
            os.makedirs(data_dir)
            
        """Start bot or stop/log exception as needed."""
        logger.info("Starting bot...")
        bot.run(BOT_TOKEN)
    except KeyboardInterrupt:
        logger.info("Bot shutdown requested.")
    except Exception as e:
        logger.critical(f"A critical error occurred: {e}", exc_info=True)