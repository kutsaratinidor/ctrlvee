from typing import Optional, Dict, Any
import os
import xml.etree.ElementTree as ET
import requests
import urllib.parse
import logging
from urllib.parse import quote

class VLCError(Exception):
    """Base exception for VLC controller errors"""
    pass

class VLCConnectionError(VLCError):
    """Raised when there's an error connecting to VLC"""
    pass

class VLCCommandError(VLCError):
    """Raised when a VLC command fails"""
    pass

class VLCController:
    """Controller for VLC media player HTTP interface"""
    
    def __init__(self, host=None, port=None, password=None):
        """Initialize VLC controller using config or provided values
        
        Args:
            host: VLC host address (defaults to config)
            port: VLC HTTP interface port (defaults to config)
            password: VLC HTTP interface password (defaults to config)
        """
        from ..config import Config
        self.host = host or Config.VLC_HOST
        self.port = port or Config.VLC_PORT
        self.password = password or Config.VLC_PASSWORD
        self.logger = logging.getLogger(__name__)
        
    def send_command(self, command: str, params: Optional[Dict[str, str]] = None) -> Optional[ET.Element]:
        """Send command to VLC HTTP interface
        
        Args:
            command: The VLC command to send
            params: Optional command parameters
            
        Returns:
            ElementTree root element of response XML or None on failure
        """
        base_url = f"http://{self.host}:{self.port}/requests/status.xml"
        
        try:
            # Build parameters dict starting with the command
            all_params = {"command": command}
            if params:
                # Add additional parameters directly to the params dict
                all_params.update(params)
            
            full_url = f"{base_url}?{urllib.parse.urlencode(all_params)}"
            self.logger.debug(f"Sending VLC command: {full_url}")
            
            response = requests.get(
                base_url,
                params=all_params,
                auth=('', self.password),
                timeout=5
            )
            
            self.logger.debug(f"VLC response status code: {response.status_code}")
            self.logger.debug(f"VLC response content: {response.content[:200]}...")
            
            if response.status_code == 200:
                return ET.fromstring(response.content)
            elif response.status_code == 401:
                self.logger.error(f"Authentication failed. Using password: {self.password[:3]}...")
                return None
            else:
                self.logger.warning(f"VLC request failed with status {response.status_code}")
            return None
        except Exception as e:
            self.logger.error(f"VLC request failed with error: {str(e)}")
            return None
        
    def get_playlist(self):
        """Get the current VLC playlist"""
        try:
            response = requests.get(
                f"http://{self.host}:{self.port}/requests/playlist.xml",
                auth=('', self.password),
                timeout=5
            )
            if response.status_code == 200:
                return ET.fromstring(response.content)
        except Exception as e:
            self.logger.error(f"Error getting playlist: {e}")
            return None
            
    def play(self):
        """Start or resume playback"""
        return self.send_command("pl_play")
        
    def pause(self):
        """Pause playback"""
        return self.send_command("pl_pause")
        
    def stop(self):
        """Stop playback"""
        return self.send_command("pl_stop")
        
    def next(self):
        """Play next track"""
        return self.send_command("pl_next")
        
    def previous(self):
        """Play previous track"""
        return self.send_command("pl_previous")
        
    def seek(self, seconds):
        """Seek to a specific position"""
        return self.send_command("seek", {"val": str(seconds)})

    def get_status(self):
        """Get current VLC status with enhanced metadata"""
        status_xml = self.send_command("status")
        if not status_xml:
            return None
            
        # Get the playlist to find current item's position
        playlist = self.get_playlist()
        if playlist:
            current_item = None
            current_position = None
            total_items = 0
            
            # Find the current item and count total items
            for i, item in enumerate(playlist.findall('.//leaf')):
                total_items += 1
                if item.get('current'):
                    current_item = item
                    current_position = i + 1  # 1-based position
                    
            if current_item:
                # Add playlist position info to the status XML
                pos_elem = ET.Element('playlist_position')
                pos_elem.text = f"{current_position}/{total_items}"
                status_xml.append(pos_elem)
                
        return status_xml
        
    def play_item(self, item_id):
        """Play a specific item from the playlist"""
        # Send direct play command with the item id
        return self.send_command("pl_play", {"id": str(item_id)})
