import logging
import requests
import asyncio
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from urllib.parse import urljoin


class RadarrService:
    def __init__(self, host: str = None, port: int = None, api_key: str = None, use_ssl: bool = None):
        """Initialize Radarr service using config or provided settings
        
        Args:
            host: Radarr server host (defaults to config)
            port: Radarr server port (defaults to config)
            api_key: Radarr API key (defaults to config)
            use_ssl: Whether to use HTTPS (defaults to config)
        """
        from ..config import Config
        self.host = host or Config.RADARR_HOST
        self.port = port or Config.RADARR_PORT
        self.api_key = api_key or Config.RADARR_API_KEY
        self.use_ssl = use_ssl if use_ssl is not None else Config.RADARR_USE_SSL
        self.logger = logging.getLogger(__name__)
        
        # Construct base URL
        protocol = "https" if self.use_ssl else "http"
        if self.host and self.port:
            self.base_url = f"{protocol}://{self.host}:{self.port}/api/v3/"
        else:
            self.base_url = None
    
    def is_configured(self) -> bool:
        """Check if Radarr is properly configured"""
        return bool(self.host and self.api_key and self.base_url)
    
    async def test_connection(self) -> Dict[str, any]:
        """Test connection to Radarr server
        
        Returns:
            Dict with 'success' boolean and 'message' or 'error' string
        """
        if not self.is_configured():
            return {
                "success": False,
                "error": "Radarr not configured (missing host or API key)"
            }
        
        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None, 
                lambda: requests.get(
                    urljoin(self.base_url, "system/status"),
                    headers={"X-Api-Key": self.api_key},
                    timeout=10
                )
            )
            
            if response.status_code == 200:
                data = response.json()
                return {
                    "success": True,
                    "message": f"Connected to Radarr v{data.get('version', 'unknown')}"
                }
            else:
                return {
                    "success": False,
                    "error": f"HTTP {response.status_code}: {response.text}"
                }
                
        except requests.exceptions.RequestException as e:
            return {
                "success": False,
                "error": f"Connection error: {str(e)}"
            }
        except Exception as e:
            return {
                "success": False,
                "error": f"Unexpected error: {str(e)}"
            }
    
    async def get_recent_downloads(self, days: int = 7, limit: int = 20) -> Dict[str, any]:
        """Get recently downloaded movies from Radarr
        
        Args:
            days: Number of days back to look for downloads
            limit: Maximum number of movies to return
            
        Returns:
            Dict with 'success' boolean and 'movies' list or 'error' string
        """
        if not self.is_configured():
            return {
                "success": False,
                "error": "Radarr not configured (missing host or API key)"
            }
        
        try:
            # Calculate date range
            end_date = datetime.now()
            start_date = end_date - timedelta(days=days)
            
            loop = asyncio.get_event_loop()
            
            # Get movie list
            response = await loop.run_in_executor(
                None,
                lambda: requests.get(
                    urljoin(self.base_url, "movie"),
                    headers={"X-Api-Key": self.api_key},
                    timeout=15
                )
            )
            
            if response.status_code != 200:
                return {
                    "success": False,
                    "error": f"Failed to fetch movies: HTTP {response.status_code}"
                }
            
            movies = response.json()
            
            # Filter for recently downloaded movies
            recent_movies = []
            for movie in movies:
                # Check if movie has a file and was downloaded recently
                if not movie.get('hasFile', False):
                    continue
                
                # Check download date from movieFile
                movie_file = movie.get('movieFile')
                if not movie_file:
                    continue
                
                date_added = movie_file.get('dateAdded')
                if not date_added:
                    continue
                
                try:
                    # Parse ISO date string
                    download_date = datetime.fromisoformat(date_added.replace('Z', '+00:00'))
                    # Convert to local timezone for comparison
                    download_date = download_date.replace(tzinfo=None)
                    
                    if start_date <= download_date <= end_date:
                        # Safely extract rating
                        rating = None
                        ratings = movie.get('ratings')
                        if isinstance(ratings, dict):
                            imdb_rating = ratings.get('imdb')
                            if isinstance(imdb_rating, dict):
                                rating = imdb_rating.get('value')
                        
                        # Safely extract quality
                        quality = 'Unknown'
                        quality_obj = movie_file.get('quality')
                        if isinstance(quality_obj, dict):
                            quality_inner = quality_obj.get('quality')
                            if isinstance(quality_inner, dict):
                                quality = quality_inner.get('name', 'Unknown')
                        
                        # Safely extract genres
                        genres = []
                        movie_genres = movie.get('genres', [])
                        if isinstance(movie_genres, list):
                            genres = [genre.get('name') for genre in movie_genres if isinstance(genre, dict) and genre.get('name')]
                        
                        recent_movies.append({
                            'id': movie.get('id'),
                            'title': movie.get('title'),
                            'year': movie.get('year'),
                            'overview': movie.get('overview', ''),
                            'rating': rating,
                            'runtime': movie.get('runtime'),
                            'genres': genres,
                            'file_path': movie_file.get('path'),
                            'file_size': movie_file.get('size'),
                            'quality': quality,
                            'download_date': download_date.isoformat(),
                            'imdb_id': movie.get('imdbId'),
                            'tmdb_id': movie.get('tmdbId')
                        })
                except (ValueError, TypeError) as e:
                    self.logger.debug(f"Could not parse date for movie {movie.get('title')}: {e}")
                    continue
            
            # Sort by download date (most recent first) and limit results
            recent_movies.sort(key=lambda x: x['download_date'], reverse=True)
            recent_movies = recent_movies[:limit]
            
            return {
                "success": True,
                "movies": recent_movies,
                "total_found": len(recent_movies),
                "days_searched": days
            }
            
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Request error getting recent downloads: {e}")
            return {
                "success": False,
                "error": f"Connection error: {str(e)}"
            }
        except Exception as e:
            self.logger.error(f"Error getting recent downloads: {e}")
            return {
                "success": False,
                "error": f"Unexpected error: {str(e)}"
            }
    
    async def get_movie_details(self, movie_id: int) -> Dict[str, any]:
        """Get detailed information about a specific movie
        
        Args:
            movie_id: Radarr movie ID
            
        Returns:
            Dict with 'success' boolean and 'movie' dict or 'error' string
        """
        if not self.is_configured():
            return {
                "success": False,
                "error": "Radarr not configured (missing host or API key)"
            }
        
        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: requests.get(
                    urljoin(self.base_url, f"movie/{movie_id}"),
                    headers={"X-Api-Key": self.api_key},
                    timeout=10
                )
            )
            
            if response.status_code == 200:
                movie = response.json()
                return {
                    "success": True,
                    "movie": movie
                }
            else:
                return {
                    "success": False,
                    "error": f"Movie not found: HTTP {response.status_code}"
                }
                
        except Exception as e:
            return {
                "success": False,
                "error": f"Error fetching movie details: {str(e)}"
            }