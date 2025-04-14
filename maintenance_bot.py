import discord
from discord.ext import commands, tasks
import json
import os
from datetime import datetime, timedelta
import asyncio
import logging
from dotenv import load_dotenv # Import dotenv

# --- Load Environment Variables ---
load_dotenv() # Load variables from .env file into environment

# --- Configuration from Environment Variables (with defaults and validation) ---
BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")

# Default reminder repeat days (used if not in .env or bot_data.json)
DEFAULT_REMINDER_DAYS = 7
try:
    # Get from .env, fallback to string default, convert to int
    REMINDER_REPEAT_DAYS_DEFAULT_ENV = int(os.getenv("REMINDER_REPEAT_DAYS", str(DEFAULT_REMINDER_DAYS)))
    if REMINDER_REPEAT_DAYS_DEFAULT_ENV <= 0:
         logging.warning(f"REMINDER_REPEAT_DAYS in .env must be positive. Using default: {DEFAULT_REMINDER_DAYS}")
         REMINDER_REPEAT_DAYS_DEFAULT_ENV = DEFAULT_REMINDER_DAYS
except ValueError:
    logging.warning(f"Invalid REMINDER_REPEAT_DAYS in .env. Must be an integer. Using default: {DEFAULT_REMINDER_DAYS}")
    REMINDER_REPEAT_DAYS_DEFAULT_ENV = DEFAULT_REMINDER_DAYS

# Default check interval (used if not in .env)
DEFAULT_CHECK_INTERVAL = 60
try:
     CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL_SECONDS", str(DEFAULT_CHECK_INTERVAL)))
     if CHECK_INTERVAL_SECONDS <= 0:
         logging.warning(f"CHECK_INTERVAL_SECONDS in .env must be positive. Using default: {DEFAULT_CHECK_INTERVAL}")
         CHECK_INTERVAL_SECONDS = DEFAULT_CHECK_INTERVAL
except ValueError:
    logging.warning(f"Invalid CHECK_INTERVAL_SECONDS in .env. Must be an integer. Using default: {DEFAULT_CHECK_INTERVAL}")
    CHECK_INTERVAL_SECONDS = DEFAULT_CHECK_INTERVAL


COMMAND_PREFIX = "!" # Keep prefix here or move to .env if desired
DATA_FILE = "bot_data.json"

# --- Validate Essential Config ---
if not BOT_TOKEN:
    logging.critical("CRITICAL ERROR: DISCORD_BOT_TOKEN not found in .env file or environment variables.")
    # Optionally raise an exception or exit
    exit("Bot token is missing. Please check your .env file.") # Exit script if token is missing

# --- Global Settings (initialized with default from .env) ---
# This dictionary will be updated from bot_data.json by load_data()
global_settings = {
    # Use the value loaded from .env as the initial default
    "reminder_repeat_days": REMINDER_REPEAT_DAYS_DEFAULT_ENV
}

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s:%(levelname)s:%(name)s: %(message)s')
logger = logging.getLogger('discord')

# --- Custom Help Command (MyHelpCommand class) ---
# ... (Keep the MyHelpCommand class exactly as it was) ...
class MyHelpCommand(commands.MinimalHelpCommand):
    """Custom help command to show parameters."""

    async def send_command_help(self, command):
        """Sends help for a specific command, including usage."""
        if not command.enabled:
            return # Don't show help for disabled commands

        ctx = self.context
        embed = discord.Embed(
            title=f"Help: {self.context.clean_prefix}{command.qualified_name}",
            description=command.help or "No description provided.", # Uses the 'help' string from decorator
            color=discord.Color.blurple() # Or any color you like
        )

        # --- Show Usage with Parameters (Signature) ---
        signature = f"{self.context.clean_prefix}{command.qualified_name} {command.signature}"
        embed.add_field(name="Usage", value=f"```\n{signature}\n```", inline=False)

        # --- Show Aliases ---
        if command.aliases:
            embed.add_field(name="Aliases", value=", ".join(command.aliases), inline=False)

        # --- Optional: Add detailed description from docstring ---
        # if command.description:
        #     embed.add_field(name="Details", value=command.description, inline=False)

        # --- Show Required Permissions ---
        perms = []
        for check in command.checks:
            check_name = getattr(check, "__qualname__", str(check))
            if 'has_permissions' in check_name:
                 if command.name in ["delete_timer", "set_reminder_interval"]:
                      perms.append("Manage Server")
        if perms:
             embed.add_field(name="Permissions Required", value=", ".join(perms), inline=False)

        await ctx.send(embed=embed)

    async def send_bot_help(self, mapping):
        """Sends general help listing commands."""
        ctx = self.context
        embed = discord.Embed(title="Maintenance Bot Help", color=discord.Color.green())
        description = self.context.bot.description # Can set bot description during init
        if description:
            embed.description = description

        usable_commands = []
        for cog, cmds in mapping.items():
            filtered = await self.filter_commands(cmds, sort=True)
            if filtered:
                 command_signatures = [f"`{self.context.clean_prefix}{c.name}` - {c.brief or c.help or 'No description'}" for c in filtered]
                 if command_signatures:
                    cog_name = getattr(cog, "qualified_name", "General Commands")
                    embed.add_field(name=cog_name, value="\n".join(command_signatures), inline=False)

        if not embed.fields:
             embed.description = (embed.description or "") + "\nNo runnable commands found."

        embed.set_footer(text=f"Type {self.context.clean_prefix}help <command_name> for more info on a command.")
        await ctx.send(embed=embed)

