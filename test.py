import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from bs4 import BeautifulSoup
from util.resolver import (
    needs_resolution,
    is_playlist,
    resolve_to_search_query,
    resolve_to_direct_url,
    resolve_playlist,
    get_source_name,
    SOURCE_NAMES,
    RESOLVERS,
    DIRECT_RESOLVERS,
    PLAYLIST_RESOLVERS,
)


# ── needs_resolution ──────────────────────────────────────────

class TestNeedsResolution:
    def test_spotify_track(self):
        assert needs_resolution("https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT")

    def test_tidal_track(self):
        assert needs_resolution("https://tidal.com/browse/track/12345678")

    def test_tidal_track_no_browse(self):
        assert needs_resolution("https://tidal.com/track/12345678")

    def test_apple_music_track(self):
        assert needs_resolution("https://music.apple.com/de/album/song-name/123456789?i=12345678")

    def test_deezer_track(self):
        assert needs_resolution("https://deezer.com/track/12345678")

    def test_deezer_track_with_country(self):
        assert needs_resolution("https://deezer.com/us/track/12345678")

    def test_youtube_url_returns_false(self):
        assert not needs_resolution("https://youtube.com/watch?v=dQw4w9WgXcQ")

    def test_plain_text_returns_false(self):
        assert not needs_resolution("never gonna give you up")

    def test_spotify_playlist_returns_false(self):
        assert not needs_resolution("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M")

    def test_case_insensitive(self):
        assert needs_resolution("HTTPS://OPEN.SPOTIFY.COM/TRACK/4cOdK2wGLETKBW3PvgPWqT")

    # -- neue URL-Formate (vom User gemeldet) --

    def test_spotify_intl_locale(self):
        assert needs_resolution("https://open.spotify.com/intl-de/track/6XYydpbZMrgQbZdv5J31oU?si=f41497311fe449f3")

    def test_tidal_stage_subdomain(self):
        assert needs_resolution("https://stage.tidal.com/track/475684244/u")

    def test_deezer_shortlink(self):
        assert needs_resolution("https://link.deezer.com/s/33CeyXodO244nuVj2Oe2k")

    def test_apple_music_song_format(self):
        assert needs_resolution("https://music.apple.com/de/song/rasenschach/1757279396")


# ── is_playlist ───────────────────────────────────────────────

class TestIsPlaylist:
    def test_tidal_playlist(self):
        assert is_playlist("https://tidal.com/browse/playlist/abc123")

    def test_tidal_album(self):
        assert is_playlist("https://tidal.com/browse/album/12345678")

    def test_apple_music_playlist(self):
        assert is_playlist("https://music.apple.com/de/playlist/some-list/pl.abc123")

    def test_apple_music_album_no_i_param(self):
        assert is_playlist("https://music.apple.com/de/album/album-name/123456789")

    def test_apple_music_track_with_i_param(self):
        assert not is_playlist("https://music.apple.com/de/album/song-name/123456789?i=12345678")

    def test_deezer_playlist(self):
        assert is_playlist("https://deezer.com/playlist/12345678")

    def test_deezer_album(self):
        assert is_playlist("https://deezer.com/album/12345678")

    def test_spotify_playlist(self):
        assert is_playlist("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M")

    def test_spotify_album(self):
        assert is_playlist("https://open.spotify.com/album/1kfVbM3U7ZYjBiE3EiLx2B")

    def test_spotify_track_returns_false(self):
        assert not is_playlist("https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT")

    def test_youtube_url_returns_false(self):
        assert not is_playlist("https://youtube.com/watch?v=dQw4w9WgXcQ")

    # -- neue URL-Formate --

    def test_spotify_intl_playlist(self):
        assert is_playlist("https://open.spotify.com/intl-de/playlist/37i9dQZF1DXcBWIGoYBM5M")

    def test_spotify_intl_album(self):
        assert is_playlist("https://open.spotify.com/intl-de/album/1kfVbM3U7ZYjBiE3EiLx2B")

    def test_tidal_stage_album(self):
        assert is_playlist("https://stage.tidal.com/album/12345678")

    def test_deezer_link_playlist(self):
        assert is_playlist("https://link.deezer.com/playlist/12345678")

    def test_apple_music_song_playlist_false(self):
        assert not is_playlist("https://music.apple.com/de/song/rasenschach/1757279396")


# ── Resolve to search query (unit tests with mocks) ────────────

