import aiohttp
import asyncio
import contextlib
import discord
import json
import logging
import pytest
import time
import xml.etree.ElementTree as ET
from datetime import (
    datetime,
    timedelta,
    timezone,
)
from random import (
    choice,
)
from redbot.cogs.streams.streamtypes import (
    APIError,
    InvalidTwitchCredentials,
    InvalidYoutubeCredentials,
    OfflineStream,
    PicartoStream,
    StreamNotFound,
    TWITCH_BASE_URL,
    TWITCH_FOLLOWS_ENDPOINT,
    TWITCH_ID_ENDPOINT,
    TWITCH_STREAMS_ENDPOINT,
    TwitchStream,
    YOUTUBE_CHANNELS_ENDPOINT,
    YOUTUBE_CHANNEL_RSS,
    YOUTUBE_SEARCH_ENDPOINT,
    YOUTUBE_VIDEOS_ENDPOINT,
    YoutubeQuotaExceeded,
    YoutubeStream,
)
from redbot.core.utils.chat_formatting import (
    humanize_number,
)
from string import (
    ascii_letters,
)
from typing import (
    ClassVar,
    List,
    Optional,
    Tuple,
)
from unittest.mock import (
    MagicMock,
)


@pytest.mark.asyncio
async def test_youtube_no_api_key():
    """
    Test that YoutubeStream.is_online() correctly raises InvalidYoutubeCredentials
    when no YouTube API key has been provided.
    """
    dummy_bot = MagicMock()
    dummy_config = MagicMock()
    yt_stream = YoutubeStream(
        _bot=dummy_bot,
        name="test_channel",
        channels=[],
        messages=[],
        config=dummy_config,
        token=None,
    )
    with pytest.raises(InvalidYoutubeCredentials):
        await yt_stream.is_online()


@pytest.mark.asyncio
async def test_youtube_offline_stream(monkeypatch):
    """
    Test that YoutubeStream.is_online() correctly raises OfflineStream when the RSS feed
    returns no video entries (i.e. the channel is offline).
    """

    class DummyResponseLocal:

        def __init__(self, url):
            self.url = url
            self.status = 200

        async def text(self):
            return "<feed xmlns='http://www.w3.org/2005/Atom'></feed>"

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            pass

    class DummySessionLocal:

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            pass

        def get(self, url, **kwargs):
            return DummyResponseLocal(url)

    monkeypatch.setattr(
        aiohttp, "ClientSession", lambda *args, **kwargs: DummySessionLocal()
    )
    dummy_bot = MagicMock()
    dummy_config = MagicMock()
    yt_stream = YoutubeStream(
        _bot=dummy_bot,
        name="dummy_channel",
        channels=[],
        messages=[],
        config=dummy_config,
        token={"api_key": "dummy_api_key"},
        id="dummy_channel_id",
    )
    with pytest.raises(OfflineStream):
        await yt_stream.is_online()


class DummyResponse:
    """Dummy implementation for aiohttp.ClientResponse."""

    def __init__(self, url, status, text_data=None, json_data=None, headers=None):
        self.url = url
        self.status = status
        self._text = text_data
        self._json = json_data
        self.headers = headers or {}

    async def text(self, encoding="utf-8"):
        return self._text

    async def json(self, encoding="utf-8"):
        return self._json

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        pass


class DummySession:
    """Dummy implementation for aiohttp.ClientSession for testing purposes."""

    responses = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        pass

    def get(self, url, **kwargs):
        return DummySession.responses.pop(0)


@pytest.mark.asyncio
async def test_youtube_live_stream(monkeypatch):
    """
    Test that YoutubeStream.is_online() returns a valid embed when the channel is live.
    This simulates the scenario where the RSS feed returns one video id,
    and the API calls to YOUTUBE_VIDEOS_ENDPOINT return live streaming data.
    """
    xml_feed = '<feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015"><entry><yt:videoId>dummy_vid</yt:videoId></entry></feed>'
    live_start = datetime.now(timezone.utc).isoformat()
    video_response_data_initial = {
        "items": [
            {
                "id": "dummy_vid",
                "snippet": {
                    "title": "Test Live Stream",
                    "channelTitle": "Test Channel",
                    "thumbnails": {"medium": {"url": "http://example.com/thumb.jpg"}},
                },
                "liveStreamingDetails": {"actualStartTime": live_start},
            }
        ]
    }
    video_response_data_final = {
        "items": [
            {
                "id": "dummy_vid",
                "snippet": {
                    "title": "Test Live Stream",
                    "channelTitle": "Test Channel",
                    "thumbnails": {"medium": {"url": "http://example.com/thumb.jpg"}},
                },
                "liveStreamingDetails": {"actualStartTime": live_start},
            }
        ]
    }
    DummySession.responses = [
        DummyResponse(
            url=YOUTUBE_CHANNEL_RSS.format(channel_id="dummy_channel_id"),
            status=200,
            text_data=xml_feed,
        ),
        DummyResponse(
            url=YOUTUBE_VIDEOS_ENDPOINT,
            status=200,
            json_data=video_response_data_initial,
        ),
        DummyResponse(
            url=YOUTUBE_VIDEOS_ENDPOINT, status=200, json_data=video_response_data_final
        ),
    ]
    monkeypatch.setattr(
        aiohttp, "ClientSession", lambda *args, **kwargs: DummySession()
    )
    dummy_bot = MagicMock()
    dummy_config = MagicMock()
    yt_stream = YoutubeStream(
        _bot=dummy_bot,
        name="dummy_channel",
        channels=[],
        messages=[],
        config=dummy_config,
        token={"api_key": "dummy_api_key"},
        id="dummy_channel_id",
    )
    result = await yt_stream.is_online()
    embed, is_schedule = result
    assert isinstance(embed, discord.Embed)
    assert embed.title == "Test Live Stream"
    assert embed.url == "https://youtube.com/watch?v=dummy_vid"
    assert is_schedule is False
    assert embed.author.name == "Test Channel"


