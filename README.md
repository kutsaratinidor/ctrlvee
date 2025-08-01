# CtrlVee - a Discord VLC Bot

A Discord bot that controls VLC media player, manages playlists, provides movie metadata integration, and features an intelligent queue system on Windows and macOS. This idea was due to a need for users on a discord server to be able to control the screen shared VLC setup I have even if I am away. It allows more options for them for viewing instead of just relying on the randomness of the playlist. It also allows me to not be around or remoting into that computer all the time. I have not seen this to be available and it makes sense because you need to have local access to the host where VLC is running. I used Github Copilot to build this and refine the bot. It used to be just one single python file, but after asking it to be refactored, it rebuilt and implemented it in a more proper way. 

## Features

- **VLC Playback Control**
  - Basic controls (play, pause, stop, restart)
  - Navigate playlist (next, previous)
  - Jump to specific items using numbers
  - Rewind playback with customizable seconds
  - Shuffle mode control (enable, disable, toggle)
  - Progress display with timestamps and enhanced status

- **Intelligent Queue System**
  - Queue items to play next with automatic shuffle handling
  - Soft queue implementation compatible with VLC 3.x
  - Automatic shuffle restoration after queued items finish
  - Queue persistence across bot restarts
  - Real-time queue status with item titles and positions

- **Movie Information**
  - Automatic movie metadata lookup via TMDB
  - Movie posters, release dates, and ratings
  - Direct links to TMDB movie pages

- **Playlist Management**
  - View and navigate existing VLC playlist with pagination
  - List all items with interactive navigation buttons
  - Quick replay of items using item numbers
  - Search and filter playlist contents
  - Play search results directly
  
- **Enhanced State Monitoring**
  - Track VLC state changes with cooldown protection
  - Notify about manual interventions and queue transitions
  - Configurable notification channel
  - State change history and queue event tracking
  - Automatic detection of media ending and queue handling

## Screenshots

### Bot Commands and Help
<img src="screenshots/help-command.png" alt="Bot Help Command" width="400">

## Prerequisites

- Python 3.6 or higher
- VLC media player installed (Windows or macOS)
- Discord bot token

### Discord Bot Setup
1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Create a new application or select your existing one
3. Go to the "Bot" section
4. Enable the following Privileged Intents:
   - MESSAGE CONTENT INTENT
5. Under "Bot Permissions", enable:
   - Read Messages/View Channels
   - Send Messages
   - Embed Links
   - Read Message History

### VLC Setup
1. Open VLC Media Player
2. Go to Preferences (or Settings)
3. Enable the Web Interface:
   - On macOS: VLC > Preferences > Interface > Main Interfaces > check "Web"
   - On Windows: Tools > Preferences > Interface > Main Interfaces > check "Web"
4. Set a password (optional but recommended)
5. Restart VLC after making these changes

### macOS Specific Setup
- Install VLC from the official website or using Homebrew: `brew install --cask vlc`
- Make sure VLC is installed in the standard location (/Applications/VLC.app)

## Installation

1. Clone this repository

2. Set up Python environment:
   ```
   # Create a virtual environment
   python -m venv venv
   
   # Activate the virtual environment
   # On Windows:
   .\venv\Scripts\activate
   # On macOS/Linux:
   source venv/bin/activate
   ```

3. Install required packages:
   ```
   # Install dependencies
   pip install -r requirements.txt
   
   # If you get "no module named audioop" error, run:
   pip install audioop-lts
   ```

4. Set up your configuration:
   ```bash
   # Copy the template environment file
   cp template.env .env
   
   # Edit the .env file with your settings
   nano .env   # or use any text editor
   ```
   
   Required Configuration:
   - `DISCORD_TOKEN`: Your Discord bot token
   - `TMDB_API_KEY`: Your TMDB API key (optional, but recommended for movie metadata)
   
   Optional Configuration:
   - `ALLOWED_ROLES`: Comma-separated list of roles that can control playback (default: "Theater Host")
   - `VLC_HOST`: VLC HTTP interface host (default: localhost)
   - `VLC_PORT`: VLC HTTP interface port (default: 8080)
   - `VLC_PASSWORD`: VLC HTTP interface password (default: vlc)
   - `QUEUE_BACKUP_FILE`: Path to queue backup file (default: queue_backup.json)
   - `ITEMS_PER_PAGE`: Number of items per page in playlist view (default: 20)

## Usage

1. Start the bot:
   ```
   python bot.py
   ```

2. Available commands in Discord:
   
   **Playback Controls:**
   - `!play` - Start/resume playback
   - `!pause` - Pause playback
   - `!stop` - Stop playback
   - `!next` - Play next item
   - `!previous` - Play previous item
   - `!restart` - Restart current file from beginning
   - `!rewind [seconds]` - Rewind by specified seconds (default: 10)
   - `!play_num <number>` - Play specific item by number
   - `!shuffle` - Toggle shuffle mode on/off
   - `!shuffle_on` - Enable shuffle mode
   - `!shuffle_off` - Disable shuffle mode

   **Playlist Management:**
   - `!list` - Show playlist with interactive navigation (⏮️, ◀️, ▶️, ⏭️ buttons)
   - `!search <query>` - Search playlist
   - `!play_search <query>` - Search and play first match

   **Queue Management:**
   - `!queue_next <number>` - Queue a playlist item to play next (temporarily disables shuffle if needed)
   - `!queue_status` - Show current queue with item titles and playlist positions
   - `!clear_queue` - Clear all queue tracking

   **Status & Information:**
   - `!status` - Show current VLC status (state, volume, playing item)
   - `!controls` - Show this help message

   **Notification Settings:**
   - `!set_notification_channel` - Set current channel for VLC state notifications
   - `!unset_notification_channel` - Disable notifications
   - `!show_notification_channel` - Show notification settings

## Known Issues

- **Metadata Matching**: Movie metadata from TMDB may not always match the actual media file. This occurs due to how media file names are parsed and handled by the system. File names with non-standard formatting, special characters, year mismatches, or quality indicators (like "1080p", "BluRay") can interfere with accurate metadata retrieval. This is a known limitation that we plan to improve in future versions by implementing better file name parsing and fuzzy matching algorithms.

## Recent Improvements

- **Enhanced Queue System**: Implemented intelligent soft queue management with automatic shuffle handling and restoration
- **Code Refactoring**: Major cleanup removed debug commands, simplified user messaging, and improved code organization
- **Better Error Handling**: Consolidated HTTP request logic with consistent error handling across all VLC operations  
- **UI Cleanup**: Streamlined queue status display to show only essential information without technical implementation details
- **Improved Monitoring**: Enhanced state monitoring with cooldown protection and better queue transition detection
- **Queue Persistence**: Queue state is now automatically saved and restored across bot restarts

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request. For major changes, please open an issue first to discuss what you would like to change.

## Notes

- The bot uses TMDB for movie metadata. You'll get better results if your media files are named accurately.
- VLC's HTTP interface must be enabled for the bot to function.
- The bot works with VLC's existing playlist - add your media files directly through VLC.
- **Queue System**: Uses a soft queue approach compatible with VLC 3.x that temporarily disables shuffle when needed and automatically restores it after queued items finish playing.
- **State Monitoring**: Enhanced monitoring helps track manual changes made directly in VLC and automatically handles queue transitions.
- **Role-Based Access**: Most commands require specific Discord roles (configurable via `ALLOWED_ROLES` in `.env`).
- **Queue Persistence**: Queue state is automatically saved to `queue_backup.json` and restored when the bot restarts.

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Acknowledgments

This bot was built with assistance from GitHub Copilot.
