from typing import Optional
import os
import re

class MediaUtils:
    @staticmethod
    def clean_movie_title(filename: str) -> str:
        """Clean up movie filename to get a searchable title
        
        Args:
            filename: The filename to clean
            
        Returns:
            Cleaned movie title suitable for searching
        """
        # Remove file extension
        title = os.path.splitext(os.path.basename(filename))[0]
        
        # First remove anything after the year if present
        year_match = re.search(r'\.?\d{4}', title)
        if year_match:
            title = title[:year_match.start()]
        
        # Remove anything after common delimiters
        for delimiter in [' - ', '.-.', '.-', '-.']:
            if delimiter in title:
                title = title.split(delimiter)[0]
        
        # Common patterns to remove (order is important)
        patterns = [
            r'\[.*?\]|\(.*?\)',  # Anything in brackets or parentheses first
            r'\.(?:mkv|avi|mp4|mov)$',  # File extensions
            r'(?:480|720|1080|2160)p?',  # Resolutions
            r'bluray|brrip|bdrip|webrip|web-?dl|dvdrip|hdtv',  # Sources
            r'(?:x|h)\.?26[45]|xvid|hevc',  # Codecs
            r'DD(?:P)?5\.?1|DTS(?:-HD)?|AAC(?:\d\.?\d)?|DDP\d\.?\d|ATMOS|TrueHD|OPUS',  # Audio
            r'REPACK|PROPER|EXTENDED|THEATRICAL|DIRECTOR\'?S\.?CUT',  # Versions
            r'HDR\d*|DV|DOLBY\.?VISION|SDR',  # HDR
            r'IMAX',  # Format
            r'AMZN|DSNP|NF|HULU|HBO|DSNY|ATVP',  # Streaming services
            r'-\w+$',  # Release group at the end
            r'\b\d{4}\b',  # Year (if not caught earlier)
        ]
        
        # Convert dots and underscores to spaces
        title = title.replace('.', ' ').replace('_', ' ')
        
        # Apply all cleanup patterns
        for pattern in patterns:
            title = re.sub(pattern, '', title, flags=re.IGNORECASE)
        
        # Clean up whitespace and remove single-letter words
        title = ' '.join(word for word in title.split() if len(word) > 1)
        title = ' '.join(title.split())  # Remove extra spaces
        
        return title

    @staticmethod
    def get_media_icon(filename):
        """Get appropriate icon for media file type"""
        name = filename.lower()
        if name.endswith(('.mp4', '.mkv', '.avi', '.mov')):
            return '🎬'  # Movie
        elif name.endswith(('.mp3', '.wav', '.flac', '.m4a')):
            return '🎵'  # Music
        return '📄'  # Default

    @staticmethod
    def format_time(seconds: int) -> str:
        """Format time in seconds to HH:MM:SS
        
        Args:
            seconds: Number of seconds
            
        Returns:
            Formatted time string in HH:MM:SS format
        """
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        seconds = seconds % 60
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        
    @staticmethod
    def clean_filename_for_display(filename: str, max_length: int = 50) -> str:
        """Clean up filename for display in Discord messages
        
        Args:
            filename: Original filename
            max_length: Maximum length of cleaned filename
            
        Returns:
            Cleaned filename suitable for display
        """
        basename = os.path.basename(filename)
        name, _ = os.path.splitext(basename)
        
        # Replace dots and underscores with spaces
        name = name.replace('.', ' ').replace('_', ' ')
        
        # Find and preserve the year
        year = None
        year_match = re.search(r'\b(19|20)\d{2}\b', name)
        if year_match:
            year = year_match.group(0)
            name = re.sub(r'\b' + re.escape(year) + r'\b', '', name)
        
        # Remove common patterns
        patterns = [
            r'\s*\([^)]*\)\s*',  # Remove parentheses and contents
            r'\b\d{3,4}p\b',  # Resolution
            r'AMZN|DSNP|NF|HULU|HBO|DSNY|ATVP',  # Streaming services
            r'WEB-?DL|BluRay|BRRip|HDRip|DVDRip',  # Source
            r'DDP\d\.\d|DD\d\.\d|AAC\d\.\d|AAC2\.0|DDP[0-9]',  # Audio
            r'H\.?264|x264|HEVC|[Hh]265',  # Codecs
            r'-\w+$',  # Release group at end
            r'\s+\d+\s+\d+\s+\d+'  # Number sequences
        ]
        
        for pattern in patterns:
            name = re.sub(pattern, '', name, flags=re.IGNORECASE)
        
        # Clean up spaces
        name = ' '.join(name.split())
        
        # Add year back if found
        if year:
            name = f"{name} ({year})"
        
        # Truncate if too long
        if len(name) > max_length:
            name = name[:max_length-3] + "..."
            
        return name
