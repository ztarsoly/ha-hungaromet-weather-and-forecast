"""Tests for the radar frames and animations (radar.py, national_radar.py, image.py)."""

# The tests check the integration's internals
# pylint: disable=protected-access

from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest
import requests
from PIL import Image

from custom_components.hungaromet_weather_and_forecast.image import HungarometRadarImage
from custom_components.hungaromet_weather_and_forecast.national_radar import (
    get_latest_image_urls,
    newest_frame_downloads,
)
from custom_components.hungaromet_weather_and_forecast.radar import (
    RadarFrames,
    create_gif,
    download_image,
)

from .entries import loaded_entry

DOWNLOAD_IMAGE = "custom_components.hungaromet_weather_and_forecast.radar.download_image"
NATIONAL = "custom_components.hungaromet_weather_and_forecast.national_radar"


@patch("custom_components.hungaromet_weather_and_forecast.national_radar.requests.get")
def test_get_latest_image_urls_success(mock_get):
    """Test get_latest_image_urls successfully parses HTML."""
    html_content = """
    <html>
        <body>
            <a href="image_003.png">Image 3</a>
            <a href="image_001.png">Image 1</a>
            <a href="image_002.png">Image 2</a>
            <a href="not_an_image.txt">Not an image</a>
        </body>
    </html>
    """
    mock_response = Mock()
    mock_response.text = html_content
    mock_response.raise_for_status = Mock()
    mock_get.return_value = mock_response

    result = get_latest_image_urls("http://example.com/", count=3)

    assert len(result) == 3
    # Should be sorted and return latest (highest numbered) files
    assert "image_001.png" in result[0]
    assert "image_002.png" in result[1]
    assert "image_003.png" in result[2]


@patch("custom_components.hungaromet_weather_and_forecast.national_radar.requests.get")
def test_get_latest_image_urls_removes_duplicates(mock_get):
    """Test get_latest_image_urls removes duplicate entries."""
    html_content = """
    <html>
        <body>
            <a href="image_001.png">Image 1</a>
            <a href="image_001.png">Image 1 duplicate</a>
            <a href="image_002.png">Image 2</a>
        </body>
    </html>
    """
    mock_response = Mock()
    mock_response.text = html_content
    mock_response.raise_for_status = Mock()
    mock_get.return_value = mock_response

    result = get_latest_image_urls("http://example.com/", count=10)

    assert len(result) == 2
    assert "image_001.png" in result[0]
    assert "image_002.png" in result[1]


@patch("custom_components.hungaromet_weather_and_forecast.national_radar.requests.get")
def test_get_latest_image_urls_limits_count(mock_get):
    """Test get_latest_image_urls limits results to specified count."""
    html_content = """
    <html>
        <body>
            <a href="image_001.png">Image 1</a>
            <a href="image_002.png">Image 2</a>
            <a href="image_003.png">Image 3</a>
            <a href="image_004.png">Image 4</a>
            <a href="image_005.png">Image 5</a>
        </body>
    </html>
    """
    mock_response = Mock()
    mock_response.text = html_content
    mock_response.raise_for_status = Mock()
    mock_get.return_value = mock_response

    result = get_latest_image_urls("http://example.com/", count=3)

    assert len(result) == 3
    # Should get the 3 latest (highest numbered)
    assert "image_003.png" in result[0]
    assert "image_004.png" in result[1]
    assert "image_005.png" in result[2]


@patch("custom_components.hungaromet_weather_and_forecast.national_radar.requests.get")
def test_get_latest_image_urls_non_tag_elements(mock_get):
    """Test get_latest_image_urls handles non-Tag elements."""
    html_content = """
    <html>
        <body>
            Some text
            <a href="image_001.png">Image 1</a>
        </body>
    </html>
    """
    mock_response = Mock()
    mock_response.text = html_content
    mock_response.raise_for_status = Mock()
    mock_get.return_value = mock_response

    result = get_latest_image_urls("http://example.com/", count=5)

    assert len(result) == 1
    assert "image_001.png" in result[0]


