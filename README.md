# Discord Maintenance Timer Bot

A simple Discord bot written in Python, designed to manage recurring maintenance tasks or reminders within a Discord server for a SYN Shop. It allows discord users to create multiple timers with custom intervals, descriptions, and owners. When a task is due, the bot posts a reminder in the designated channel and continues to re-notify at a configurable interval until the task is marked as complete. Any user can add a timer, but only an admin (in our case board members) can delete one.

## Features

*   **Create Unlimited Timers:** Set up as many maintenance reminders as needed.
*   **Custom Intervals:** Define timers based on days, weeks, or months.
*   **Recurring Reminders:** Get notified when a task is due.
*   **Persistent Pending Notifications:** If a task isn't marked done, the bot re-notifies periodically (using a global setting) until completion.
*   **Task Completion Tracking:** Use a simple command (`!done`) to mark tasks complete and automatically reset their timer.
*   **List & Manage:** View all active timers and delete unnecessary ones.
*   **`.env` Configuration:** Securely store the bot token and settings outside the code.
*   **Data Persistence:** Timer and settings data are saved in `maintenancebot_data.json` to survive bot restarts.
*   **Detailed Help Command:** Get specific usage instructions for each command, including parameters.

## Prerequisites

Before you begin, ensure you have the following:

1.  **Python:** Version 3.8 or newer installed.
2.  **Discord.py Library:** `pip install -U discord.py`
3.  **Python-DotEnv Library:** `pip install python-dotenv`
4.  **Python Date Utility:** `pip install python-dateutil`
5.  **Discord Bot Application:**
    *   Create one on the [Discord Developer Portal](https://discord.com/developers/applications).
    *   Create a "Bot" user for your application.
    *   **Copy the Bot Token.** You will need this for the configuration.
    *   **Enable Privileged Gateway Intents:** Under the "Bot" section of your application on the Developer Portal, enable:
        *   `SERVER MEMBERS INTENT`
        *   `MESSAGE CONTENT INTENT`

## Setup Instructions

1.  **Clone or Download:**
    *   Clone this repository: `git clone <repository_url>`
    *   Or download the `maintenance_bot.py` file.

2.  **Install Dependencies:**
    Navigate to the bot's directory in your terminal and install the required Python libraries:
    ```bash
    pip install -U discord.py python-dotenv python-dateutil
    ```
    *(Alternatively, if a `requirements.txt` file is provided: `pip install -r requirements.txt`)*

3.  **Configure the Bot:**
    *   In the same directory as the Python script, create a file named `.env`.
    *   Add the following content to the `.env` file, replacing the placeholder values:

    ```dotenv
    # --- Configuration ---

    # REQUIRED: Your Discord Bot Token from the Discord Developer Portal
    DISCORD_BOT_TOKEN="YOUR_ACTUAL_BOT_TOKEN_HERE"

    # OPTIONAL: Default global reminder interval in days for pending tasks.
    # This is used initially and if the setting is missing from bot_data.json.
    # Can be changed later using the !set_reminder_interval command.
    # Must be a positive whole number.
    REMINDER_REPEAT_DAYS="3"

    # OPTIONAL: How often the bot checks timers, in seconds.
    # Must be a positive whole number. Lower values mean faster checks but more activity.
    CHECK_INTERVAL_SECONDS="14400"
    ```

    *   **IMPORTANT:** If you are using Git, add `.env` to your `.gitignore` file to avoid accidentally committing your bot token!

4.  **Invite the Bot to Your Server:**
    *   Go back to your bot application on the Discord Developer Portal.
    *   Navigate to "OAuth2" -> "URL Generator".
    *   Select the following scopes:
        *   `bot`
        *   `applications.commands` (optional, good practice for future slash commands)
    *   Select the following Bot Permissions:
        *   `View Channels`
        *   `Send Messages`
        *   `Send Messages in Threads`
        *   `Embed Links`
        *   `Read Message History`
        *   `Mention @everyone` (Needed for reminder pings if owners are roles/users)
    *   Copy the generated URL and paste it into your browser.
    *   Select the server you want to add the bot to and authorize it.

## Running the Bot

1.  Open your terminal or command prompt.
2.  Navigate to the directory containing the `maintenance_bot.py` script and your `.env` file.
3.  Run the bot using Python:
    ```bash
    python maintenance_bot.py
    ```
4.  The bot should log into Discord and print confirmation messages in your terminal. If you see errors, check your `.env` file (especially the token) and ensure you've installed the prerequisites.
5. It is suggested to add the bot to a dedicated discord channel, and perform commands and monitor it's announcements there.

## Usage / Commands

Interact with the bot using commands in your Discord server channels. The default prefix is `!`.

*   **`!help`**
    Displays a list of available commands.

*   **`!help <command_name>`**
    Shows detailed help for a specific command, including its required parameters and usage format.
    *Example:* `!help create_timer`

*   **`!create_timer <name> <interval_value> <days|weeks|months> <owner> <description>`**
    Creates a new recurring maintenance timer.
    *   `<name>`: A unique, single-word name for the timer.
    *   `<interval_value>`: A number (e.g., `7`, `2`, `1`).
    *   `<days|weeks|months>`: The unit for the interval.
    *   `<owner>`: The user primarily responsible (can be used for pings).
    *   `<description>`: A description of the task (can contain spaces).
    *Example:* `!create_timer WeeklyBackup 7 days @AdminTeam Perform weekly server backup`

*   **`!done <name>`**
    Marks a currently pending timer as completed for this cycle. This resets the timer countdown.
    *   `<name>`: The name of the timer to mark as done.
    *Example:* `!done WeeklyBackup`

*   **`!list_timers`**
    Displays all currently active timers for the server, showing their status, next due date, owner, and description.

*   **`!delete_timer <name>`**
    Permanently deletes a timer.
    *Requires `Manage Server` permission for the user.*
    *   `<name>`: The name of the timer to delete.
    *Example:* `!delete_timer OldTask`

*   **`!get_reminder_interval`**
    Shows the current global setting for how many days the bot waits before re-notifying about a pending task.

*   **`!set_reminder_interval <days>`**
    Sets the global reminder interval for pending tasks.
    *Requires `Manage Server` permission for the user.*
    *   `<days>`: A positive whole number of days.
    *Example:* `!set_reminder_interval 3`

## Data Persistence

*   The bot stores all timer configurations and the global reminder interval setting in a file named `bot_data.json` in the same directory as the script.
*   This file is created automatically when the first timer is created or the global setting is changed.
*   **Do not manually edit this file unless you know what you are doing, as corruption could lead to data loss.**
*   It's recommended to occasionally back up this file.

## Permissions

*   **Bot Permissions (Server Invite):** Ensure the bot has the necessary permissions listed in the "Invite the Bot" section when you add it to your server. Lack of permissions (like Send Messages or Embed Links) will prevent it from working correctly.
*   **User Permissions (Commands):**
    *   `!delete_timer` requires the user invoking the command to have the `Manage Server` permission within Discord.
    *   `!set_reminder_interval` requires the user invoking the command to have the `Manage Server` permission.
    *   Other commands are generally available to all users who can see the channel and send messages.

---

*Feel free to open an issue if you find bugs or have suggestions for improvement.*