# --- Bot Setup ---
intents = discord.Intents.default()
intents.guilds = True
intents.messages = True
intents.message_content = True

bot = commands.Bot(
    command_prefix=COMMAND_PREFIX,
    intents=intents,
    help_command=MyHelpCommand(),
    description="A bot for managing maintenance task reminders."
)

# --- Global Timers Dictionary ---
timers = {}

# --- Helper Functions (save_data, load_data, calculate_next_due, strfdelta) ---
# load_data needs a slight adjustment to use the .env default if JSON is missing the key

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
            # Use the *current* global_settings dict which might have been updated by !set_reminder_interval
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
        # Initial global_settings already set using .env default
        logger.warning(f"{DATA_FILE} not found. Using default global settings from .env/code.")
        return

    try:
        with open(DATA_FILE, 'r') as f:
            loaded_data = json.load(f)

        # Load global settings from JSON, use .env value as the fallback if key is missing in JSON
        loaded_settings = loaded_data.get("global_settings", {})
        # Note: global_settings dict was already initialized using the .env default
        # This line updates it from the JSON file if the key exists there.
        global_settings["reminder_repeat_days"] = loaded_settings.get(
            "reminder_repeat_days", global_settings["reminder_repeat_days"] # Fallback to existing value (from .env)
        )
        # Add more global settings here in the future if needed

        # Load timers (same logic as before)
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
        timers = {} # Reset timers
        # Keep global_settings as initialized from .env defaults
    except Exception as e:
        logger.error(f"Unexpected error during data deserialization: {e}")
        timers = {}
        # Keep global_settings as initialized from .env defaults

# ... (calculate_next_due and strfdelta functions remain unchanged) ...
def calculate_next_due(interval_value, interval_unit, start_time=None):
    if start_time is None:
        start_time = datetime.utcnow()
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


# --- Bot Events ---
@bot.event
async def on_ready():
    """Called when the bot is ready and connected."""
    logger.info(f'Logged in as {bot.user.name} ({bot.user.id})')
    logger.info('Loading data...')
    load_data() # Load persisted data (which might override .env defaults for settings)
    logger.info('Starting timer check loop...')
    # Use the CHECK_INTERVAL_SECONDS loaded from .env (or default)
    # We need to change the loop's interval *before* starting it if it differs from the default
    check_timers_task.change_interval(seconds=CHECK_INTERVAL_SECONDS)
    check_timers_task.start()
    logger.info(f"Timer check interval: {CHECK_INTERVAL_SECONDS} seconds.")
    logger.info('Bot is ready.')

