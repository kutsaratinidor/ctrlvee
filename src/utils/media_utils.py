from typing import Optional, Tuple
import os
import re

class MediaUtils:
    @staticmethod
    def get_media_duration(item) -> int | str | None:
        """Try to extract duration (in seconds) from a VLC playlist item (Element). Returns int, 'Loading...', or None."""
        import logging
        logger = logging.getLogger(__name__)
        # Try attribute first
        dur_attr = item.get('duration')
        logger.debug(f"VLC item duration attribute: {dur_attr}")
        if dur_attr:
            try:
                seconds = int(dur_attr)
                if seconds > 0:
                    return seconds
                elif seconds == 0:
                    return 'Loading...'
            except Exception as e:
                logger.debug(f"Error parsing duration attribute: {e}")
        # Try child element
        dur_elem = item.find('duration')
        logger.debug(f"VLC item duration element: {getattr(dur_elem, 'text', None)}")
        if dur_elem is not None and dur_elem.text:
            try:
                seconds = int(dur_elem.text)
                if seconds > 0:
                    return seconds
                elif seconds == 0:
                    return 'Loading...'
            except Exception as e:
                logger.debug(f"Error parsing duration element: {e}")
        return None
    @staticmethod
    def parse_movie_filename(filename: str) -> Tuple[str, Optional[int]]:
        """Parse a movie filename into a clean title and optional release year.
        
        This aims to be conservative about treating a 4-digit number as a year:
        - Prefer the rightmost 4-digit year token among candidates between 1900-2099
        - If the only 4-digit token is the first token (e.g. "1917") and there are no
          other year candidates, treat it as the title (year=None)
        - Stop title collection at the detected year or the first noise token (e.g. 1080p, BluRay)
        
        Args:
            filename: A path or filename for a movie file.
        Returns:
            (title, year) where title is cleaned for searching and year is an int or None
        """
        basename = os.path.basename(filename)
        name, _ = os.path.splitext(basename)

        # Normalize separators and remove brackets to simplify tokenization
        normalized = name.replace('.', ' ').replace('_', ' ')
        normalized = re.sub(r'[\(\)\[\]]', ' ', normalized)
        normalized = ' '.join(normalized.split())

        tokens = re.split(r"[\s\-]+", normalized)
        ltokens = [t.lower() for t in tokens]

        def is_year_token(tok: str) -> bool:
            return tok.isdigit() and len(tok) == 4 and 1900 <= int(tok) <= 2099

        # Known noise detectors
        source_tokens = {
            'bluray', 'brrip', 'bdrip', 'webrip', 'web', 'webdl', 'web-dl', 'dvdrip', 'hdtv',
            'remux', 'hdrip', 'cam', 'tc', 'ts'
        }
        codec_tokens = {'x264', 'x265', 'h264', 'h265', 'hevc', 'av1'}
        audio_tokens = {
            'dd', 'dd5', 'dd51', 'ddp', 'ddp5', 'ddp51', 'dts', 'dtshd', 'aac', 'opus', 'truehd', 'atmos'
        }
        tag_tokens = {
            'proper', 'repack', 'extended', 'theatrical', 'directors', 'director', 'cut', 'imax',
            'hdr', 'hdr10', 'dv', 'dolby', 'vision', 'sdr',
            # hardcoded subs markers
            'hc', 'hardsub', 'hardsubs', 'hardcoded', 'hcsubs', 'hcsub', 'hcsubbed'
        }

        def is_resolution(tok: str) -> bool:
            return bool(re.match(r'^\d{3,4}p$', tok)) or tok in {'uhd', '4k', '1080', '720', '480'}

        def is_noise(tok: str) -> bool:
            t = tok.lower()
            return (
                is_resolution(t) or
                t in source_tokens or
                t in codec_tokens or
                t in audio_tokens or
                t in tag_tokens or
                bool(re.match(r'^[\w-]+$' , t)) and t.startswith('rarbg')
            )

        # Find candidate year positions (prefer the rightmost one)
        year_positions = [i for i, tok in enumerate(tokens) if is_year_token(tok)]
        year_idx: Optional[int] = None
        year_val: Optional[int] = None

        if year_positions:
            # Special case: only one year and it's the first token -> treat as title token, not year
            if len(year_positions) == 1 and year_positions[0] == 0:
                year_idx = None
                year_val = None
            else:
                year_idx = year_positions[-1]
                try:
                    year_val = int(tokens[year_idx])
                except Exception:
                    year_val = None

        # Determine where the meaningful title likely ends
        stop_idx = len(tokens)
        if year_idx is not None:
            stop_idx = min(stop_idx, year_idx)
        else:
            for i, tok in enumerate(tokens):
                if is_noise(tok):
                    stop_idx = i
                    break

        # Build title tokens and drop known noise (providers, codecs, tags like HC) even before the year
        title_tokens = [tok for tok in tokens[:stop_idx] if tok and not is_noise(tok)]
        # If we ended up with no tokens (e.g., title started with a year-only token), fallback to the first token
        if not title_tokens and tokens:
            title_tokens = [tokens[0]]

        title = ' '.join(title_tokens)
        # Final cleanup: remove extra spaces and stray dashes/underscores
        title = ' '.join(title.split())

        return title, year_val
    @staticmethod
    def clean_movie_title(filename: str) -> str:
        """Clean up movie filename to get a searchable title
        
        Args:
            filename: The filename to clean
            
        Returns:
            Cleaned movie title suitable for searching
        """
        # Reuse the more robust parser and return only the title part
        title, _year = MediaUtils.parse_movie_filename(filename)
        return title

    @staticmethod
    def parse_tv_filename(filename: str) -> Tuple[Optional[str], Optional[int], Optional[int]]:
        """Parse a TV episode filename into (series_title, season, episode).

        Handles common scene patterns like:
        - Show.Name.S02E03.1080p...
        - Show Name - S02E03 - ...
        - Show Name 2x03 ...
        - Season folders: parent directory 'Season 2'
        """
        base = os.path.basename(filename)
        name, _ = os.path.splitext(base)
        work = name.replace('.', ' ').replace('_', ' ')
        # Season/Episode patterns
        m = re.search(r'(?i)\bS(\d{1,2})E(\d{1,2})\b', work)
        if not m:
            m = re.search(r'(?i)\b(\d{1,2})x(\d{1,2})\b', work)
        season = int(m.group(1)) if m else None
        episode = int(m.group(2)) if m else None

        # Remove season/episode token and common noise to extract series name
        if m:
            work = work[:m.start()].strip()
        # Drop release noise similar to movie cleaning
        work = re.sub(r'\s*\b(480|576|720|1080|2160|4320)p\b', ' ', work, flags=re.IGNORECASE)
        work = re.sub(r'\b(?:web(?:-?dl|-?rip)?|bluray|brrip|bdrip|hdrip|hdtv|dvdrip|remux)\b', ' ', work, flags=re.IGNORECASE)
        work = re.sub(r'\b(?:x?26[45]|h\.?26[45]|hevc|av1|xvid)\b', ' ', work, flags=re.IGNORECASE)
        work = re.sub(r'\s+', ' ', work).strip()
        # Cleanup trailing separators like '-' or '–'
        work = re.sub(r'[\-–]+\s*$', '', work).strip()

        series = work if work else None
        return series, season, episode

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

        # Precompiled regex patterns (module-level caching via function attributes)
        if not hasattr(MediaUtils, '_DISPLAY_REGEX'):
            MediaUtils._DISPLAY_REGEX = {
                'year': re.compile(r'\b(19|20)\d{2}\b'),
                'brackets': re.compile(r'\s*[\(\[\{][^\)\]\}]*[\)\]\}]\s*'),
                'resolution': re.compile(r'\b(?:480|576|720|1080|2160|4320)p\b|\b(?:4k|uhd)\b', re.IGNORECASE),
                'source': re.compile(r'\b(?:web(?:-?dl|-?rip)?|bluray|brrip|bdrip|hdrip|hdtv|dvdrip|remux)\b', re.IGNORECASE),
                'provider': re.compile(r'\b(?:amzn|dsnp|dsny|nf|netflix|hulu|hbo|hmax|max|atvp|atv|apple|disney|paramount|pmnt|peacock)\b', re.IGNORECASE),
                'audio': re.compile(r'\b(?:ddp?\d(?:\.\d)?|dts(?:-?hd)?|aac(?:\d(?:\.\d)?)?|opus|truehd|atmos)\b', re.IGNORECASE),
                'codec': re.compile(r'\b(?:x?26[45]|h\.?26[45]|hevc|av1|xvid)\b', re.IGNORECASE),
                'hdr': re.compile(r'\b(?:hdr10\+?|hdr|dv|dolby\s*vision|sdr)\b', re.IGNORECASE),
                'tags': re.compile(r'\b(?:proper|repack|remux|extended|theatrical|director(?:\'s)?\s*cut|unrated|uncut|imax|limited|internal|readnfo|hc|hardsubs?|hardcoded|hcsubs?)\b', re.IGNORECASE),
                'lang': re.compile(r'\b(?:multi|vostfr|french|truefrench|subfrench|ger|deu|nl|eng|en|ita|esp|castellano|latino|ptbr|por|rus|hindi)\b', re.IGNORECASE),
                'site': re.compile(r'\b(?:yts|yify|rarbg|evo|fgt|sparks|ntg|tgx|etrg|galaxyrg|ganool|msd|ettv|eztv)\b', re.IGNORECASE),
                'group_suffix': re.compile(r'-[A-Za-z0-9]+$'),
                'numseq': re.compile(r'\s+\d+\s+\d+\s+\d+'),
                'episode': re.compile(r'\bS\d{1,2}E\d{1,2}\b', re.IGNORECASE),
            }

        RX = MediaUtils._DISPLAY_REGEX

        # Pre-remove tricky combos on the raw name before replacing separators
        pre = name
        # Remove WEB-DL / WEBRip variants even with separators
        pre = re.sub(r'(?i)\bWEB(?:[-_\. ]?DL|[-_\. ]?RIP)\b', ' ', pre)
        # Remove HC / hardsub indicators
        pre = re.sub(r'(?i)\bHC(?:SUBS?|SUBBED)?\b', ' ', pre)
        pre = re.sub(r'(?i)\bHARD(?:-?SUBS?|CODED)\b', ' ', pre)
        # Remove audio like DDP5.1, DDP.5.1, or DDP5 1
        pre = re.sub(r'(?i)\bDDP?[\.\s]?\d(?:[\.\s]?\d)?\b', ' ', pre)
        # Remove AAC2.0 variants before they split into 'AAC2' and '0'
        pre = re.sub(r'(?i)\bAAC(?:[\._\s]?\d(?:[\._\s]?\d)?)\b', ' ', pre)
        # Remove codec like H.264 / H 264 / H264
        pre = re.sub(r'(?i)\bH(?:[\.\s]?26[45])\b', ' ', pre)
        pre = re.sub(r'(?i)\b(?:x?26[45]|HEVC|AV1)\b', ' ', pre)

        # Normalize separators for tokenization (keep hyphens so -GROUP is intact)
        work = pre.replace('.', ' ').replace('_', ' ')

        # Pull out and preserve year
        year = None
        ym = RX['year'].search(work)
        if ym:
            year = ym.group(0)
            work = RX['year'].sub('', work)

        # Drop bracketed segments like [YTS], (1080p), etc.
        work = RX['brackets'].sub(' ', work)

        # Tokenize and filter out noise tokens
        raw_tokens = re.split(r'\s+', work)
        tokens = []
        for t in raw_tokens:
            if not t:
                continue
            lt = t.lower()
            # Numeric-only tokens: keep if they're likely part of the title
            if t.isdigit():
                # Drop resolutions and stray zero from audio patterns
                if t in {'480','576','720','1080','2160','4320'} or t == '0':
                    continue
                tokens.append(t)
                continue
            # Drop stray codec marker 'H' if it slipped through
            if lt == 'h':
                continue
            # Keep TV episode SxxExx tokens intact
            if RX['episode'].match(t):
                tokens.append(t)
                continue
            # Filter tokens matching noise categories
            if (
                RX['resolution'].match(t) or
                RX['source'].match(t) or
                RX['provider'].match(t) or
                RX['audio'].match(t) or
                RX['codec'].match(t) or
                RX['hdr'].match(t) or
                RX['tags'].match(t) or
                RX['lang'].match(t) or
                RX['site'].match(t)
            ):
                continue
            # Drop obvious release/scene leftover tokens
            if lt in {'webrip', 'webdl', 'bluray', 'hdrip', 'hdtv', 'dvdrip', 'proper', 'repack', 'remux'}:
                continue
            tokens.append(t)

        cleaned = ' '.join(tokens)
        # Remove trailing release group suffix like -RARBG
        cleaned = RX['group_suffix'].sub('', cleaned)
        # Remove number sequences
        cleaned = RX['numseq'].sub(' ', cleaned)
        # Collapse spaces
        cleaned = ' '.join(cleaned.split())

        # Add year back if found
        if year:
            cleaned = f"{cleaned} ({year})"

        # Truncate if too long
        if len(cleaned) > max_length:
            cleaned = cleaned[:max_length-3] + "..."

        return cleaned
