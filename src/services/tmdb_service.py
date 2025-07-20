import os
import logging
import discord
import tmdbsimple as tmdb

class TMDBService:
    def __init__(self, api_key=None):
        """Initialize TMDB service using config or provided API key
        
        Args:
            api_key: TMDB API key (defaults to config)
        """
        from ..config import Config
        self.api_key = api_key or Config.TMDB_API_KEY
        self.logger = logging.getLogger(__name__)
        if self.api_key:
            tmdb.API_KEY = self.api_key

    def get_movie_metadata(self, title):
        """Get movie metadata from TMDB"""
        if not self.api_key:
            self.logger.warning("No TMDB API key found")
            return None
        
        try:
            self.logger.debug(f"Searching TMDB for title: {title}")
            search = tmdb.Search()
            response = search.movie(query=title)
            
            if not response['results']:
                self.logger.debug(f"No results found for: {title}")
                return None
            
            self.logger.debug(f"Found movie: {response['results'][0]['title']}")
                
            movie = response['results'][0]  # Get the first match
            
            # Get more detailed movie info
            movie_info = tmdb.Movies(movie['id']).info()
            
            # Create embed
            embed = discord.Embed(
                title=movie_info['title'],
                description=movie_info['overview'],
                color=discord.Color.blue(),
                url=f"https://www.themoviedb.org/movie/{movie_info['id']}"
            )
            
            # Add movie details
            if movie_info['release_date']:
                embed.add_field(name="Release Date", value=movie_info['release_date'], inline=True)
            if movie_info['runtime']:
                embed.add_field(name="Runtime", value=f"{movie_info['runtime']} minutes", inline=True)
            if movie_info['vote_average']:
                embed.add_field(name="Rating", value=f"⭐ {movie_info['vote_average']:.1f}/10", inline=True)
            
            # Add poster if available
            if movie_info['poster_path']:
                embed.set_thumbnail(url=f"https://image.tmdb.org/t/p/w500{movie_info['poster_path']}")
                
            return embed
        except Exception as e:
            self.logger.error(f"Error getting movie metadata: {e}")
            return None
