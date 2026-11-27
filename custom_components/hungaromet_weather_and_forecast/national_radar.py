"""The frames of HungaroMet's open-data national radar composite."""

import requests

from .html_tree import parse_html
from .radar import FRAME_COUNT, REQUEST_TIMEOUT, download_image


def newest_frame_downloads(base_url):
    """Return whether the newest frame listed at base_url can be downloaded.

    Raises OSError (a request's errors included) or ValueError if the frames
    cannot be listed or the frame is no image.
    """
    urls = get_latest_image_urls(base_url, 1)
    return bool(urls) and download_image(urls[0]) is not None


def get_latest_image_urls(base_url, count=FRAME_COUNT):
    """Return the URLs of the newest PNG frames listed at base_url, oldest first."""
    response = requests.get(base_url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    links = (a.get("href", "") for a in parse_html(response.text).find_all("a"))
    png_files = [href for href in links if href.endswith(".png")]

    # Remove duplicates while preserving order
    seen = set()
    unique_png_files = []
    for fname in png_files:
        if fname not in seen:
            unique_png_files.append(fname)
            seen.add(fname)
    unique_png_files.sort(reverse=True)

    latest_files = unique_png_files[:count]
    latest_files.sort()
    return [base_url + fname for fname in latest_files]