def _png(color="red"):
    data = BytesIO()
    Image.new("RGB", (10, 10), color=color).save(data, format="PNG")
    return Mock(content=data.getvalue(), raise_for_status=Mock())


@patch("custom_components.hungaromet_weather_and_forecast.radar.requests.get")
def test_download_image_success(mock_get):
    """Test download image success."""
    mock_get.return_value = _png()

    image = download_image("http://example.com/img1.png")

    assert isinstance(image, Image.Image)
    assert image.size == (10, 10)


@patch("custom_components.hungaromet_weather_and_forecast.radar.requests.get")
def test_download_image_handles_failure(mock_get):
    """Test download image handles failure."""
    mock_get.side_effect = requests.RequestException("Network error")

    assert download_image("http://example.com/img1.png") is None


def _urls(*minutes):
    return [f"http://x/f_20261030_10{m:02d}.png" for m in minutes]


def _frame(url):
    """A distinct image per frame; Pillow merges identical frames into one."""
    minute = int(url[-6:-4])
    return Image.new("RGB", (5, 5), color=(minute * 8, 0, 0))


def test_frames_download_only_the_new_frame():
    """The newest frame is appended and the oldest one dropped."""
    frames = RadarFrames()
    with patch(DOWNLOAD_IMAGE, side_effect=_frame) as get:
        frames.update(_urls(0, 5, 10, 15, 20))
        assert get.call_count == 5

        newest, gif = frames.update(_urls(5, 10, 15, 20, 25))

    assert get.call_count == 6
    assert get.call_args.args == ("http://x/f_20261030_1025.png",)
    assert list(frames._images) == _urls(5, 10, 15, 20, 25)
    assert newest == "http://x/f_20261030_1025.png"
    assert _durations(gif) == [1000, 1000, 1000, 1000, 2000]


def test_frames_with_nothing_new_download_nothing():
    """Test frames with nothing new download nothing."""
    frames = RadarFrames()
    frame = Image.new("RGB", (5, 5))
    with patch(DOWNLOAD_IMAGE, return_value=frame) as get:
        frames.update(_urls(0, 5))
        newest, gif = frames.update(_urls(0, 5))
    assert get.call_count == 2
    assert newest == "http://x/f_20261030_1005.png"
    assert gif is not None


def test_frames_retry_a_failed_download():
    """With a frame missing there is no animation; only it is downloaded again."""
    frames = RadarFrames()
    images = {"http://x/f_20261030_1000.png": None}
    with patch(
        DOWNLOAD_IMAGE,
        side_effect=lambda url: images.get(url, Image.new("RGB", (5, 5))),
    ) as get:
        assert frames.update(_urls(0, 5)) == (None, None)
        assert frames.gif is None

        # The failed frame is downloaded again at the next try
        images.clear()
        newest, _ = frames.update(_urls(0, 5))
    assert newest == "http://x/f_20261030_1005.png"
    assert [c.args[0][-8:-4] for c in get.call_args_list] == ["1000", "1005", "1000"]


def test_frames_without_an_animation():
    """Frames that cannot be animated count as a failure."""
    frames = RadarFrames()
    with (
        patch(DOWNLOAD_IMAGE, return_value=Image.new("RGB", (5, 5))),
        patch(
            "custom_components.hungaromet_weather_and_forecast.radar.create_gif",
            return_value=None,
        ),
    ):
        assert frames.update(_urls(0)) == (None, None)


def test_frames_without_any_download():
    """Test frames without any download."""
    frames = RadarFrames()
    with patch(DOWNLOAD_IMAGE, return_value=None):
        assert frames.update(_urls(0)) == (None, None)


def _durations(gif):
    with Image.open(BytesIO(gif)) as img:
        assert img.format == "GIF"
        durations = []
        for frame in range(img.n_frames):
            img.seek(frame)
            durations.append(img.info["duration"])
    return durations