# --- Background Task ---
# Define the task with a default interval, which will be adjusted in on_ready
@tasks.loop(seconds=DEFAULT_CHECK_INTERVAL) # Start with default, will be changed in on_ready
async def check_timers_task():
    """Periodically checks all timers and sends reminders if due."""
    global timers, global_settings
    now = datetime.utcnow()
    data_to_save = False

    # Use the global setting (potentially loaded from JSON, with .env as initial default)
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
                    embed = discord.Embed( title=f"🚨 Maintenance Due: {name}", description=f"**Task:** {timer_data['description']}\n**Owner:** {timer_data['owner']}\n\nPlease complete the task and type `{COMMAND_PREFIX}done {name}`", color=discord.Color.orange(), timestamp=now )
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
                        embed = discord.Embed( title=f"🔁 Maintenance Still Pending: {name}", description=f"**Task:** {timer_data['description']}\n**Owner:** {timer_data['owner']}\n\nThis is a reminder (repeats every {reminder_interval_days} days until done).\nPlease complete the task and type `{COMMAND_PREFIX}done {name}`", color=discord.Color.red(), timestamp=now )
                        await channel.send(f"cc: {timer_data['owner']}", embed=embed)
                        timers[guild_id][name]["last_reminded"] = now
                        data_to_save = True
                        logger.info(f"Sent repeat reminder for '{name}' in guild {guild_id}, channel {channel.id}.")

            except discord.errors.Forbidden: logger.error(f"Bot lacks permissions to send message in channel {timer_channel_id} for timer '{name}', guild {guild_id}.")
            except Exception as e: logger.error(f"Error processing timer '{name}' in guild {guild_id}: {e}", exc_info=True)

    if data_to_save:
        save_data()

@check_timers_task.before_loop
async def before_check_timers():
    await bot.wait_until_ready()
    logger.info("Bot is ready, preparing background timer check loop.")


# --- Bot Commands (create_timer, done, list_timers, delete_timer, get_reminder_interval, set_reminder_interval) ---
# ... (Keep the command definitions exactly as they were in the previous version) ...
@bot.command(name="create_timer", help="Creates a new maintenance timer.")
@commands.guild_only()
async def create_timer(ctx, name: str, interval_value: int, interval_unit: str, owner: str, *, description: str):
    """
    Creates a recurring maintenance task reminder.

    Arguments:
    <name>: A unique name for this timer (no spaces).
    <interval_value>: How often the task repeats (e.g., 7, 2, 1).
    <interval_unit>: The unit for the interval ('days', 'weeks', 'months').
    <owner>: The primary person/role responsible.
    <description>: What needs to be done (can include spaces).
    """
    guild_id = ctx.guild.id; channel_id = ctx.channel.id; valid_units = ["days", "weeks", "months"]
    if interval_value <= 0: await ctx.send("❌ Error: Interval value must be positive."); return
    if interval_unit.lower() not in valid_units: await ctx.send(f"❌ Error: Interval unit must be one of: {', '.join(valid_units)}."); return
    if guild_id not in timers: timers[guild_id] = {}
    if name in timers[guild_id]: await ctx.send(f"❌ Error: A timer with the name '{name}' already exists in this server."); return
    try:
        next_due_time = calculate_next_due(interval_value, interval_unit)
        if not next_due_time: await ctx.send(f"❌ Error: Could not calculate next due date with unit '{interval_unit}'."); return
        new_timer = { "name": name, "interval_value": interval_value, "interval_unit": interval_unit.lower(), "description": description, "owner": owner, "channel_id": channel_id, "next_due": next_due_time, "is_pending": False, "last_reminded": None }
        timers[guild_id][name] = new_timer; save_data()
        await ctx.send(f"✅ Timer '{name}' created successfully!\nIt will first trigger on {next_due_time.strftime('%Y-%m-%d %H:%M:%S UTC')}.\nPending reminders will repeat every {global_settings['reminder_repeat_days']} days (global setting).")
        logger.info(f"Timer '{name}' created in guild {guild_id} by {ctx.author.name}.")
    except ValueError as e: await ctx.send(f"❌ Error: {e}")
    except Exception as e: await ctx.send("❌ An unexpected error occurred."); logger.error(f"Error creating timer '{name}' in guild {guild_id}: {e}", exc_info=True)

