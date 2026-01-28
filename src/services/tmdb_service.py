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

    def _compute_title_score(self, search_title: str, item_title: str, item_original_title: str, target_year: int | None, item_year: int | None, popularity: float, vote_count: int) -> float:
        """Compute a matching score for a search result.
        
        Args:
            search_title: The title being searched for
            item_title: The result's primary title
            item_original_title: The result's original title
            target_year: The year being searched for (None if not provided)
            item_year: The result's release/air year
            popularity: The result's TMDB popularity score
            vote_count: The result's vote count
            
        Returns:
            A float score (higher is better, typically 0-200+)
        """
        def norm(s: str) -> str:
            return ''.join(ch for ch in s.lower() if ch.isalnum())

        target = norm(search_title)
        n = norm(item_title)
        n_orig = norm(item_original_title)
        
        score = 0.0
        
        # Title matching: exact match is best, then check original_title (for anime/foreign content)
        if n == target or n_orig == target:
            score += 100.0  # Exact match bonus
        elif target in n or n in target or target in n_orig or n_orig in target:
            score += 50.0  # Partial match bonus
        else:
            score += 0.0  # No match
        
        # Year matching: exact year match, then proximity
        if target_year and item_year:
            if item_year == target_year:
                score += 50.0  # Exact year match
            else:
                # Penalize by year distance (max penalty at 5+ years)
                year_diff = abs(item_year - target_year)
                score += max(0, 50.0 - (year_diff * 10.0))
        elif not target_year:
            # No year provided, slight bonus for recent releases
            score += 5.0
        
        # Popularity/quality indicators (helps differentiate similar titles)
        if popularity > 0:
            score += min(20.0, popularity * 0.5)  # Cap at 20 points
        if vote_count > 100:
            score += min(10.0, vote_count / 100)  # Cap at 10 points
        
        return score

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
            self.logger.info(f"TMDB movie lookup: title='{title}' year={year}")
            search = tmdb.Search()
            # First attempt: include year if provided; if empty, fallback without year
            if year:
                response = search.movie(query=title, year=year)
                # Log raw result titles for diagnostics
                try:
                    res = response.get('results') or []
                    self.logger.info(f"TMDB movie results (with year): count={len(res)} sample={[ (r.get('title') or r.get('original_title')) for r in res[:5] ]}")
                except Exception:
                    pass
                if not response.get('results'):
                    self.logger.info(f"TMDB movie lookup returned no results with year={year}; retrying without year")
                    response = search.movie(query=title)
                    try:
                        res = response.get('results') or []
                        self.logger.info(f"TMDB movie results (no year): count={len(res)} sample={[ (r.get('title') or r.get('original_title')) for r in res[:5] ]}")
                    except Exception:
                        pass
            else:
                response = search.movie(query=title)
                try:
                    res = response.get('results') or []
                    self.logger.info(f"TMDB movie results: count={len(res)} sample={[ (r.get('title') or r.get('original_title')) for r in res[:5] ]}")
                except Exception:
                    pass

            if not response.get('results'):
                self.logger.info(f"TMDB movie lookup: no results for title='{title}' (year={year})")
                return None
            
            # Choose best match with improved scoring algorithm
            def norm(s: str) -> str:
                return ''.join(ch for ch in s.lower() if ch.isalnum())

            target = norm(title)
            best = None
            for item in response['results']:
                item_title = item.get('title') or ''
                item_original_title = item.get('original_title') or ''
                n = norm(item_title)
                n_orig = norm(item_original_title)
                
                item_year = None
                rd = item.get('release_date')
                if rd and len(rd) >= 4 and rd[:4].isdigit():
                    item_year = int(rd[:4])

                # Compute score (higher is better)
                score = 0.0
                
                # Title matching: exact match is best, then check original_title (for anime/foreign films)
                if n == target or n_orig == target:
                    score += 100.0  # Exact match bonus
                elif target in n or n in target or target in n_orig or n_orig in target:
                    score += 50.0  # Partial match bonus
                else:
                    score += 0.0  # No match
                
                # Year matching: exact year match, then proximity
                if year and item_year:
                    if item_year == year:
                        score += 50.0  # Exact year match
                    else:
                        # Penalize by year distance (max penalty at 5+ years)
                        year_diff = abs(item_year - year)
                        score += max(0, 50.0 - (year_diff * 10.0))
                elif not year:
                    # No year provided, slight bonus for recent releases
                    score += 5.0
                
                # Popularity/quality indicators (helps differentiate similar titles)
                popularity = item.get('popularity', 0.0)
                vote_count = item.get('vote_count', 0)
                if popularity > 0:
                    score += min(20.0, popularity * 0.5)  # Cap at 20 points
                if vote_count > 100:
                    score += min(10.0, vote_count / 100)  # Cap at 10 points
                
                if best is None or score > best[0]:
                    best = (score, item)

            movie = (best[1] if best else response['results'][0])
            best_score = best[0] if best else 0.0
            self.logger.info(f"TMDB movie match: '{movie.get('title')}' (orig: '{movie.get('original_title')}') year={(movie.get('release_date') or '')[:4]} score={best_score:.1f}")
            
            # Store score for comparison when deciding between movie/TV
            self._last_match_score = best_score
            
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
            
            # Add genres if available
            if movie_info['genres']:
                embed.add_field(name="Genre", value=', '.join([g['name'] for g in movie_info['genres']]), inline=True)
            
            # Add poster if available
            if movie_info['poster_path']:
                embed.set_thumbnail(url=f"https://image.tmdb.org/t/p/w500{movie_info['poster_path']}")
                
            return embed
        except Exception as e:
            self.logger.error(f"Error getting movie metadata for title='{title}' year={year}: {e}")
            return None

    def get_tv_metadata(self, title: str, season: int | None = None, year: int | None = None):
        """Get TV show or season metadata from TMDB.

        Args:
            title: Clean TV show title
            season: Optional season number to fetch season-specific info
            year: Optional first air year to disambiguate results
        Returns:
            discord.Embed or None
        """
        if not self.api_key:
            self.logger.warning("No TMDB API key found")
            return None

        try:
            # Normalize title: strip trailing year in parentheses (e.g., "Show (2015)")
            try:
                import re as _re
                match = _re.search(r"\s*\((19|20)\d{2}\)\s*$", title)
                if match and not year:
                    # Extract year from title if not explicitly provided
                    year = int(match.group(0).strip('() '))
                norm_title = _re.sub(r"\s*\((19|20)\d{2}\)\s*$", "", title).strip()
            except Exception:
                norm_title = title
            self.logger.info(f"TMDB TV lookup: title='{norm_title}' year={year} season={season}")
            search = tmdb.Search()
            
            # Try with year first if provided
            if year:
                response = search.tv(query=norm_title, first_air_date_year=year)
                try:
                    res = response.get('results') or []
                    self.logger.info(f"TMDB TV results (with year): count={len(res)} sample={[ (r.get('name') or r.get('original_name')) for r in res[:5] ]}")
                except Exception:
                    pass
                if not response.get('results'):
                    self.logger.info(f"TMDB TV lookup returned no results with year={year}; retrying without year")
                    response = search.tv(query=norm_title)
                    try:
                        res = response.get('results') or []
                        self.logger.info(f"TMDB TV results (no year): count={len(res)} sample={[ (r.get('name') or r.get('original_name')) for r in res[:5] ]}")
                    except Exception:
                        pass
            else:
                response = search.tv(query=norm_title)
                try:
                    res = response.get('results') or []
                    self.logger.info(f"TMDB TV results: count={len(res)} sample={[ (r.get('name') or r.get('original_name')) for r in res[:5] ]}")
                except Exception:
                    pass
                    
            if not response.get('results'):
                self.logger.debug(f"No TV results found for: {norm_title}")
                return None

            def norm(s: str) -> str:
                return ''.join(ch for ch in s.lower() if ch.isalnum())

            target = norm(norm_title)
            best = None
            for item in response['results']:
                item_name = item.get('name') or ''
                item_original_name = item.get('original_name') or ''
                n = norm(item_name)
                n_orig = norm(item_original_name)
                
                # TV has 'first_air_date' which may include year
                item_year = None
                fd = item.get('first_air_date')
                if fd and len(fd) >= 4 and fd[:4].isdigit():
                    item_year = int(fd[:4])

                # Compute score (higher is better)
                score = 0.0
                
                # Title matching: exact match is best, check both name and original_name (for anime)
                if n == target or n_orig == target:
                    score += 100.0  # Exact match bonus
                elif target in n or n in target or target in n_orig or n_orig in target:
                    score += 50.0  # Partial match bonus
                else:
                    score += 0.0  # No match
                
                # Year matching: exact year match, then proximity
                if year and item_year:
                    if item_year == year:
                        score += 50.0  # Exact year match
                    else:
                        # Penalize by year distance (max penalty at 5+ years)
                        year_diff = abs(item_year - year)
                        score += max(0, 50.0 - (year_diff * 10.0))
                elif not year:
                    # No year provided, slight bonus for recent shows
                    score += 5.0
                
                # Popularity/quality indicators (helps differentiate similar titles)
                popularity = item.get('popularity', 0.0)
                vote_count = item.get('vote_count', 0)
                if popularity > 0:
                    score += min(20.0, popularity * 0.5)  # Cap at 20 points
                if vote_count > 100:
                    score += min(10.0, vote_count / 100)  # Cap at 10 points
                
                if best is None or score > best[0]:
                    best = (score, item)

            tv = best[1] if best else response['results'][0]
            best_score = best[0] if best else 0.0
            self.logger.info(f"TMDB TV match: '{tv.get('name')}' (orig: '{tv.get('original_name')}') year={(tv.get('first_air_date') or '')[:4]} score={best_score:.1f}")
            
            # Store score for comparison when deciding between movie/TV
            self._last_match_score = best_score

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

            # Add genres if available
            if tv_info.get('genres'):
                embed.add_field(name="Genre", value=', '.join([g['name'] for g in tv_info['genres']]), inline=True)

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
            self.logger.error(f"Error getting TV metadata for title='{title}' season={season}: {e}")
            return None