@pytest.mark.asyncio
async def test_youtube_api_error_in_video_endpoint(monkeypatch):
    """
    Test that YoutubeStream.is_online() raises OfflineStream when the video details API call
    returns an API error. This simulates a case where the API call returns an unexpected error,
    and although the error is caught internally, no valid live stream data is obtained.
    """
    xml_feed = '<feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015"><entry><yt:videoId>error_vid</yt:videoId></entry></feed>'
    api_error_data = {
        "error": {
            "code": 500,
            "errors": [{"reason": "internalError", "message": "Internal error"}],
            "message": "Internal error encountered.",
        }
    }
    DummySession.responses = [
        DummyResponse(
            url=YOUTUBE_CHANNEL_RSS.format(channel_id="dummy_channel_id"),
            status=200,
            text_data=xml_feed,
        ),
        DummyResponse(
            url=YOUTUBE_VIDEOS_ENDPOINT, status=200, json_data=api_error_data
        ),
    ]
    monkeypatch.setattr(
        aiohttp, "ClientSession", lambda *args, **kwargs: DummySession()
    )
    dummy_bot = MagicMock()
    dummy_config = MagicMock()
    yt_stream = YoutubeStream(
        _bot=dummy_bot,
        name="dummy_channel",
        channels=[],
        messages=[],
        config=dummy_config,
        token={"api_key": "dummy_api_key"},
        id="dummy_channel_id",
    )
    with pytest.raises(OfflineStream):
        await yt_stream.is_online()


@pytest.mark.asyncio
async def test_picarto_live_stream(monkeypatch):
    """
    Test that PicartoStream.is_online() returns a valid embed when the Picarto API reports a live channel.
    The test simulates a live Picarto stream response and verifies that the returned embed includes the correct
    title, URL, author, and footer information (including category and tags).
    """
    picarto_data = {
        "online": True,
        "avatar": "http://example.com/avatar.png",
        "name": "picarto_channel",
        "title": "Picarto Live!",
        "thumbnails": {"web": "http://example.com/thumb.png"},
        "followers": 1234,
        "viewers_total": 4321,
        "tags": ["tag1", "tag2"],
        "adult": False,
        "category": "Art",
    }
    dummy_text = json.dumps(picarto_data)
    dummy_response = DummyResponse(
        url="https://api.picarto.tv/api/v1/channel/name/picarto_channel",
        status=200,
        text_data=dummy_text,
    )
    DummySession.responses = [dummy_response]
    monkeypatch.setattr(
        aiohttp, "ClientSession", lambda *args, **kwargs: DummySession()
    )
    dummy_bot = MagicMock()
    picarto_stream = PicartoStream(
        _bot=dummy_bot, name="picarto_channel", channels=[], messages=[]
    )
    embed = await picarto_stream.is_online()
    assert isinstance(embed, discord.Embed)
    assert embed.title == "Picarto Live!"
    assert embed.url == "https://picarto.tv/picarto_channel"
    assert embed.author.name == "picarto_channel"
    footer_text = embed.footer.text
    assert "Category: Art" in footer_text
    assert "Tags: tag1, tag2" in footer_text


