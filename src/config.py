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
    """Configuration settings loaded from environment variables"""
    
    # Discord Bot Settings
    DISCORD_TOKEN: str = os.getenv('DISCORD_TOKEN', '')
    ALLOWED_ROLES: List[str] = [role.strip() for role in os.getenv('ALLOWED_ROLES', 'Theater 2,Theater Host').split(',')]
    
    # VLC Settings
    VLC_HOST: str = os.getenv('VLC_HOST', 'localhost')
    VLC_PORT: int = int(os.getenv('VLC_PORT', '8080'))
    VLC_PASSWORD: str = os.getenv('VLC_PASSWORD', 'vlc')
    
    # TMDB Settings
    TMDB_API_KEY: str = os.getenv('TMDB_API_KEY', '')
    
    # Queue Settings
    QUEUE_BACKUP_FILE: str = os.getenv('QUEUE_BACKUP_FILE', 'queue_backup.json')
    
    # Playlist Settings
    ITEMS_PER_PAGE: int = int(os.getenv('ITEMS_PER_PAGE', '20'))
    
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
            
        return errors
    
    @classmethod
    def print_config(cls) -> None:
        """Log the current configuration (excluding sensitive values)"""
        logger = logging.getLogger(__name__)
        config_lines = [
            "Current Configuration:",
            "-" * 50,
            f"VLC Host: {cls.VLC_HOST}",
            f"VLC Port: {cls.VLC_PORT}",
            f"Allowed Roles: {', '.join(cls.ALLOWED_ROLES)}",
            f"Queue Backup File: {cls.QUEUE_BACKUP_FILE}",
            f"Items Per Page: {cls.ITEMS_PER_PAGE}",
            f"TMDB API Key: {'Configured' if cls.TMDB_API_KEY else 'Not Configured'}",
            f"Discord Token: {'Configured' if cls.DISCORD_TOKEN else 'Not Configured'}",
            "-" * 50
        ]
        # Log each line separately for better formatting
        for line in config_lines:
            logger.info(line)
