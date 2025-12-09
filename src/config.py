from typing import List
import os
import logging
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

class Config:
    # Optional: Role ID to mention in scheduled movie announcements (set to 0 or leave empty to disable)
    WATCH_ANNOUNCE_ROLE_ID: int = int(os.getenv('WATCH_ANNOUNCE_ROLE_ID', '0'))
    """Configuration settings loaded from environment variables"""
    
    # Discord Bot Settings
    DISCORD_TOKEN: str = os.getenv('DISCORD_TOKEN', '')
    ALLOWED_ROLES: List[str] = [role.strip() for role in os.getenv('ALLOWED_ROLES', 'Theater 2,Theater Host').split(',')]
    
    # VLC Settings
    VLC_HOST: str = os.getenv('VLC_HOST', 'localhost')
    VLC_PORT: int = int(os.getenv('VLC_PORT', '8080'))
    VLC_PASSWORD: str = os.getenv('VLC_PASSWORD', 'vlc')

    # Discord Command Prefix
    DISCORD_COMMAND_PREFIX: str = os.getenv('DISCORD_COMMAND_PREFIX', '!')
    
    # TMDB Settings
    TMDB_API_KEY: str = os.getenv('TMDB_API_KEY', '')
    
    # Queue Settings
    QUEUE_BACKUP_FILE: str = os.getenv('QUEUE_BACKUP_FILE', 'queue_backup.json')
    # Optional: periodically save current VLC playlist to a file (relative to bot dir if not absolute)
    PLAYLIST_AUTOSAVE_FILE: str = os.getenv('PLAYLIST_AUTOSAVE_FILE', '').strip()
    PLAYLIST_AUTOSAVE_INTERVAL: int = int(os.getenv('PLAYLIST_AUTOSAVE_INTERVAL', '300'))
    
    # Playlist Settings
    ITEMS_PER_PAGE: int = int(os.getenv('ITEMS_PER_PAGE', '20'))

    # Optional Ko-fi / support URL to show in embeds
    KOFI_URL: str = os.getenv('KOFI_URL', '').strip()

    # Presence / Rich presence toggles
    # Enable or disable the bot updating its Discord presence/activity (default: true)
    ENABLE_PRESENCE: bool = os.getenv('ENABLE_PRESENCE', 'true').strip().lower() in {'1','true','yes','y'}
    # Throttle (seconds) between presence updates to avoid rate limits
    PRESENCE_UPDATE_THROTTLE: int = int(os.getenv('PRESENCE_UPDATE_THROTTLE', '5'))
    # Include progress (time/length) in presence and update periodically
    ENABLE_PRESENCE_PROGRESS: bool = os.getenv('ENABLE_PRESENCE_PROGRESS', 'true').strip().lower() in {'1','true','yes','y'}
    # Interval (seconds) between presence progress updates
    PRESENCE_PROGRESS_UPDATE_INTERVAL: int = int(os.getenv('PRESENCE_PROGRESS_UPDATE_INTERVAL', '30'))

    # Periodic "Now Playing" Announcements
    PERIODIC_ANNOUNCE_ENABLED: bool = os.getenv('PERIODIC_ANNOUNCE_ENABLED', 'false').strip().lower() in {'1','true','yes','y'}
    PERIODIC_ANNOUNCE_INTERVAL: int = int(os.getenv('PERIODIC_ANNOUNCE_INTERVAL', '300'))

    # Voice Channel Settings
    # Enable or disable the bot automatically joining a voice channel (default: true)
    ENABLE_VOICE_JOIN: bool = os.getenv('ENABLE_VOICE_JOIN', 'true').strip().lower() in {'1','true','yes','y'}
    # Discord voice channel ID for the bot to join
    VOICE_JOIN_CHANNEL_ID: int = int(os.getenv('VOICE_JOIN_CHANNEL_ID', '0'))
    # Whether to join voice immediately on startup (default: true)
    VOICE_AUTO_JOIN_ON_START: bool = os.getenv('VOICE_AUTO_JOIN_ON_START', 'true').strip().lower() in {'1','true','yes','y'}
    # Voice reconnect and connection tuning
    VOICE_MAX_RECONNECTS: int = int(os.getenv('VOICE_MAX_RECONNECTS', '3'))
    VOICE_RECONNECT_WINDOW: int = int(os.getenv('VOICE_RECONNECT_WINDOW', '60'))  # seconds
    VOICE_RECONNECT_COOLDOWN: int = int(os.getenv('VOICE_RECONNECT_COOLDOWN', '30'))  # seconds
    VOICE_CONNECT_TIMEOUT: float = float(os.getenv('VOICE_CONNECT_TIMEOUT', '20.0'))
    VOICE_CONNECT_RETRY_DELAY: float = float(os.getenv('VOICE_CONNECT_RETRY_DELAY', '2.0'))
    VOICE_ERROR_RETRY_DELAY: float = float(os.getenv('VOICE_ERROR_RETRY_DELAY', '5.0'))
    VOICE_INITIAL_RETRIES: int = int(os.getenv('VOICE_INITIAL_RETRIES', '2'))  # extra retries after first attempt
    # Voice logging and debounce/settle controls
    DISCORD_VOICE_LOG_LEVEL: str = os.getenv('DISCORD_VOICE_LOG_LEVEL', 'CRITICAL').strip()
    VOICE_INITIAL_SETTLE_SECONDS: float = float(os.getenv('VOICE_INITIAL_SETTLE_SECONDS', '20.0'))
    VOICE_DEBOUNCE_SECONDS: float = float(os.getenv('VOICE_DEBOUNCE_SECONDS', '5.0'))
    # Voice guard/event toggles
    ENABLE_VOICE_GUARD: bool = os.getenv('ENABLE_VOICE_GUARD', 'false').strip().lower() in {'1','true','yes','y'}
    ENABLE_VOICE_EVENTS_RECONNECT: bool = os.getenv('ENABLE_VOICE_EVENTS_RECONNECT', 'true').strip().lower() in {'1','true','yes','y'}

    # Watch Folders
    # Comma-separated absolute paths. If empty, watch service is disabled.
    WATCH_FOLDERS = [p.strip() for p in os.getenv('WATCH_FOLDERS', '').split(',') if p.strip()]
    # Polling interval in seconds for watch service
    WATCH_SCAN_INTERVAL: int = int(os.getenv('WATCH_SCAN_INTERVAL', '10'))
    # Minimum age (seconds) a file must remain unchanged before it is considered stable
    WATCH_STABLE_AGE: float = float(os.getenv('WATCH_STABLE_AGE', '2'))
    # Whether to enqueue discovered files on the initial scan (default: true)
    WATCH_ENQUEUE_ON_START: bool = os.getenv('WATCH_ENQUEUE_ON_START', 'true').strip().lower() in {'1','true','yes','y'}
    # Always load announce channel IDs from the environment at runtime
    @staticmethod
    def get_announce_channel_ids() -> list[int]:
        val = os.getenv('WATCH_ANNOUNCE_CHANNEL_ID', '0')
        return [int(cid.strip()) for cid in val.split(',') if cid.strip() and cid.strip() != '0']
    # Max items to list per announcement message
    WATCH_ANNOUNCE_MAX_ITEMS: int = int(os.getenv('WATCH_ANNOUNCE_MAX_ITEMS', '10'))
    # Throttle in milliseconds between per-file announcements (when not initial scan)
    WATCH_ANNOUNCE_THROTTLE_MS: int = int(os.getenv('WATCH_ANNOUNCE_THROTTLE_MS', '500'))
    
    @classmethod
    def validate(cls) -> List[str]:
        """Validate the configuration
        
        Returns:
            List of error messages, empty if configuration is valid
        """
        errors = []
        
        if not cls.DISCORD_TOKEN:
            errors.append("DISCORD_TOKEN is required")
            
        if not cls.ALLOWED_ROLES:
            errors.append("ALLOWED_ROLES must contain at least one role")
            
        try:
            if not (1 <= cls.VLC_PORT <= 65535):
                errors.append("VLC_PORT must be between 1 and 65535")
        except ValueError:
            errors.append("VLC_PORT must be a valid integer")
            
        if not cls.TMDB_API_KEY:
            errors.append("TMDB_API_KEY is required for movie metadata features")
            
        try:
            if cls.ITEMS_PER_PAGE < 1:
                errors.append("ITEMS_PER_PAGE must be greater than 0")
        except ValueError:
            errors.append("ITEMS_PER_PAGE must be a valid integer")
        # Validate autosave interval if feature enabled
        if cls.PLAYLIST_AUTOSAVE_FILE:
            try:
                if cls.PLAYLIST_AUTOSAVE_INTERVAL < 10:
                    errors.append("PLAYLIST_AUTOSAVE_INTERVAL should be at least 10 seconds if autosave is enabled")
            except ValueError:
                errors.append("PLAYLIST_AUTOSAVE_INTERVAL must be a valid integer (seconds)")

        # Validate watch folders if provided
        for folder in cls.WATCH_FOLDERS:
            if not os.path.isabs(folder):
                errors.append(f"WATCH_FOLDERS entry must be an absolute path: {folder}")
            elif not os.path.isdir(folder):
                errors.append(f"WATCH_FOLDERS entry is not a directory or not found: {folder}")
        try:
            if cls.WATCH_SCAN_INTERVAL < 1:
                errors.append("WATCH_SCAN_INTERVAL must be greater than 0")
        except ValueError:
            errors.append("WATCH_SCAN_INTERVAL must be a valid integer")
        try:
            if cls.WATCH_STABLE_AGE < 0:
                errors.append("WATCH_STABLE_AGE must be 0 or greater")
        except ValueError:
            errors.append("WATCH_STABLE_AGE must be a valid number (seconds)")
            
        # Voice join validation
        if cls.ENABLE_VOICE_JOIN and cls.VOICE_AUTO_JOIN_ON_START:
            try:
                if cls.VOICE_JOIN_CHANNEL_ID <= 0:
                    errors.append("VOICE_JOIN_CHANNEL_ID must be set to a valid Discord channel ID if voice join is enabled")
            except ValueError:
                errors.append("VOICE_JOIN_CHANNEL_ID must be a valid integer")

        # Periodic announcement validation
        if cls.PERIODIC_ANNOUNCE_ENABLED:
            try:
                if cls.PERIODIC_ANNOUNCE_INTERVAL < 30:
                    errors.append("PERIODIC_ANNOUNCE_INTERVAL must be at least 30 seconds")
            except ValueError:
                errors.append("PERIODIC_ANNOUNCE_INTERVAL must be a valid integer")
            
        return errors
    
    @classmethod
    def print_config(cls) -> None:
        """Log the current configuration (excluding sensitive values)"""
        logger = logging.getLogger(__name__)
        announce_ids = cls.get_announce_channel_ids()
        config_lines = [
            f"Discord Command Prefix: {cls.DISCORD_COMMAND_PREFIX}",
            "Current Configuration:",
            "-" * 50,
            f"VLC Host: {cls.VLC_HOST}",
            f"VLC Port: {cls.VLC_PORT}",
            f"Allowed Roles: {', '.join(cls.ALLOWED_ROLES)}",
            f"Queue Backup File: {cls.QUEUE_BACKUP_FILE}",
            f"Items Per Page: {cls.ITEMS_PER_PAGE}",
            f"Watch Folders: {', '.join(cls.WATCH_FOLDERS) if cls.WATCH_FOLDERS else 'Disabled'}",
            f"Watch Scan Interval: {cls.WATCH_SCAN_INTERVAL}s",
            f"Watch Stable Age: {cls.WATCH_STABLE_AGE}s",
            f"Watch Enqueue On Start: {cls.WATCH_ENQUEUE_ON_START}",
            f"Watch Announce Channels: {announce_ids if announce_ids else 'Disabled'}",
            f"Watch Announce Max Items: {cls.WATCH_ANNOUNCE_MAX_ITEMS}",
            (
                f"Playlist Autosave: file='{cls.PLAYLIST_AUTOSAVE_FILE}', interval={cls.PLAYLIST_AUTOSAVE_INTERVAL}s"
                if cls.PLAYLIST_AUTOSAVE_FILE else "Playlist Autosave: Disabled"
            ),
            f"Voice Join Enabled: {cls.ENABLE_VOICE_JOIN}",
            f"Voice Channel ID: {cls.VOICE_JOIN_CHANNEL_ID if cls.VOICE_JOIN_CHANNEL_ID else 'Not Configured'}",
            f"Auto Join On Start: {cls.VOICE_AUTO_JOIN_ON_START}",
            f"Voice Max Reconnects: {cls.VOICE_MAX_RECONNECTS}",
            f"Voice Reconnect Window: {cls.VOICE_RECONNECT_WINDOW}s",
            f"Voice Reconnect Cooldown: {cls.VOICE_RECONNECT_COOLDOWN}s",
            f"Voice Connect Timeout: {cls.VOICE_CONNECT_TIMEOUT}s",
            f"Voice Connect Retry Delay: {cls.VOICE_CONNECT_RETRY_DELAY}s",
            f"Voice Error Retry Delay: {cls.VOICE_ERROR_RETRY_DELAY}s",
            f"Voice Initial Retries: {cls.VOICE_INITIAL_RETRIES}",
            f"Voice Initial Settle: {cls.VOICE_INITIAL_SETTLE_SECONDS}s",
            f"Voice Debounce: {cls.VOICE_DEBOUNCE_SECONDS}s",
            f"Discord Voice Log Level: {cls.DISCORD_VOICE_LOG_LEVEL}",
            f"Voice Guard Enabled: {cls.ENABLE_VOICE_GUARD}",
            f"Voice Events Reconnect Enabled: {cls.ENABLE_VOICE_EVENTS_RECONNECT}",
            f"TMDB API Key: {'Configured' if cls.TMDB_API_KEY else 'Not Configured'}",
            f"Discord Token: {'Configured' if cls.DISCORD_TOKEN else 'Not Configured'}",
            f"Ko-fi URL: {cls.KOFI_URL if cls.KOFI_URL else 'Not Configured'}",
            f"Presence Updates Enabled: {cls.ENABLE_PRESENCE}",
            f"Presence Update Throttle: {cls.PRESENCE_UPDATE_THROTTLE}s",
            f"Presence Progress Enabled: {cls.ENABLE_PRESENCE_PROGRESS}",
            f"Presence Progress Interval: {cls.PRESENCE_PROGRESS_UPDATE_INTERVAL}s",
            f"Periodic Announcements Enabled: {cls.PERIODIC_ANNOUNCE_ENABLED}",
            f"Periodic Announcement Interval: {cls.PERIODIC_ANNOUNCE_INTERVAL}s",
            "-" * 50
        ]
        # Log each line separately for better formatting
        for line in config_lines:
            logger.info(line)