@bot.command(name="done", help="Marks a pending maintenance timer as completed.")
@commands.guild_only()
async def done_timer(ctx, name: str):
    """
    Marks a specific maintenance task as done for its current cycle.
    This resets the timer for its next interval.

    Arguments:
    <name>: The name of the timer to mark as done.
    """
    guild_id = ctx.guild.id
    if guild_id not in timers or name not in timers[guild_id]: await ctx.send(f"❌ Error: Timer '{name}' not found."); return
    timer_data = timers[guild_id][name]
    if not timer_data.get("is_pending", False): await ctx.send(f"ℹ️ Timer '{name}' was not pending completion."); return
    try:
        interval_value = timer_data["interval_value"]; interval_unit = timer_data["interval_unit"]
        next_due_time = calculate_next_due(interval_value, interval_unit)
        if not next_due_time: await ctx.send(f"❌ Error: Could not recalculate next due date for timer '{name}'."); return
        timers[guild_id][name]["is_pending"] = False; timers[guild_id][name]["last_reminded"] = None; timers[guild_id][name]["next_due"] = next_due_time
        save_data()
        await ctx.send(f"✅ Timer '{name}' marked as done by {ctx.author.mention}!\nIt will trigger again around {next_due_time.strftime('%Y-%m-%d %H:%M:%S UTC')}.")
        logger.info(f"Timer '{name}' marked done in guild {guild_id} by {ctx.author.name}.")
    except ValueError as e: await ctx.send(f"❌ Error resetting timer: {e}")
    except Exception as e: await ctx.send("❌ An unexpected error occurred."); logger.error(f"Error marking timer '{name}' done in guild {guild_id}: {e}", exc_info=True)

@bot.command(name="list_timers", help="Lists all active maintenance timers.")
@commands.guild_only()
async def list_timers_cmd(ctx):
    """Displays the status of all configured maintenance timers for this server."""
    guild_id = ctx.guild.id
    if guild_id not in timers or not timers[guild_id]: await ctx.send("ℹ️ No maintenance timers are set up."); return
    embed = discord.Embed(title=f"Maintenance Timers for {ctx.guild.name}", color=discord.Color.blue())
    embed.set_footer(text=f"Global pending reminder interval: {global_settings['reminder_repeat_days']} days")
    now = datetime.utcnow(); sorted_timers = sorted(timers[guild_id].values(), key=lambda t: t.get('next_due') or datetime.max.replace(tzinfo=None))
    for timer_data in sorted_timers:
        name = timer_data['name']; next_due_dt = timer_data.get('next_due'); is_pending = timer_data.get('is_pending', False); owner = timer_data.get('owner', 'N/A'); description = timer_data.get('description', 'N/A'); channel = bot.get_channel(timer_data.get("channel_id", 0)); channel_mention = channel.mention if channel else f"ID: {timer_data.get('channel_id', 'Unknown')}"
        status_emoji = "🚨 PENDING" if is_pending else "⏳ Active"
        if next_due_dt:
            time_str = next_due_dt.strftime('%Y-%m-%d %H:%M UTC')
            if is_pending: due_since = now - (next_due_dt if next_due_dt <= now else now); due_since_str = strfdelta(due_since, "{days}d {hours}h {minutes}m ago"); time_display = f"Originally due: {time_str} ({due_since_str})"
            else: time_delta = next_due_dt - now; time_display = f"Next due: {time_str} (in {strfdelta(time_delta, '{days}d {hours}h {minutes}m')})" if time_delta.total_seconds() > 0 else f"Due: {time_str} (Overdue!)"
        else: time_display = "Next due date not set."
        embed.add_field( name=f"{status_emoji} - {name}", value=f"**Owner:** {owner}\n**Task:** {description}\n**When:** {time_display}\n**Channel:** {channel_mention}", inline=False )
    if not embed.fields: await ctx.send("ℹ️ No maintenance timers found."); return
    MAX_EMBED_LENGTH = 5900
    if len(embed) > MAX_EMBED_LENGTH:
         await ctx.send("⚠️ Too many timers to display. Showing first few...")
         while len(embed) > MAX_EMBED_LENGTH and len(embed.fields) > 1: embed.remove_field(-1)
    await ctx.send(embed=embed)

@bot.command(name="delete_timer", help="Permanently deletes a maintenance timer.")
@commands.guild_only()
@commands.has_permissions(manage_guild=True) # This is around line 417
async def delete_timer(ctx, name: str):
    """
    Removes a maintenance timer permanently. Requires 'Manage Server' permission.

    Arguments:
    <name>: The name of the timer to delete.
    """
    guild_id = ctx.guild.id
    if guild_id not in timers or name not in timers.get(guild_id, {}):
        await ctx.send(f"❌ Error: Timer '{name}' not found.")
        return
    try:
        del timers[guild_id][name]
        if not timers[guild_id]:
            del timers[guild_id]
        save_data()
        await ctx.send(f"🗑️ Timer '{name}' has been deleted.")
        logger.info(f"Timer '{name}' deleted in guild {guild_id} by {ctx.author.name}.")
    except Exception as e:
        await ctx.send("❌ An unexpected error occurred.")
        logger.error(f"Error deleting timer '{name}' in guild {guild_id}: {e}", exc_info=True)