def test_create_gif_success():
    """Test create_gif builds the animation in memory."""
    images = [
        Image.new("RGB", (100, 100), color="red"),
        Image.new("RGB", (100, 100), color="green"),
        Image.new("RGB", (100, 100), color="blue"),
    ]

    gif = create_gif(images, duration=500, end_delay=2000)

    assert _durations(gif) == [500, 500, 2000]


def test_animation_timing():
    """1 s on each frame, and 2 s on the newest one."""
    colors = ["red", "green", "blue", "yellow", "white"]

    gif = create_gif([Image.new("RGB", (10, 10), color=c) for c in colors])

    assert _durations(gif) == [1000, 1000, 1000, 1000, 2000]


@patch("custom_components.hungaromet_weather_and_forecast.national_radar.requests.get")
def test_newest_six_frames_by_default(mock_get):
    """Test newest six frames by default."""
    links = "".join(f'<a href="f_20261030_10{m:02d}.png">x</a>' for m in range(0, 40, 5))
    mock_get.return_value = Mock(text=links)

    urls = get_latest_image_urls("http://x/")

    assert [u[-8:-4] for u in urls] == ["1010", "1015", "1020", "1025", "1030", "1035"]


@pytest.mark.parametrize("image", [Image.new("RGB", (1, 1)), None])
@patch("custom_components.hungaromet_weather_and_forecast.national_radar.requests.get")
def test_newest_frame_downloads(mock_get, image):
    """Test only the newest listed frame is tried."""
    links = "".join(f'<a href="f_20261030_10{m:02d}.png">x</a>' for m in (5, 10, 0))
    mock_get.return_value = Mock(text=links)

    with patch(f"{NATIONAL}.download_image", return_value=image) as download:
        assert newest_frame_downloads("http://x/") is (image is not None)

    download.assert_called_once_with("http://x/f_20261030_1010.png")


@patch("custom_components.hungaromet_weather_and_forecast.national_radar.requests.get")
def test_newest_frame_without_a_listed_one(mock_get):
    """Test there is no frame to download from an empty listing."""
    mock_get.return_value = Mock(text="")

    with patch(f"{NATIONAL}.download_image") as download:
        assert newest_frame_downloads("http://x/") is False

    download.assert_not_called()


def test_create_gif_empty_images_list():
    """Test create_gif handles empty image list."""
    assert create_gif([]) is None


def _national():
    return HungarometRadarImage(SimpleNamespace(data={}, bus=MagicMock()), loaded_entry())


@patch("custom_components.hungaromet_weather_and_forecast.image.get_latest_image_urls")
def test_national_rebuild_uses_the_open_data_listing(mock_get_urls):
    """Test national rebuild uses the open data listing."""
    mock_get_urls.return_value = _urls(0, 5)
    image = _national()
    with patch(DOWNLOAD_IMAGE, side_effect=_frame):
        newest, gif = image._rebuild_gif()

    mock_get_urls.assert_called_once_with(
        "https://odp.met.hu/weather/radar/composite/png/refl2D_pscappi/"
    )
    assert newest == "http://x/f_20261030_1005.png"
    assert _durations(gif) == [1000, 2000]


@patch("custom_components.hungaromet_weather_and_forecast.image.get_latest_image_urls")
def test_rebuild_without_listed_frames(mock_get_urls):
    """Test rebuild without listed frames."""
    mock_get_urls.return_value = []
    image = _national()

    assert image._rebuild_gif() == (None, None)
    assert image._problem == "no frames are listed"


@patch("custom_components.hungaromet_weather_and_forecast.image.get_latest_image_urls")
def test_rebuild_handles_network_error(mock_get_urls):
    """Test rebuild handles network error."""
    mock_get_urls.side_effect = requests.RequestException("Network error")
    image = _national()

    assert image._rebuild_gif() == (None, None)
    assert image._problem == "the frames could not be listed: Network error"
