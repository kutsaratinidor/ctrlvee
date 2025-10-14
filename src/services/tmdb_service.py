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

    def get_tv_metadata(self, title: str, season: int | None = None):
        """Get TV show or season metadata from TMDB.

        Args:
            title: Clean TV show title
            season: Optional season number to fetch season-specific info
        Returns:
            discord.Embed or None
        """
        if not self.api_key:
            self.logger.warning("No TMDB API key found")
            return None

        try:
            self.logger.debug(f"Searching TMDB for TV title: {title}" + (f" (season {season})" if season else ""))
            search = tmdb.Search()
            response = search.tv(query=title)
            if not response['results']:
                self.logger.debug(f"No TV results found for: {title}")
                return None

            def norm(s: str) -> str:
                return ''.join(ch for ch in s.lower() if ch.isalnum())

            target = norm(title)
            best = None
            for item in response['results']:
                item_name = item.get('name') or item.get('original_name') or ''
                n = norm(item_name)
                # TV has 'first_air_date' which may include year
                item_year = None
                fd = item.get('first_air_date')
                if fd and len(fd) >= 4 and fd[:4].isdigit():
                    item_year = int(fd[:4])

                rank = (
                    2 if (n == target) else
                    1 if (target in n or n in target) else
                    0
                )
                if best is None or rank > best[0]:
                    best = (rank, item)

            tv = best[1] if best else response['results'][0]
            self.logger.debug(f"Selected TV match: {tv.get('name')} ({(tv.get('first_air_date') or '')[:4]})")

            tv_info = tmdb.TV(tv['id']).info()

            # Build embed
            embed = discord.Embed(
                title=tv_info.get('name') or tv_info.get('original_name'),
                description=tv_info.get('overview') or '',
                color=discord.Color.blue(),
                url=f"https://www.themoviedb.org/tv/{tv_info['id']}"
            )

            if tv_info.get('first_air_date'):
                embed.add_field(name="First Air Date", value=tv_info.get('first_air_date'), inline=True)
            if tv_info.get('vote_average'):
                embed.add_field(name="Rating", value=f"⭐ {tv_info.get('vote_average'):.1f}/10", inline=True)

            # Season-specific info
            if season is not None:
                try:
                    season_obj = tmdb.TV_Seasons(tv['id'], season).info()
                    # Add episode count and season poster if available
                    eps = season_obj.get('episode_count') or season_obj.get('episodes') and len(season_obj.get('episodes'))
                    if eps:
                        embed.add_field(name=f"Season {season} Episodes", value=str(eps), inline=True)
                    if season_obj.get('poster_path'):
                        embed.set_thumbnail(url=f"https://image.tmdb.org/t/p/w500{season_obj.get('poster_path')}")
                    else:
                        # Fallback to show poster
                        if tv_info.get('poster_path'):
                            embed.set_thumbnail(url=f"https://image.tmdb.org/t/p/w500{tv_info.get('poster_path')}")
                except Exception as e:
                    self.logger.debug(f"Failed to fetch season info for {tv_info.get('name')} season {season}: {e}")
                    if tv_info.get('poster_path'):
                        embed.set_thumbnail(url=f"https://image.tmdb.org/t/p/w500{tv_info.get('poster_path')}")
            else:
                if tv_info.get('poster_path'):
                    embed.set_thumbnail(url=f"https://image.tmdb.org/t/p/w500{tv_info.get('poster_path')}")

            return embed
        except Exception as e:
            self.logger.error(f"Error getting TV metadata: {e}")
            return None
