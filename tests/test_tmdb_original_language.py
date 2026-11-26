"""Runnable self-check for the TMDB "Original Language" embed field.

Run from the repo root:  python tests/test_tmdb_original_language.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.services.tmdb_service import TMDBService  # noqa: E402

lang = TMDBService._original_language


def main():
    spoken = [{"iso_639_1": "en", "english_name": "English"}, {"iso_639_1": "ko", "english_name": "Korean"}]
    # Code is resolved to its English name via spoken_languages
    assert lang({"original_language": "ko", "spoken_languages": spoken}) == "Korean"
    # Code not among spoken_languages -> bare uppercase code
    assert lang({"original_language": "tl", "spoken_languages": spoken}) == "TL"
    # No original_language -> no field
    assert lang({"spoken_languages": spoken}) is None
    assert lang({"original_language": "", "spoken_languages": None}) is None

    embed = TMDBService(api_key="x")._build_embed_from_movie_info({
        "id": 1, "title": "Parasite", "overview": "", "original_language": "ko", "spoken_languages": spoken,
    })
    assert any(f.name == "Original Language" and f.value == "Korean" for f in embed.fields)

    print(f"OK  ({os.path.basename(__file__)}): original language cases pass")


if __name__ == "__main__":
    main()