@pytest.mark.asyncio
async def test_resolve_to_search_query_unknown_domain():
    result = await resolve_to_search_query("https://example.com/song")
    assert result is None


@pytest.mark.asyncio
@patch("util.resolver.spotify.get_spotify_track_info", return_value="Song Title Artist Name")
async def test_resolve_spotify_to_search_query(mock_get_info):
    result = await resolve_to_search_query("https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT")
    assert result is not None
    assert result["query"].startswith("ytsearch:")
    assert "Song Title" in result["query"]
    assert result["source_name"] == "Spotify"
    assert result["is_playlist"] is False


@pytest.mark.asyncio
@patch("util.resolver.spotify.get_spotify_track_info", return_value=None)
async def test_resolve_spotify_failure_returns_none(mock_get_info):
    result = await resolve_to_search_query("https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT")
    assert result is None


@pytest.mark.asyncio
@patch("util.resolver.tidal.get_tidal_track_info", return_value="Song Title von Artist Name")
async def test_resolve_tidal_to_search_query(mock_get_info):
    result = await resolve_to_search_query("https://tidal.com/browse/track/12345678")
    assert result is not None
    assert result["query"].startswith("ytsearch:")
    assert result["source_name"] == "Tidal"
    assert result["is_playlist"] is False


@pytest.mark.asyncio
@patch("util.resolver.apple_music.get_apple_music_track_info", return_value="Song Title Artist Name")
async def test_resolve_apple_music_to_search_query(mock_get_info):
    result = await resolve_to_search_query("https://music.apple.com/de/album/song-name/123456789?i=12345678")
    assert result is not None
    assert result["query"].startswith("ytsearch:")
    assert result["source_name"] == "Apple Music"
    assert result["is_playlist"] is False


@pytest.mark.asyncio
@patch("util.resolver.deezer.get_deezer_track_info", return_value="Artist Name - Song Title")
async def test_resolve_deezer_to_search_query(mock_get_info):
    result = await resolve_to_search_query("https://deezer.com/track/12345678")
    assert result is not None
    assert result["query"].startswith("ytsearch:")
    assert result["source_name"] == "Deezer"
    assert result["is_playlist"] is False


# ── Resolve to direct URL (unit tests with mocks) ─────────────

@pytest.mark.asyncio
async def test_resolve_to_direct_url_unknown_domain():
    result = await resolve_to_direct_url("https://example.com/song")
    assert result is None


@pytest.mark.asyncio
@patch("util.resolver.deezer._search_ytmusic", return_value="https://music.youtube.com/watch?v=test123")
async def test_resolve_deezer_direct(mock_search):
    result = await resolve_to_direct_url("https://deezer.com/track/12345678")
    assert result == "https://music.youtube.com/watch?v=test123"


@pytest.mark.asyncio
@patch("util.resolver.deezer._search_ytmusic", return_value=None)
async def test_resolve_deezer_direct_failure(mock_search):
    result = await resolve_to_direct_url("https://deezer.com/track/12345678")
    assert result is None


# ── Scraper functions (unit tests with mock HTML) ─────────────

SPOTIFY_HTML = """<html><head>
<meta property="og:title" content="Blinding Lights">
<meta property="og:description" content="Song by The Weeknd · 2019">
<title>Blinding Lights - song and lyrics by The Weeknd | Spotify</title>
</head></html>"""

TIDAL_HTML = """<html><head>
<meta property="og:title" content="Blinding Lights von The Weeknd">
</head></html>"""

APPLE_MUSIC_HTML = """<html><head>
<meta property="og:title" content="Blinding Lights">
<meta property="og:description" content="Song · The Weeknd · 2019">
</head></html>"""

DEEZER_HTML_KUERZER = """<html><head>
<meta property="og:title" content="The Weeknd - Blinding Lights">
</head></html>"""

DEEZER_HTML_LANG = """<html><head>
<meta property="og:title" content="The Weeknd - Blinding Lights - Musik hören">
</head></html>"""


