from typing import Optional, Dict, Any
import os
import json
import xml.etree.ElementTree as ET
import requests
import time
import logging
from urllib.parse import quote, urlencode

class VLCError(Exception):
    """Base exception for VLC controller errors"""
    pass

class VLCPlaylistError(VLCError):
    """Exception for VLC playlist-related errors"""
    pass

class VLCController:
    """Controller for VLC HTTP interface"""
    
    def __init__(self, host: Optional[str] = None, port: Optional[int] = None, 
                 password: Optional[str] = None, queue_backup_file: str = "queue_backup.json"):
        """Initialize VLC controller
        
        Args:
            host: VLC HTTP interface host (defaults to config)
            port: VLC HTTP interface port (defaults to config)
            password: VLC HTTP interface password (defaults to config)
            queue_backup_file: Path to queue backup file for persistence
        """
        from ..config import Config
        self.host = host or Config.VLC_HOST
        self.port = port or Config.VLC_PORT
        self.password = password or Config.VLC_PASSWORD
        self.queue_backup_file = queue_backup_file
        self.logger = logging.getLogger(__name__)
        
        # Queue management state
        self._queued_items = {}  # item_id -> queue_info
        self._shuffle_restore_queue = []  # List of items that need shuffle restored after playing
        
        # Load queue state from backup file
        self._load_queue_backup()

    def _load_queue_backup(self):
        """Load queue state from backup file"""
        try:
            if os.path.exists(self.queue_backup_file) and os.path.getsize(self.queue_backup_file) > 0:
                with open(self.queue_backup_file, 'r') as f:
                    backup_data = json.load(f)
                    
                # Convert old format to new format if needed
                if isinstance(backup_data, list) and backup_data and 'item_id' in backup_data[0]:
                    # Old format from previous queue system
                    self.logger.info("Found old queue format - clearing and starting fresh with new format")
                    self._queued_items = {}
                    self._shuffle_restore_queue = []
                    self._save_queue_backup()  # Save empty new format
                elif isinstance(backup_data, dict):
                    # New format
                    self._queued_items = backup_data.get('queued_items', {})
                    self._shuffle_restore_queue = backup_data.get('shuffle_restore_queue', [])
                    self.logger.info(f"Loaded queue backup: {len(self._queued_items)} queued items, {len(self._shuffle_restore_queue)} pending shuffle restores")
                else:
                    # Empty or invalid format
                    self._queued_items = {}
                    self._shuffle_restore_queue = []
        except Exception as e:
            self.logger.error(f"Error loading queue backup: {e}")
            self._queued_items = {}
            self._shuffle_restore_queue = []

    def _save_queue_backup(self):
        """Save current queue state to backup file"""
        try:
            backup_data = {
                'queued_items': dict(self._queued_items),
                'shuffle_restore_queue': list(self._shuffle_restore_queue),
                'backup_timestamp': __import__('time').time()
            }
            with open(self.queue_backup_file, 'w') as f:
                json.dump(backup_data, f, indent=2)
            self.logger.debug("Queue backup saved successfully")
        except Exception as e:
            self.logger.error(f"Error saving queue backup: {e}")

    def send_command(self, command: str, params: Optional[Dict[str, str]] = None) -> Optional[ET.Element]:
        """Send command to VLC HTTP interface
        
        Args:
            command: The VLC command to send
            params: Optional command parameters
            
        Returns:
            ElementTree root element of response XML or None on failure
        """
        # Build parameters dict starting with the command
        all_params = {"command": command}
        if params:
            all_params.update(params)
        
        return self._make_request("status.xml", all_params)
    
    def _make_request(self, endpoint: str, params: Optional[Dict[str, str]] = None) -> Optional[ET.Element]:
        """Make a HTTP request to VLC interface with consistent error handling
        
        Args:
            endpoint: The endpoint path (e.g., 'status.xml', 'playlist.xml')
            params: Optional query parameters
            
        Returns:
            ElementTree root element of response XML or None on failure
        """
        try:
            url = f"http://{self.host}:{self.port}/requests/{endpoint}"
            
            response = requests.get(
                url,
                params=params,
                auth=('', self.password),
                timeout=5
            )
            
            self.logger.debug(f"VLC {endpoint} response code: {response.status_code}")
            
            if response.status_code == 200:
                return ET.fromstring(response.content)
            elif response.status_code == 401:
                self.logger.error(f"Authentication failed for {endpoint}. Using password: {self.password[:3]}...")
                return None
            else:
                self.logger.warning(f"VLC {endpoint} request failed with status {response.status_code}")
                return None
        except requests.exceptions.ConnectionError:
            self.logger.debug(f"Could not connect to VLC HTTP interface for {endpoint}")
            return None
        except requests.exceptions.Timeout:
            self.logger.warning(f"VLC {endpoint} request timed out")
            return None
        except ET.ParseError as e:
            self.logger.error(f"Failed to parse VLC {endpoint} XML: {e}")
            return None
        except Exception as e:
            self.logger.error(f"Unexpected error getting VLC {endpoint}: {e}")
            return None

    def _parse_playlist_info(self, playlist=None):
        """Parse playlist XML to extract item information
        
        Args:
            playlist: Optional playlist XML element. If None, fetches current playlist.
            
        Returns:
            dict: Mapping of item_id -> {name, position, is_current}
        """
        if playlist is None:
            playlist = self.get_playlist()
        
        if not playlist:
            return {}
        
        playlist_map = {}
        current_item_id = None
        
        for idx, item in enumerate(playlist.findall('.//leaf'), 1):
            item_id = item.get('id')
            item_name = item.get('name', 'Unknown')
            is_current = item.get('current') is not None
            
            if item_id:
                playlist_map[item_id] = {
                    'name': item_name,
                    'position': idx,
                    'is_current': is_current
                }
                
                if is_current:
                    current_item_id = item_id
        
        return playlist_map, current_item_id
        
    def get_playlist(self):
        """Get the current VLC playlist"""
        return self._make_request("playlist.xml")

    def export_playlist(self) -> Optional[list]:
        """Export current playlist to a list of dicts with basic fields.

        Returns a list like:
            [{ 'id': '123', 'name': 'Title', 'current': True/False }]

        Returns None if playlist cannot be fetched.
        """
        try:
            playlist = self.get_playlist()
            if not playlist:
                return None
            items = []
            for item in playlist.findall('.//leaf'):
                items.append({
                    'id': item.get('id'),
                    'name': item.get('name', ''),
                    'current': (item.get('current') is not None)
                })
            return items
        except Exception as e:
            self.logger.error(f"Failed to export playlist: {e}")
            return None

    def export_playlist_xspf(self) -> Optional[str]:
        """Export current playlist in XSPF format (as a UTF-8 XML string).

        VLC can load this .xspf file directly.
        """
        try:
            playlist = self.get_playlist()
            if not playlist:
                return None

            ns = "http://xspf.org/ns/0/"
            ET.register_namespace('', ns)
            pl_el = ET.Element(ET.QName(ns, 'playlist'), version="1")
            title_el = ET.SubElement(pl_el, ET.QName(ns, 'title'))
            title_el.text = "CtrlVee Playlist Export"
            tl_el = ET.SubElement(pl_el, ET.QName(ns, 'trackList'))

            count = 0
            for leaf in playlist.findall('.//leaf'):
                uri = leaf.get('uri')
                name = leaf.get('name', '')

                # Fallback: if no uri but name looks like an absolute existing path, convert to file URI
                if not uri and name:
                    try:
                        import pathlib
                        p = pathlib.Path(name)
                        if p.is_absolute() and p.exists():
                            uri = p.resolve().as_uri()
                    except Exception:
                        pass

                if not uri:
                    # Skip items without a resolvable URI
                    continue

                tr_el = ET.SubElement(tl_el, ET.QName(ns, 'track'))
                loc_el = ET.SubElement(tr_el, ET.QName(ns, 'location'))
                loc_el.text = uri
                if name:
                    t_el = ET.SubElement(tr_el, ET.QName(ns, 'title'))
                    t_el.text = name
                count += 1

            # Always return a valid XSPF document, even if empty
            return ET.tostring(pl_el, encoding='utf-8', xml_declaration=True).decode('utf-8')
        except Exception as e:
            self.logger.error(f"Failed to export playlist as XSPF: {e}")
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

    def get_status(self, enhanced: bool = False):
        """Get current VLC status
        
        Args:
            enhanced: If True, includes playlist position metadata
            
        Returns:
            ElementTree root element of status XML or None on failure
        """
        if enhanced:
            return self._get_enhanced_status()
        return self._make_request("status.xml")
    
    def _get_enhanced_status(self):
        """Get current VLC status with enhanced metadata"""
        status_xml = self.send_command("status")
        if not status_xml:
            return None
            
        # Get playlist information
        playlist_map, current_item_id = self._parse_playlist_info()
        
        if current_item_id and current_item_id in playlist_map:
            current_info = playlist_map[current_item_id]
            total_items = len(playlist_map)
            
            # Add playlist position info to the status XML
            pos_elem = ET.Element('playlist_position')
            pos_elem.text = f"{current_info['position']}/{total_items}"
            status_xml.append(pos_elem)
                
        return status_xml
        
    def play_item(self, item_id):
        """Play a specific item from the playlist"""
        # Send direct play command with the item id
        return self.send_command("pl_play", {"id": str(item_id)})
    
    def get_shuffle_state(self):
        """Check if shuffle is currently enabled"""
        status = self.get_status()
        if status:
            random_elem = status.find('random')
            return random_elem is not None and random_elem.text == 'true'
        return False
    
    def toggle_shuffle(self):
        """Toggle shuffle mode on/off"""
        return self.send_command("pl_random")
    
    def get_repeat_state(self):
        """Check current repeat mode"""
        status = self.get_status()
        if status:
            repeat_elem = status.find('repeat')
            loop_elem = status.find('loop')
            if repeat_elem is not None and repeat_elem.text == 'true':
                return 'one'  # Repeat current item
            elif loop_elem is not None and loop_elem.text == 'true':
                return 'all'  # Repeat playlist
        return 'none'
    
    def move_item_to_position(self, item_id, position):
        """Move a playlist item to a specific position (0-based)
        
        Note: VLC 3.x has limited playlist modification support via HTTP interface.
        This method attempts to move items but may not work on all VLC versions.
        The soft queue system provides a fallback for reliable queueing.
        """
        result = self.send_command("pl_move", {"id": str(item_id), "psn": str(position)})
        if result is not None:
            self.logger.debug(f"Move command sent for item {item_id} to position {position}")
            return True
        return False
    
    def play_item_by_id(self, item_id):
        """Play a specific playlist item by its ID"""
        result = self.send_command("pl_play", {"id": str(item_id)})
        if result is not None:
            self.logger.debug(f"Play command sent for item {item_id}")
            return True
        return False

    def set_rate(self, rate: float) -> bool:
        """Set VLC playback rate/speed.

        Args:
            rate: Playback rate (e.g., 1.0, 1.5, 0.5)

        Returns:
            True if the command was successfully sent (response not None), False otherwise.
        """
        try:
            # VLC HTTP interface accepts a 'rate' command with 'val' parameter
            res = self.send_command('rate', {'val': str(float(rate))})
            if res is not None:
                self.logger.info(f"Set VLC playback rate to {rate}")
                return True
            else:
                self.logger.debug(f"VLC did not return a response when setting rate to {rate}")
                return False
        except Exception as e:
            self.logger.error(f"Error setting VLC rate to {rate}: {e}")
            return False
    
    def get_current_position(self):
        """Get the current item's position in the playlist"""
        playlist_map, current_item_id = self._parse_playlist_info()
        
        if current_item_id and current_item_id in playlist_map:
            return playlist_map[current_item_id]['position'] - 1  # Return 0-based position
        
        return None
    
    def queue_item_next(self, item_id, restore_shuffle=True):
        """
        Queue an item to play next using soft queue system (VLC 3.x compatible)
        
        This method doesn't physically move items in the playlist but tracks
        what should play next and handles it when tracks finish playing.
        
        Args:
            item_id: The playlist item ID to queue
            restore_shuffle: Whether to restore shuffle after the queued item plays
            
        Returns:
            dict: Status information about the queuing operation
        """
        try:
            # Get current state
            shuffle_was_on = self.get_shuffle_state()
            current_pos = self.get_current_position()
            
            if current_pos is None:
                return {"success": False, "error": "Could not determine current position"}
            
            self.logger.info(f"Soft-queuing item {item_id} to play next (shuffle was {'on' if shuffle_was_on else 'off'})")
            
            # If shuffle is on and this is the first queued item, temporarily disable it
            if shuffle_was_on and len(self._queued_items) == 0:
                self.toggle_shuffle()
            
            # For soft queue, we don't move items physically
            # Instead, we track the queue order in memory
            existing_queue_count = len(self._queued_items)
            queue_order = existing_queue_count + 1
            
            # Get item info for display
            playlist_map, _ = self._parse_playlist_info()
            item_name = "Unknown"
            
            if str(item_id) in playlist_map:
                item_name = playlist_map[str(item_id)]['name']
            
            # Track this queued item for soft queue management
            # For shuffle restoration: if shuffle was on when we started queuing,
            # the LAST item in the queue should restore it (not just the first)
            should_restore_shuffle = False
            if shuffle_was_on and len(self._queued_items) == 0:
                # This is the first item and shuffle was on - mark for restoration
                should_restore_shuffle = True
            elif len(self._queued_items) > 0:
                # Check if any existing item in the queue is marked for shuffle restoration
                for existing_item in self._queued_items.values():
                    if existing_item.get("restore_shuffle", False):
                        should_restore_shuffle = True
                        break
            
            queue_info = {
                "item_id": item_id,
                "item_name": item_name,
                "queue_order": queue_order,
                "shuffle_was_on": shuffle_was_on,
                "restore_shuffle": should_restore_shuffle,
                "queued_at_time": __import__('time').time(),
                "queue_type": "soft"  # Indicates this is a soft queue item
            }
            
            self._queued_items[item_id] = queue_info
            
            # Update all existing items to NOT restore shuffle (only the last one should)
            if should_restore_shuffle and len(self._queued_items) > 1:
                for existing_id, existing_item in self._queued_items.items():
                    if existing_id != item_id:  # Don't update the item we just added
                        existing_item["restore_shuffle"] = False
            
            # Add to shuffle restore queue if needed
            if shuffle_was_on and restore_shuffle:
                self._shuffle_restore_queue.append(item_id)
            
            # Save queue state to backup file
            self._save_queue_backup()
            
            result = {
                "success": True,
                "item_id": item_id,
                "item_name": item_name,
                "queue_order": queue_order,
                "total_queued": len(self._queued_items)
            }
            
            return result
            
        except Exception as e:
            self.logger.error(f"Error soft-queuing item: {e}")
            return {"success": False, "error": str(e)}
    
    def restore_shuffle_if_needed(self, was_shuffle_on):
        """
        Restore shuffle mode if it was previously enabled
        This should be called after a queued item finishes playing
        """
        if was_shuffle_on and not self.get_shuffle_state():
            self.logger.info("Restoring shuffle mode after queued item finished")
            return self.toggle_shuffle()
        return True
    
    def get_next_queued_item(self):
        """Get the next item that should play from the soft queue
        
        Returns:
            dict: Next queue item info or None if no items queued
        """
        if not self._queued_items:
            return None
        
        # Sort queued items by queue order to get the next one
        sorted_items = sorted(
            self._queued_items.items(), 
            key=lambda x: x[1].get('queue_order', 0)
        )
        
        if sorted_items:
            next_item_id, next_item_info = sorted_items[0]
            return {
                "item_id": next_item_id,
                "item_name": next_item_info.get("item_name", "Unknown"),
                "queue_order": next_item_info.get("queue_order", 0),
                "restore_shuffle": next_item_info.get("restore_shuffle", False)
            }
        
        return None
    
    def play_next_queued_item(self):
        """Play the next item from the soft queue
        
        Returns:
            dict: Result of playing the next queued item
        """
        next_item = self.get_next_queued_item()
        if not next_item:
            return {"success": False, "error": "No items in queue"}
        
        item_id = next_item["item_id"]
        item_name = next_item["item_name"]
        
        # Check if this is the last item in the queue before playing it
        remaining_queue_items = len(self._queued_items)
        should_restore_shuffle = next_item.get("restore_shuffle", False)
        
        # Play the item
        success = self.play_item_by_id(item_id)
        
        if success:
            # If this was the last item in the queue and it needs shuffle restored,
            # restore shuffle immediately when it starts playing (not when it ends)
            if remaining_queue_items == 1 and should_restore_shuffle:
                current_shuffle = self.get_shuffle_state()
                if not current_shuffle:
                    self.logger.info(f"Restoring shuffle mode - last queued item ({item_name}) is now playing")
                    self.toggle_shuffle()
            
            # Remove the item from the queue since we triggered it to play
            # (This will also clear shuffle restore queue if queue becomes empty)
            self._remove_from_queue(item_id)
            
            self.logger.info(f"Playing next queued item: {item_name} (ID: {item_id}) - removed from queue")
            return {
                "success": True,
                "item_id": item_id,
                "item_name": item_name,
                "queue_order": next_item["queue_order"]
            }
        else:
            self.logger.error(f"Failed to play queued item: {item_name} (ID: {item_id})")
            return {"success": False, "error": f"Failed to play item {item_id}"}

    def check_and_handle_queue_transition(self, current_item_id):
        """
        Check if we need to handle soft queue transitions and shuffle restoration
        This should be called from the monitoring system when track changes
        
        For soft queue: When a track ends naturally, we need to check if there's
        a queued item that should play next and trigger it.
        
        Args:
            current_item_id: The ID of the item that just started playing
            
        Returns:
            dict: Information about any queue transitions that occurred
        """
        transitions = []
        
        # First, check if the current item is one we were tracking
        # If so, it means it just started playing (either queued or natural progression)
        current_is_queued = current_item_id in self._queued_items
        
        # For soft queue, we need different logic:
        # 1. If current item is a queued item, remove it from tracking
        # 2. Check if there are more items in the queue to play
        
        if current_is_queued:
            # This queued item just started playing
            if current_item_id in self._queued_items:
                queue_info = self._queued_items[current_item_id]
                item_name = queue_info.get("item_name", "Unknown")
                
                self.logger.info(f"Soft-queued item now playing: {item_name} (ID: {current_item_id})")
                
                # Use the centralized removal method
                self._remove_from_queue(current_item_id)
                
                transitions.append({
                    "action": "queued_item_started",
                    "item_id": current_item_id,
                    "item_name": item_name,
                    "success": True
                })
            else:
                self.logger.debug(f"Queued item {current_item_id} already removed from queue")
        
        # Check if we should play the next queued item
        # This happens when the current track is about to end or has ended
        next_queued = self.get_next_queued_item()
        if next_queued and not current_is_queued:
            # There's a next item to play and current item is not a queued item
            # This suggests the previous track ended naturally
            
            play_result = self.play_next_queued_item()
            if play_result.get("success"):
                transitions.append({
                    "action": "auto_play_next_queued",
                    "item_id": play_result["item_id"],
                    "item_name": play_result["item_name"],
                    "success": True
                })
                self.logger.info(f"Automatically playing next queued item: {play_result['item_name']}")
        
        # Save backup after any queue state changes (if any transitions occurred)
        if transitions:
            self._save_queue_backup()
        
        return {
            "transitions": transitions,
            "active_queue_items": len(self._queued_items),
            "pending_shuffle_restores": len(self._shuffle_restore_queue)
        }
    
    def get_queue_status(self):
        """Get current queue status information"""
        return {
            "queued_items": dict(self._queued_items),
            "shuffle_restore_queue": list(self._shuffle_restore_queue),
            "shuffle_currently_on": self.get_shuffle_state()
        }
    
    def clear_queue_tracking(self):
        """Clear all queue tracking state (useful for reset/cleanup)"""
        self._queued_items.clear()
        self._shuffle_restore_queue.clear()  # Also clear shuffle restore queue
        self._save_queue_backup()
        self.logger.info("Cleared all queue tracking state including shuffle restore queue")

    def remove_from_queue_by_order(self, queue_order: int):
        """Remove a queued item by its queue order number.

        Returns dict with success and optional details.
        """
        try:
            target_id = None
            for item_id, info in self._queued_items.items():
                if info.get('queue_order') == int(queue_order):
                    target_id = item_id
                    break
            if not target_id:
                return {"success": False, "error": f"Queue order {queue_order} not found"}
            name = self._queued_items[target_id].get('item_name', 'Unknown')
            self._remove_from_queue(target_id)
            return {"success": True, "removed_item_id": target_id, "item_name": name}
        except Exception as e:
            self.logger.error(f"remove_from_queue_by_order error: {e}")
            return {"success": False, "error": str(e)}

    def remove_from_queue_by_playlist_number(self, number: int):
        """Remove a queued item by its playlist number (1-based)."""
        try:
            playlist_map, _ = self._parse_playlist_info()
            # Find item_id with matching position
            target_id = None
            for item_id, info in playlist_map.items():
                if info.get('position') == int(number):
                    target_id = item_id
                    break
            if not target_id:
                return {"success": False, "error": f"Playlist number {number} not found"}
            if target_id not in self._queued_items:
                return {"success": False, "error": f"Playlist #{number} is not queued"}
            name = self._queued_items[target_id].get('item_name', playlist_map[target_id]['name'])
            self._remove_from_queue(target_id)
            return {"success": True, "removed_item_id": target_id, "item_name": name}
        except Exception as e:
            self.logger.error(f"remove_from_queue_by_playlist_number error: {e}")
            return {"success": False, "error": str(e)}
    
    def enqueue_item(self, item_id):
        """Add an item to the end of the playlist"""
        # VLC's enqueue command - adds to end of playlist
        return self.send_command("in_enqueue", {"id": str(item_id)})

    def enqueue_path(self, file_path: str) -> bool:
        """Add a local file path to the end of the playlist.

        Uses VLC HTTP command 'in_enqueue' with 'input' parameter.
        Escapes path as a file URI.
        """
        try:
            # Convert to file URI using pathlib for correctness
            import pathlib
            p = pathlib.Path(file_path).resolve()
            uri = p.as_uri()
            result = self.send_command("in_enqueue", {"input": uri})
            return result is not None
        except Exception as e:
            self.logger.error(f"enqueue_path failed for {file_path}: {e}")
            return False
    
    def smart_queue(self, item_id, behavior="auto"):
        """
        Intelligently queue an item based on current state and user preference
        
        Args:
            item_id: The playlist item ID to queue
            behavior: Queuing behavior
                - "auto": Smart default behavior
                - "play_now": Play immediately
                - "queue_next": Queue to play next (disable shuffle temporarily)
                - "add_to_end": Add to end of playlist
        
        Returns:
            dict: Result of the queuing operation
        """
        shuffle_on = self.get_shuffle_state()
        
        if behavior == "play_now":
            return {
                "success": bool(self.play_item(item_id)),
                "action": "played_immediately",
                "shuffle_affected": False
            }
        elif behavior == "add_to_end":
            return {
                "success": bool(self.enqueue_item(item_id)),
                "action": "added_to_end",
                "shuffle_affected": False
            }
        elif behavior == "queue_next":
            return self.queue_item_next(item_id)
        else:  # "auto"
            return self.queue_item_next(item_id)

    def _remove_from_queue(self, item_id):
        """
        Remove an item from the queue and handle shuffle restoration if needed
        
        Args:
            item_id: The item ID to remove from the queue
        """
        if item_id not in self._queued_items:
            self.logger.debug(f"Item {item_id} not in queue - nothing to remove")
            return
        
        # Get the item info before removing it
        queue_info = self._queued_items[item_id]
        should_restore_shuffle = queue_info.get("restore_shuffle", False)
        
        # Remove from the main queue
        del self._queued_items[item_id]
        self.logger.debug(f"Removed item {item_id} from queue")
        
        # If the queue is now empty, clear the shuffle restore queue too
        if len(self._queued_items) == 0:
            self._shuffle_restore_queue.clear()
            self.logger.debug("Queue is empty - cleared shuffle restore queue")
        
        # Save the updated queue state
        self._save_queue_backup()
        self.logger.debug(f"Queue state saved after removing item {item_id}")

    def _handle_queued_item_finished(self, item_id):
        """
        Handle when a queued item finishes playing (track ends naturally)
        
        Note: Shuffle restoration now happens when the LAST item STARTS playing,
        not when it finishes. This method mainly handles cleanup.
        
        Args:
            item_id: The item ID that just finished playing
        """
        if item_id not in self._shuffle_restore_queue:
            self.logger.debug(f"Item {item_id} finished but was not in shuffle restore queue")
            return
        
        # Remove from shuffle restore queue since this item finished
        # (shuffle should have already been restored when the last item started playing)
        self._shuffle_restore_queue.remove(item_id)
        self.logger.debug(f"Removed finished item {item_id} from shuffle restore queue")
        
        # Note: We no longer restore shuffle here - it happens when the last item starts playing
        # This prevents the delay between queue finishing and shuffle being restored
        
        # Save the updated state
        self._save_queue_backup()


# Error classes for VLC operations
class VLCConnectionError(VLCError):
    """Raised when there's an error connecting to VLC"""
    pass

class VLCCommandError(VLCError):
    """Raised when a VLC command fails"""
    pass