# This decorator applies to the function BELOW it. Ensure syntax is correct.
@delete_timer.error
async def delete_timer_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        pass # Handled globally by on_command_error
    else:
        logger.error(f"Error in delete_timer command: {error}")
        await ctx.send("❌ Internal error.")

@bot.command(name="get_reminder_interval", help="Shows the global interval for pending reminders.")
async def get_reminder_interval(ctx):
    """Displays the current global setting for how often pending tasks are re-notified."""
    current_interval = global_settings.get("reminder_repeat_days", "Not Set")
    await ctx.send(f"ℹ️ Pending tasks are reminded every **{current_interval}** days globally.")

@bot.command(name="set_reminder_interval", help="Sets the global interval (days) for pending reminders.")
@commands.has_permissions(manage_guild=True)
async def set_reminder_interval(ctx, days: int):
    """
    Sets the global reminder interval for pending tasks. Requires 'Manage Server' permission.

    Arguments:
    <days>: The number of days between reminders for pending tasks.
    """
    if days <= 0: await ctx.send("❌ Error: Interval must be positive."); return
    try:
        global_settings["reminder_repeat_days"] = days; save_data()
        await ctx.send(f"✅ Global reminder interval set to **{days}** days.")
        logger.info(f"Global reminder interval changed to {days} days by {ctx.author.name} ({ctx.author.id}).")
    except Exception as e: await ctx.send("❌ Error saving interval."); logger.error(f"Error setting global reminder interval: {e}", exc_info=True)


# --- Error Handling (Specific Handlers and Generic Handler) ---
# ... (Keep the error handlers exactly as they were in the previous version) ...
@delete_timer.error
async def delete_timer_error(ctx, error):
    if isinstance(error, commands.MissingPermissions): pass # Handled globally
    else: logger.error(f"Error in delete_timer command: {error}"); await ctx.send("❌ Internal error.")

@set_reminder_interval.error
async def set_reminder_interval_error(ctx, error):
    if isinstance(error, commands.MissingPermissions): pass # Handled globally
    elif isinstance(error, commands.BadArgument): await ctx.send(f"❌ Invalid argument. Please provide a whole number of days.")
    else: logger.error(f"Error in set_reminder_interval command: {error}"); await ctx.send("❌ Internal error.")

@bot.event
async def on_command_error(ctx, error):
    """Handles errors globally."""
    if hasattr(ctx.command, 'on_error'): return # Command has specific handler

    if isinstance(error, commands.CommandNotFound): pass # Ignore
    elif isinstance(error, commands.MissingRequiredArgument): await ctx.send(f"❌ Missing argument: `{error.param.name}`."); await ctx.send_help(ctx.command)
    elif isinstance(error, commands.BadArgument): await ctx.send(f"❌ Invalid argument type."); await ctx.send_help(ctx.command)
    elif isinstance(error, commands.NoPrivateMessage): await ctx.send("❌ Command unavailable in DMs.")
    elif isinstance(error, commands.MissingPermissions): missing_perms = getattr(error, 'missing_perms', ['Unknown Permission']); await ctx.send(f"❌ You lack required permissions: `{', '.join(missing_perms)}`")
    elif isinstance(error, commands.CommandInvokeError): logger.error(f"Error invoking {ctx.command.name}: {error.original}", exc_info=error.original); await ctx.send(f"❌ Internal error running `{ctx.command.name}`.")
    else: logger.error(f"Unhandled error in {ctx.command}: {error}", exc_info=True); await ctx.send("❌ Unexpected error.")


# --- Run the Bot ---
if __name__ == "__main__":
    # Validation for BOT_TOKEN already happened earlier
    try:
        async def main():
            async with bot:
                await bot.start(BOT_TOKEN) # Use the token loaded from .env

        asyncio.run(main())

    # No need for LoginFailure check here if BOT_TOKEN validation passed
    except KeyboardInterrupt:
         logger.info("Bot shutdown requested.")
    except Exception as e: # Catch other potential issues during startup/runtime
         logger.critical(f"An critical error occurred: {e}", exc_info=True)