class TestSpotifyScraper:
    def test_get_spotify_track_info_success(self):
        with patch("util.resolver.spotify.requests.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = SPOTIFY_HTML
            mock_get.return_value = mock_response

            from util.resolver.spotify import get_spotify_track_info
            result = get_spotify_track_info("https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT")
            assert result is not None
            assert "Blinding Lights" in result
            assert "The Weeknd" in result

    def test_get_spotify_track_info_non_200(self):
        with patch("util.resolver.spotify.requests.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 404
            mock_get.return_value = mock_response

            from util.resolver.spotify import get_spotify_track_info
            result = get_spotify_track_info("https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT")
            assert result is None

    def test_get_spotify_track_info_fallback_title(self):
        html = """<html><head>
        <title>Blinding Lights - song and lyrics by The Weeknd | Spotify</title>
        </head></html>"""
        with patch("util.resolver.spotify.requests.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = html
            mock_get.return_value = mock_response

            from util.resolver.spotify import get_spotify_track_info
            result = get_spotify_track_info("https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT")
            assert result is not None
            assert "Blinding Lights - song and lyrics by The Weeknd" in result


class TestTidalScraper:
    def test_get_tidal_track_info_success(self):
        with patch("util.resolver.tidal.requests.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = TIDAL_HTML
            mock_get.return_value = mock_response

            from util.resolver.tidal import get_tidal_track_info
            result = get_tidal_track_info("https://tidal.com/browse/track/12345678")
            assert result is not None
            assert "Blinding Lights" in result
            assert "The Weeknd" in result

    def test_get_tidal_track_info_no_von(self):
        html = """<html><head>
        <meta property="og:title" content="Blinding Lights The Weeknd">
        </head></html>"""
        with patch("util.resolver.tidal.requests.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = html
            mock_get.return_value = mock_response

            from util.resolver.tidal import get_tidal_track_info
            result = get_tidal_track_info("https://tidal.com/browse/track/12345678")
            assert result == "Blinding Lights The Weeknd"

    def test_get_tidal_track_info_non_200(self):
        with patch("util.resolver.tidal.requests.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 403
            mock_get.return_value = mock_response

            from util.resolver.tidal import get_tidal_track_info
            result = get_tidal_track_info("https://tidal.com/browse/track/12345678")
            assert result is None


class TestAppleMusicScraper:
    def test_get_apple_music_track_info_success(self):
        with patch("util.resolver.apple_music.requests.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = APPLE_MUSIC_HTML
            mock_get.return_value = mock_response

            from util.resolver.apple_music import get_apple_music_track_info
            result = get_apple_music_track_info("https://music.apple.com/de/album/song-name/123456789?i=12345678")
            assert result is not None
            assert "Blinding Lights" in result
            assert "The Weeknd" in result

    def test_get_apple_music_track_info_non_200(self):
        with patch("util.resolver.apple_music.requests.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 500
            mock_get.return_value = mock_response

            from util.resolver.apple_music import get_apple_music_track_info
            result = get_apple_music_track_info("https://music.apple.com/de/album/song-name/123456789?i=12345678")
            assert result is None

    def test_get_apple_music_track_info_no_description(self):
        html = """<html><head>
        <meta property="og:title" content="Blinding Lights">
        </head></html>"""
        with patch("util.resolver.apple_music.requests.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = html
            mock_get.return_value = mock_response

            from util.resolver.apple_music import get_apple_music_track_info
            result = get_apple_music_track_info("https://music.apple.com/de/album/song-name/123456789?i=12345678")
            assert result == "Blinding Lights"


class TestDeezerScraper:
    def test_get_deezer_track_info_kurzer_titel(self):
        with patch("util.resolver.deezer.requests.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = DEEZER_HTML_KUERZER
            mock_get.return_value = mock_response

            from util.resolver.deezer import get_deezer_track_info
            result = get_deezer_track_info("https://deezer.com/track/12345678")
            assert result is not None
            assert "The Weeknd - Blinding Lights" in result
            assert "Musik hören" not in result

    def test_get_deezer_track_info_langer_titel(self):
        with patch("util.resolver.deezer.requests.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = DEEZER_HTML_LANG
            mock_get.return_value = mock_response

            from util.resolver.deezer import get_deezer_track_info
            result = get_deezer_track_info("https://deezer.com/track/12345678")
            assert result is not None
            assert "The Weeknd - Blinding Lights" in result
            assert "Musik hören" not in result

    def test_get_deezer_track_info_non_200(self):
        with patch("util.resolver.deezer.requests.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 403
            mock_get.return_value = mock_response

            from util.resolver.deezer import get_deezer_track_info
            result = get_deezer_track_info("https://deezer.com/track/12345678")
            assert result is None


# ── get_source_name ────────────────────────────────────────────

class TestGetSourceName:
    def test_spotify(self):
        assert get_source_name("https://open.spotify.com/playlist/abc123") == "Spotify"

    def test_deezer(self):
        assert get_source_name("https://deezer.com/album/123456") == "Deezer"

    def test_apple_music(self):
        assert get_source_name("https://music.apple.com/de/album/test/123") == "Apple Music"

    def test_tidal(self):
        assert get_source_name("https://tidal.com/browse/playlist/abc123") == "Tidal"

    def test_youtube_returns_none(self):
        assert get_source_name("https://youtube.com/watch?v=test") is None

    def test_unknown_returns_none(self):
        assert get_source_name("https://example.com/song") is None

    def test_case_insensitive(self):
        assert get_source_name("HTTPS://OPEN.SPOTIFY.COM/TRACK/123") == "Spotify"


# ── resolve_playlist ───────────────────────────────────────────

class TestPlaylistResolversRegistration:
    def test_playlist_resolvers_have_all_platforms(self):
        assert "open.spotify.com" in PLAYLIST_RESOLVERS
        assert "deezer.com" in PLAYLIST_RESOLVERS
        assert "music.apple.com" in PLAYLIST_RESOLVERS
        assert "tidal.com" in PLAYLIST_RESOLVERS

    def test_source_names_in_sync(self):
        for domain in PLAYLIST_RESOLVERS:
            assert domain in SOURCE_NAMES


class TestResolvePlaylist:
    @pytest.mark.asyncio
    async def test_unknown_domain_returns_none(self):
        result = await resolve_playlist("https://example.com/song")
        assert result is None

    @pytest.mark.asyncio
    async def test_playlist_resolver_returns_list(self):
        with patch("util.resolver.spotify.get_spotify_playlist_tracks") as mock_get:
            mock_get.return_value = [
                "https://open.spotify.com/track/abc123",
                "https://open.spotify.com/track/def456",
            ]

            with patch("util.resolver.spotify.resolve") as mock_resolve:
                mock_resolve.return_value = {
                    "query": "ytsearch:test song",
                    "title": "Test Song",
                    "artist": "Test Artist",
                    "source_name": "Spotify",
                }

                result = await resolve_playlist("https://open.spotify.com/playlist/abc123")
                assert result is not None
                assert len(result) == 2
                assert result[0]["source_name"] == "Spotify"
                assert result[0]["title"] == "Test Song"

    @pytest.mark.asyncio
    async def test_resolve_playlist_empty_tracks_returns_none(self):
        with patch("util.resolver.spotify.get_spotify_playlist_tracks") as mock_get:
            mock_get.return_value = None

            result = await resolve_playlist("https://open.spotify.com/playlist/abc123")
            assert result is None


# ── Playlist scraping functions ────────────────────────────────

class TestSpotifyPlaylistScraper:
    SPOTIFY_PLAYLIST_HTML = """<html><body>
    <a href="https://open.spotify.com/track/abc123">Track 1</a>
    <a href="https://open.spotify.com/track/def456">Track 2</a>
    <a href="https://open.spotify.com/track/ghi789">Track 3</a>
    <a href="https://open.spotify.com/album/xyz999">Not a track</a>
    </body></html>"""

    def test_extracts_track_links(self):
        from util.resolver.spotify import get_spotify_playlist_tracks
        with patch("util.resolver.spotify.requests.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = self.SPOTIFY_PLAYLIST_HTML
            mock_get.return_value = mock_response

            result = get_spotify_playlist_tracks("https://open.spotify.com/playlist/abc123")
            assert result is not None
            assert len(result) == 3
            assert "open.spotify.com/track/abc123" in result[0]
            assert "open.spotify.com/track/def456" in result[1]
            assert "open.spotify.com/track/ghi789" in result[2]

    def test_non_200_returns_none(self):
        from util.resolver.spotify import get_spotify_playlist_tracks
        with patch("util.resolver.spotify.requests.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 404
            mock_get.return_value = mock_response

            result = get_spotify_playlist_tracks("https://open.spotify.com/playlist/abc123")
            assert result is None

    def test_no_track_links_returns_none(self):
        from util.resolver.spotify import get_spotify_playlist_tracks
        with patch("util.resolver.spotify.requests.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = "<html><body>No tracks here</body></html>"
            mock_get.return_value = mock_response

            result = get_spotify_playlist_tracks("https://open.spotify.com/playlist/abc123")
            assert result is None


class TestDeezerPlaylistScraper:
    DEEZER_PLAYLIST_HTML = """<html><body>
    <a href="https://deezer.com/track/123">Track 1</a>
    <a href="https://deezer.com/track/456">Track 2</a>
    <a href="https://deezer.com/album/789">Not a track</a>
    </body></html>"""

    def test_extracts_track_links(self):
        from util.resolver.deezer import get_deezer_playlist_tracks
        with patch("util.resolver.deezer.requests.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = self.DEEZER_PLAYLIST_HTML
            mock_get.return_value = mock_response

            result = get_deezer_playlist_tracks("https://deezer.com/playlist/123")
            assert result is not None
            assert len(result) == 2
            assert "deezer.com/track/123" in result[0]
            assert "deezer.com/track/456" in result[1]

    def test_non_200_returns_none(self):
        from util.resolver.deezer import get_deezer_playlist_tracks
        with patch("util.resolver.deezer.requests.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 500
            mock_get.return_value = mock_response

            result = get_deezer_playlist_tracks("https://deezer.com/playlist/123")
            assert result is None


class TestTidalPlaylistScraper:
    TIDAL_PLAYLIST_HTML = """<html><body>
    <a href="https://tidal.com/browse/track/123">Track 1</a>
    <a href="https://tidal.com/track/456">Track 2</a>
    <a href="https://tidal.com/browse/album/789">Not a track</a>
    </body></html>"""

    def test_extracts_track_links(self):
        from util.resolver.tidal import get_tidal_playlist_tracks
        with patch("util.resolver.tidal.requests.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = self.TIDAL_PLAYLIST_HTML
            mock_get.return_value = mock_response

            result = get_tidal_playlist_tracks("https://tidal.com/browse/playlist/abc")
            assert result is not None
            assert len(result) == 2
            assert "tidal.com/browse/track/123" in result[0]
            assert "tidal.com/track/456" in result[1]

    def test_non_200_returns_none(self):
        from util.resolver.tidal import get_tidal_playlist_tracks
        with patch("util.resolver.tidal.requests.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 403
            mock_get.return_value = mock_response

            result = get_tidal_playlist_tracks("https://tidal.com/browse/playlist/abc")
            assert result is None


class TestAppleMusicPlaylistScraper:
    APPLE_MUSIC_ALBUM_HTML = """<html><body>
    <a href="https://music.apple.com/de/album/album-name/123456789?i=111">Track 1</a>
    <a href="https://music.apple.com/de/album/album-name/123456789?i=222">Track 2</a>
    <a href="https://music.apple.com/de/album/album-name/123456789?i=333">Track 3</a>
    <a href="https://music.apple.com/de/artist/test/999">Not a track</a>
    </body></html>"""

    def test_extracts_tracks_from_links(self):
        from util.resolver.apple_music import get_apple_music_playlist_info
        with patch("util.resolver.apple_music.requests.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = self.APPLE_MUSIC_ALBUM_HTML
            mock_get.return_value = mock_response

            with patch("util.resolver.apple_music.get_apple_music_track_info") as mock_track:
                mock_track.side_effect = [
                    "Artist One - Song One",
                    "Artist Two - Song Two",
                    "Artist Three - Song Three",
                ]

                result = get_apple_music_playlist_info(
                    "https://music.apple.com/de/album/album-name/123456789"
                )
                assert result is not None
                assert len(result) == 3
                assert result[0]["title"] == "Song One"
                assert result[0]["artist"] == "Artist One"
                assert result[1]["title"] == "Song Two"
                assert result[1]["artist"] == "Artist Two"
                assert result[2]["title"] == "Song Three"
                assert result[2]["artist"] == "Artist Three"

    def test_non_200_returns_none(self):
        from util.resolver.apple_music import get_apple_music_playlist_info
        with patch("util.resolver.apple_music.requests.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 500
            mock_get.return_value = mock_response

            result = get_apple_music_playlist_info(
                "https://music.apple.com/de/album/album-name/123456789"
            )
            assert result is None

    def test_empty_page_returns_none(self):
        from util.resolver.apple_music import get_apple_music_playlist_info
        with patch("util.resolver.apple_music.requests.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = "<html><body>No content</body></html>"
            mock_get.return_value = mock_response

            result = get_apple_music_playlist_info(
                "https://music.apple.com/de/album/album-name/123456789"
            )
            assert result is None
