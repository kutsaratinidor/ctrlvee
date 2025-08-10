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

    def get_movie_metadata(self, title: str, year: int | None = None):
        """Get movie metadata from TMDB
        
        Args:
            title: Clean movie title
            year: Optional release year to disambiguate results
        """
        if not self.api_key:
            self.logger.warning("No TMDB API key found")
            return None
        
        try:
            self.logger.debug(f"Searching TMDB for title: {title}" + (f" ({year})" if year else ""))
            search = tmdb.Search()
            # TMDB supports 'year' and 'primary_release_year'. We'll pass 'year' for broader match.
            if year:
                response = search.movie(query=title, year=year)
            else:
                response = search.movie(query=title)
            
            if not response['results']:
                self.logger.debug(f"No results found for: {title}")
                return None
            
            # Choose best match: prefer exact title and year when provided
            def norm(s: str) -> str:
                return ''.join(ch for ch in s.lower() if ch.isalnum())

            target = norm(title)
            best = None
            for item in response['results']:
                item_title = item.get('title') or item.get('original_title') or ''
                n = norm(item_title)
                item_year = None
                rd = item.get('release_date')
                if rd and len(rd) >= 4 and rd[:4].isdigit():
                    item_year = int(rd[:4])

                # Ranking tuple: higher is better
                rank = (
                    3 if (n == target and year and item_year == year) else
                    2 if (n == target) else
                    1 if (year and item_year == year) else
                    0
                )
                if best is None or rank > best[0]:
                    best = (rank, item)

            movie = (best[1] if best else response['results'][0])
            self.logger.debug(f"Selected TMDB match: {movie.get('title')} ({(movie.get('release_date') or '')[:4]})")
            
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