@pytest.mark.asyncio
async def test_twitch_live_stream(monkeypatch):
    """
    Test that TwitchStream.is_online() returns a valid embed when the Twitch API indicates a live stream.
    This test simulates responses for:
      - The user profile endpoint (TWITCH_ID_ENDPOINT) to retrieve channel ID and profile image.
      - The streams endpoint (TWITCH_STREAMS_ENDPOINT) to indicate that the stream is live.
      - The followers endpoint (TWITCH_FOLLOWS_ENDPOINT) to return a follower count.
    It then verifies that the returned embed has the expected title, author, fields, and images.
    """
    profile_data = {
        "data": [
            {
                "id": "dummy_id",
                "login": "dummy_login",
                "profile_image_url": "http://example.com/profile.png",
            }
        ]
    }
    stream_data = {
        "data": [
            {
                "user_name": "dummy_stream",
                "game_name": "dummy_game",
                "thumbnail_url": "http://example.com/thumbnail.jpg",
                "title": "Dummy Twitch Live Stream",
                "type": "live",
                "viewer_count": 100,
            }
        ]
    }
    follows_data = {"total": 2000}
    DummySession.responses = [
        DummyResponse(url=TWITCH_ID_ENDPOINT, status=200, json_data=profile_data),
        DummyResponse(url=TWITCH_STREAMS_ENDPOINT, status=200, json_data=stream_data),
        DummyResponse(url=TWITCH_FOLLOWS_ENDPOINT, status=200, json_data=follows_data),
    ]
    monkeypatch.setattr(
        aiohttp, "ClientSession", lambda *args, **kwargs: DummySession()
    )
    dummy_bot = MagicMock()
    twitch_stream = TwitchStream(
        _bot=dummy_bot,
        name="dummy_login",
        channels=[],
        messages=[],
        token="dummy_client_id",
    )
    result = await twitch_stream.is_online()
    embed, is_rerun = result
    assert isinstance(embed, discord.Embed)
    assert embed.title == "Dummy Twitch Live Stream"
    assert embed.url == "https://www.twitch.tv/dummy_login"
    assert embed.author.name == "dummy_stream"
    field_names = [field.name for field in embed.fields]
    assert "Followers" in field_names
    assert "Total views" in field_names
    fields_dict = {field.name: field.value for field in embed.fields}
    assert fields_dict["Followers"] == humanize_number(2000)
    assert fields_dict["Total views"] == humanize_number(100)
    assert embed.thumbnail.url == "http://example.com/profile.png"
    assert embed.image.url.startswith("http://example.com/thumbnail.jpg")
    assert is_rerun is False


@pytest.mark.asyncio
async def test_youtube_scheduled_stream(monkeypatch):
    """
    Test that YoutubeStream.is_online() returns a valid embed for a scheduled stream.
    This simulates a YouTube channel with a scheduled stream (scheduledStartTime set in the future)
    and verifies that the returned embed correctly displays that the stream will start in the future,
    sets the embed timestamp accordingly, and flags the stream as scheduled.
    """
    scheduled_time = datetime.now(timezone.utc) + timedelta(hours=1)
    scheduled_time_iso = scheduled_time.isoformat()
    xml_feed = '<feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015"><entry><yt:videoId>dummy_scheduled_vid</yt:videoId></entry></feed>'
    video_response_data_initial = {
        "items": [
            {
                "id": "dummy_scheduled_vid",
                "snippet": {
                    "title": "Test Scheduled Stream",
                    "channelTitle": "Scheduled Channel",
                    "thumbnails": {
                        "medium": {"url": "http://example.com/sched_thumb.jpg"}
                    },
                },
                "liveStreamingDetails": {"scheduledStartTime": scheduled_time_iso},
            }
        ]
    }
    video_response_data_final = video_response_data_initial.copy()
    DummySession.responses = [
        DummyResponse(
            url=YOUTUBE_CHANNEL_RSS.format(channel_id="dummy_channel_id"),
            status=200,
            text_data=xml_feed,
        ),
        DummyResponse(
            url=YOUTUBE_VIDEOS_ENDPOINT,
            status=200,
            json_data=video_response_data_initial,
        ),
        DummyResponse(
            url=YOUTUBE_VIDEOS_ENDPOINT, status=200, json_data=video_response_data_final
        ),
    ]
    monkeypatch.setattr(
        aiohttp, "ClientSession", lambda *args, **kwargs: DummySession()
    )
    dummy_bot = MagicMock()
    dummy_config = MagicMock()
    yt_stream = YoutubeStream(
        _bot=dummy_bot,
        name="dummy_channel",
        channels=[],
        messages=[],
        config=dummy_config,
        token={"api_key": "dummy_api_key"},
        id="dummy_channel_id",
    )
    embed, is_schedule = await yt_stream.is_online()
    assert isinstance(embed, discord.Embed)
    assert embed.title == "Test Scheduled Stream"
    assert embed.url == "https://youtube.com/watch?v=dummy_scheduled_vid"
    assert "will start" in embed.description
    delta = abs((embed.timestamp - scheduled_time).total_seconds())
    assert delta < 1
    assert embed.author.name == "Scheduled Channel"
    assert is_schedule is True


@pytest.mark.asyncio
async def test_twitch_invalid_credentials(monkeypatch):
    """
    Test that TwitchStream.is_online() raises InvalidTwitchCredentials when the Twitch streams endpoint
    returns a 400 error code. This simulates invalid credentials for Twitch.
    """
    DummySession.responses = [
        DummyResponse(url=TWITCH_STREAMS_ENDPOINT, status=400, json_data={})
    ]
    monkeypatch.setattr(
        aiohttp, "ClientSession", lambda *args, **kwargs: DummySession()
    )
    from unittest.mock import MagicMock

    dummy_bot = MagicMock()
    twitch_stream = TwitchStream(
        _bot=dummy_bot,
        name="dummy_login",
        channels=[],
        messages=[],
        token="dummy_client_id",
        id="dummy_id",
        bearer="dummy_bearer",
    )
    with pytest.raises(InvalidTwitchCredentials):
        await twitch_stream.is_online()
