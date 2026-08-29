import logging
import asyncio
import requests
from typing import Dict, Optional
from urllib.parse import urljoin


STATUS_LABELS = {
    1: "Unknown",
    2: "Pending",
    3: "Processing",
    4: "Partially Available",
    5: "Available",
}


class OverseerrService:
    def __init__(self, base_url: Optional[str] = None, api_key: Optional[str] = None):
        """Initialize Overseerr/Jellyseerr service using config or provided settings.

        Args:
            base_url: Base URL of the Seerr instance, e.g. http://localhost:5055 (defaults to config)
            api_key: Seerr API key (defaults to config)
        """
        try:
            from ..config import Config
        except Exception:
            Config = None  # type: ignore
        raw_base_url = base_url or (getattr(Config, 'OVERSEERR_URL', '') if Config else '')
        self.api_key = api_key or (getattr(Config, 'OVERSEERR_API_KEY', '') if Config else '')
        self.logger = logging.getLogger(__name__)

        if raw_base_url:
            self.base_url = raw_base_url.rstrip('/') + '/api/v1/'
        else:
            self.base_url = None

    def is_configured(self) -> bool:
        """Check if Overseerr/Jellyseerr is properly configured"""
        return bool(self.base_url and self.api_key)

    def _headers(self) -> Dict[str, str]:
        return {"X-Api-Key": self.api_key, "Content-Type": "application/json"}

    async def test_connection(self) -> Dict:
        """Test connection to the Overseerr/Jellyseerr server"""
        if not self.is_configured():
            return {"success": False, "error": "Overseerr not configured (missing URL or API key)"}
        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: requests.get(urljoin(self.base_url, "status"), headers=self._headers(), timeout=10)
            )
            if response.status_code == 200:
                data = response.json()
                return {"success": True, "message": f"Connected to Overseerr v{data.get('version', 'unknown')}"}
            return {"success": False, "error": f"HTTP {response.status_code}: {response.text}"}
        except requests.exceptions.RequestException as e:
            return {"success": False, "error": f"Connection error: {str(e)}"}
        except Exception as e:
            return {"success": False, "error": f"Unexpected error: {str(e)}"}

    async def request_movie(self, tmdb_id: int) -> Dict:
        """Submit a movie request to Overseerr/Jellyseerr by TMDB ID.

        Returns:
            Dict with 'success' boolean and 'request_id'/'media_id', or 'error' string
            (Seerr's own error message is passed through, e.g. already requested/available).
        """
        if not self.is_configured():
            return {"success": False, "error": "Overseerr not configured (missing URL or API key)"}
        try:
            from ..config import Config
        except Exception:
            Config = None  # type: ignore
        server_id = getattr(Config, 'OVERSEERR_RADARR_SERVER_ID', None) if Config else None
        payload = {"mediaType": "movie", "mediaId": tmdb_id}
        if server_id is not None:
            payload["serverId"] = server_id
        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: requests.post(
                    urljoin(self.base_url, "request"),
                    headers=self._headers(),
                    json=payload,
                    timeout=15,
                )
            )
            if response.status_code in (200, 201):
                data = response.json()
                return {
                    "success": True,
                    "request_id": data.get("id"),
                    "media_id": (data.get("media") or {}).get("id"),
                }
            try:
                err = response.json().get("message") or response.text
            except Exception:
                err = response.text
            return {"success": False, "error": f"{err} (HTTP {response.status_code})"}
        except requests.exceptions.RequestException as e:
            return {"success": False, "error": f"Connection error: {str(e)}"}
        except Exception as e:
            return {"success": False, "error": f"Unexpected error: {str(e)}"}

    async def get_movie_status(self, tmdb_id: int) -> Dict:
        """Look up a movie's current media status on Overseerr/Jellyseerr by TMDB ID.

        Returns:
            Dict with 'success' boolean, 'status' (raw Seerr mediaInfo.status int, or None
            if the movie has never been requested/added), and 'available' (status == 5).
            'error' string on failure (network error, non-200, etc).
        """
        if not self.is_configured():
            return {"success": False, "error": "Overseerr not configured (missing URL or API key)"}
        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: requests.get(urljoin(self.base_url, f"movie/{tmdb_id}"), headers=self._headers(), timeout=10)
            )
            if response.status_code != 200:
                return {"success": False, "error": f"HTTP {response.status_code}: {response.text}"}
            data = response.json()
            status = (data.get("mediaInfo") or {}).get("status")
            return {"success": True, "status": status, "available": status == 5}
        except requests.exceptions.RequestException as e:
            return {"success": False, "error": f"Connection error: {str(e)}"}
        except Exception as e:
            return {"success": False, "error": f"Unexpected error: {str(e)}"}
