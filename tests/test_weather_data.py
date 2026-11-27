"""Tests for weather_data.py"""

# The tests check the integration's internals
# pylint: disable=protected-access

import io
import json
import lzma
import zipfile
from unittest.mock import Mock, patch

import pytest

from custom_components.hungaromet_weather_and_forecast.weather_data import (
    _get_reference_coords,
    fetch_data,
    clean_data,
    add_distance_column,
    calculate_mean_values,
    haversine,
    latest_observations,
    parse_csv,
    station_records,
    to_number,
    process_daily_data,
    process_hourly_data,
    process_ten_minutes_data,
    select_stations,
)

from .rows import column, table


def test_haversine_same_location():
    """Test haversine distance for same location is zero."""
    distance = haversine(47.5, 19.0, 47.5, 19.0)
    assert distance == pytest.approx(0.0, abs=0.1)


def test_haversine_known_distance():
    """Test haversine with known distance (Budapest to Debrecen approximately 200km)."""
    # Budapest coords: 47.4979, 19.0402
    # Debrecen coords: 47.5316, 21.6273
    distance = haversine(47.4979, 19.0402, 47.5316, 21.6273)
    assert distance == pytest.approx(200, abs=10)  # Roughly 200km


def test_get_reference_coords_from_hass():
    """Test getting reference coordinates from hass."""
    hass = Mock()
    hass.config.latitude = 47.5
    hass.config.longitude = 19.0

    lat, lon = _get_reference_coords(hass)

    assert lat == 47.5
    assert lon == 19.0


def test_get_reference_coords_no_hass_no_local_config():
    """Test getting reference coordinates without hass or local_config raises ValueError."""
    with pytest.raises(ValueError, match="Reference coordinates are unavailable"):
        _get_reference_coords(None)


def test_clean_data():
    """Test clean_data replaces -999 with NA and strips column names."""
    df = table({" Temperature ": [20, -999, 25], " Humidity ": [60, 70, -999]})

    result = clean_data(df)

    assert list(result[0]) == ["Temperature", "Humidity"]
    assert column(result, "Temperature") == [20, None, 25]
    assert column(result, "Humidity") == [60, 70, None]


def test_clean_data_strips_station_names():
    """Test clean_data removes the padding around station names."""
    df = table({" StationName ": ["Debrecen Kismacs        ", " Budapest "]})

    result = clean_data(df)

    assert column(result, "StationName") == ["Debrecen Kismacs", "Budapest"]


def test_add_distance_column():
    """Test add_distance_column calculates distances correctly."""
    df = table({
        "Latitude": [47.5, 47.6, 47.4],
        "Longitude": [19.0, 19.1, 18.9],
        "StationName": ["A", "B", "C"],
    })

    result = add_distance_column(df, 47.5, 19.0)

    near, second, third = column(result, "Distance_km")
    assert near == pytest.approx(0.0, abs=0.1)
    assert second > 0
    assert third > 0


def test_calculate_mean_values_all_valid():
    """Test calculate_mean_values with all valid data."""
    df = table({
        "temp": [20, 22, 21],
        "humidity": [60, 70, 65],
        "pressure": [1013, 1015, 1014],
    })

    result = calculate_mean_values(df, ["temp", "humidity", "pressure"])

    assert result["temp"] == pytest.approx(21.0, abs=0.1)
    assert result["humidity"] == pytest.approx(65.0, abs=0.1)
    assert result["pressure"] == pytest.approx(1014.0, abs=0.1)


def test_calculate_mean_values_with_na():
    """Test calculate_mean_values skips NA values."""
    df = table({"temp": [20, None, 22], "humidity": [60, 70, None]})

    result = calculate_mean_values(df, ["temp", "humidity"])

    assert result["temp"] == pytest.approx(21.0, abs=0.1)
    assert result["humidity"] == pytest.approx(65.0, abs=0.1)


def test_calculate_mean_values_all_na():
    """Test calculate_mean_values returns None for all NA."""
    df = table({"temp": [None, None, None]})

    result = calculate_mean_values(df, ["temp"])

    assert result["temp"] is None


def test_calculate_mean_values_missing_column():
    """Test calculate_mean_values skips missing columns."""
    df = table({"temp": [20, 22, 21]})

    result = calculate_mean_values(df, ["temp", "missing_col"])

    assert "temp" in result
    assert "missing_col" not in result
    assert result["temp"] == pytest.approx(21.0, abs=0.1)


@patch("custom_components.hungaromet_weather_and_forecast.weather_data.requests.get")
def test_fetch_data_success(mock_get):
    """Test fetch_data successfully downloads and parses data."""
    # Create a CSV content
    csv_content = """Time;StationNumber;Temperature
20240101;1234;20.5
20240101;5678;21.3"""

    # Create a zip file in memory
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zipf:
        zipf.writestr("data.csv", csv_content)
    zip_buffer.seek(0)

    # Mock the response
    mock_response = Mock()
    mock_response.content = zip_buffer.getvalue()
    mock_response.raise_for_status = Mock()
    mock_get.return_value = mock_response

    result = fetch_data("http://example.com/data.zip")

    assert result == [
        {"Time": 20240101, "StationNumber": 1234, "Temperature": 20.5},
        {"Time": 20240101, "StationNumber": 5678, "Temperature": 21.3},
    ]


@patch("custom_components.hungaromet_weather_and_forecast.weather_data.requests.get")
def test_fetch_data_http_error(mock_get):
    """Test fetch_data raises error on HTTP failure."""
    mock_response = Mock()
    mock_response.raise_for_status.side_effect = Exception("HTTP Error")
    mock_get.return_value = mock_response

    with pytest.raises(Exception, match="HTTP Error"):
        fetch_data("http://example.com/data.zip")


def _archive(files):
    """Return the bytes of a ZIP file with the given files."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def _feed_archive(offset=None, value=b""):
    """Return a feed's ZIP file, optionally overwritten in its two file headers.

    offset counts from the start of the local header; the central directory has
    the same field two bytes further on.
    """
    content = bytearray(_archive({"data.csv": "a;b\n" + "1;2\n" * 500}))
    if offset is not None:
        for start in (0, content.index(b"PK\x01\x02") + 2):
            content[start + offset : start + offset + len(value)] = value
    return bytes(content)


def _damaged_archive():
    """Return a feed's ZIP file whose compressed data starts with nonsense."""
    content = bytearray(_feed_archive())
    start = 30 + len("data.csv")
    content[start : start + 12] = b"\xff" * 12
    return bytes(content)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (_archive({}), "ZIP file is empty"),
        (_damaged_archive(), "cannot be read: Error -3"),
        # The flag of an encrypted file
        (_feed_archive(6, b"\x01\x00"), "cannot be read: .*password required"),
        # A compression method zipfile does not know
        (_feed_archive(8, b"\x63\x00"), "cannot be read: .*compression method"),
        # A newer ZIP version than zipfile reads; found when opening the file
        (_feed_archive(4, b"\x8c\x00"), "cannot be read: zip file version"),
        # Marked as Zstandard compression, which the data is not
        (_feed_archive(8, b"\x5d\x00"), "cannot be read"),
    ],
    ids=[
        "empty",
        "damaged",
        "encrypted",
        "unknown compression",
        "unknown version",
        "not zstandard",
    ],
)
@patch("custom_components.hungaromet_weather_and_forecast.weather_data.requests.get")
def test_fetch_data_reports_an_unusable_zip_file(mock_get, content, message):
    """A ZIP file without a readable file is an unreadable download."""
    mock_get.return_value = Mock(content=content)

    with pytest.raises(ValueError, match=message):
        fetch_data("http://example.com/data.zip")


@patch("custom_components.hungaromet_weather_and_forecast.weather_data.requests.get")
def test_fetch_data_reports_an_answer_that_is_no_zip_file(mock_get):
    """An answer such as a maintenance page raises zipfile's own error."""
    mock_get.return_value = Mock(content=b"<html>maintenance</html>")

    with pytest.raises(zipfile.BadZipFile):
        fetch_data("http://example.com/data.zip")


@pytest.mark.parametrize("error", [EOFError(), lzma.LZMAError("Corrupt input data")])
@patch("custom_components.hungaromet_weather_and_forecast.weather_data.requests.get")
def test_fetch_data_reports_cut_off_or_corrupt_data(mock_get, error):
    """Other errors of reading the file are reported the same way."""
    mock_get.return_value = Mock(content=_feed_archive())

    # An error without a message is named
    expected = f"cannot be read: {str(error) or 'EOFError'}"
    with (
        patch.object(zipfile.ZipFile, "read", side_effect=error),
        pytest.raises(ValueError, match=expected),
    ):
        fetch_data("http://example.com/data.zip")


@patch("custom_components.hungaromet_weather_and_forecast.weather_data.fetch_data")
def test_process_daily_data(mock_fetch):
    """Test process_daily_data processes data correctly."""
    mock_df = table({
        "Time": [20240101, 20240101],
        "StationNumber": [1234, 5678],
        "StationName": ["Station A", "Station B"],
        "Latitude": [47.5, 47.6],
        "Longitude": [19.0, 19.1],
        "Elevation": [100, 110],
        "rau": [5.0, 7.0],
        "upe": [2.0, 3.0],
        "t": [20.0, 22.0],
        "tn": [15.0, 16.0],
        "tx": [25.0, 27.0],
        "sr": [800, 850],
        "et5": [10, 12],
        "et10": [11, 13],
        "et20": [12, 14],
        "et50": [13, 15],
        "et100": [14, 16],
        "tsn24": [5, 6],
    })
    mock_fetch.return_value = mock_df

    hass = Mock()
    hass.config.latitude = 47.5
    hass.config.longitude = 19.0

    result, station_info = process_daily_data(hass, distance_km=50.0)

    assert "time" in result
    assert result["time"] == "2024-01-01"
    assert "average_t" in result
    assert "average_rau" in result
    assert "average_upe" in result
    assert len(station_info) > 0


@patch("custom_components.hungaromet_weather_and_forecast.weather_data.fetch_data")
def test_process_hourly_data(mock_fetch):
    """Test process_hourly_data processes data correctly."""
    mock_df = table({
        "Time": [202401011200, 202401011200],
        "StationNumber": [1234, 5678],
        "StationName": ["Station A", "Station B"],
        "Latitude": [47.5, 47.6],
        "Longitude": [19.0, 19.1],
        "Elevation": [100, 110],
        "r": [0.5, 1.0],
        "t": [20.0, 22.0],
        "ta": [19.0, 21.0],
        "tn": [15.0, 16.0],
        "tx": [25.0, 27.0],
        "u": [60, 65],
        "sg": [5, 6],
        "sr": [100, 110],
        "suv": [1, 2],
        "fs": [5.0, 6.0],
        "fsd": [180, 190],
        "fx": [8.0, 9.0],
        "fxd": [200, 210],
        "f": [4.0, 5.0],
        "fd": [170, 180],
        "we": [1, 1],
        "et5": [10, 12],
        "et10": [11, 13],
        "et20": [12, 14],
        "et50": [13, 15],
        "et100": [14, 16],
        "tsn": [5, 6],
        "tviz": [10000, 12000],
    })
    mock_fetch.return_value = mock_df

    hass = Mock()
    hass.config.latitude = 47.5
    hass.config.longitude = 19.0

    result, station_info = process_hourly_data(hass, distance_km=50.0)

    assert "time" in result
    assert "average_t" in result
    assert "average_r" in result
    assert "average_u" in result
    assert "we" in result
    assert result["we"] == 1
    assert len(station_info) > 0


@patch("custom_components.hungaromet_weather_and_forecast.weather_data.fetch_data")
def test_process_ten_minutes_data(mock_fetch):
    """Test process_ten_minutes_data processes data correctly."""
    mock_df = table({
        "Time": [202401011230, 202401011230],
        "StationNumber": [1234, 5678],
        "StationName": ["Station A", "Station B"],
        "Latitude": [47.5, 47.6],
        "Longitude": [19.0, 19.1],
        "Elevation": [100, 110],
        "r": [0.5, 1.0],
        "t": [20.0, 22.0],
        "ta": [19.0, 21.0],
        "tn": [15.0, 16.0],
        "tx": [25.0, 27.0],
        "u": [60, 65],
        "sg": [5, 6],
        "sr": [50, 60],
        "suv": [1, 2],
        "fs": [5.0, 6.0],
        "fsd": [180, 190],
        "fx": [8.0, 9.0],
        "fxd": [200, 210],
        "et5": [10, 12],
        "et10": [11, 13],
        "et20": [12, 14],
        "et50": [13, 15],
        "et100": [14, 16],
        "tsn": [5, 6],
        "tviz": [10000, 12000],
    })
    mock_fetch.return_value = mock_df

    hass = Mock()
    hass.config.latitude = 47.5
    hass.config.longitude = 19.0

    result, station_info = process_ten_minutes_data(hass, distance_km=50.0)

    assert "time" in result
    assert "average_t" in result
    assert "average_r" in result
    assert "average_fs" in result
    assert len(station_info) > 0


@patch("custom_components.hungaromet_weather_and_forecast.weather_data.fetch_data")
def test_process_hourly_data_empty_we(mock_fetch):
    """Test process_hourly_data with empty 'we' column."""
    mock_df = table({
        "Time": [202401011200],
        "StationNumber": [1234],
        "StationName": ["Station A"],
        "Latitude": [47.5],
        "Longitude": [19.0],
        "Elevation": [100],
        "r": [0.5],
        "t": [20.0],
        "ta": [19.0],
        "tn": [15.0],
        "tx": [25.0],
        "u": [60],
        "sg": [5],
        "sr": [100],
        "suv": [1],
        "fs": [5.0],
        "fsd": [180],
        "fx": [8.0],
        "fxd": [200],
        "f": [4.0],
        "fd": [170],
        "we": [None],  # Empty we value
        "et5": [10],
        "et10": [11],
        "et20": [12],
        "et50": [13],
        "et100": [14],
        "tsn": [5],
        "tviz": [10000],
    })
    mock_fetch.return_value = mock_df

    hass = Mock()
    hass.config.latitude = 47.5
    hass.config.longitude = 19.0

    result, _ = process_hourly_data(hass, distance_km=50.0)

    assert result["we"] is None


def test_parse_csv_types_each_column():
    """Fields are trimmed; a column is whole numbers, numbers or texts."""
    rows = parse_csv(
        "/ a comment\n"
        "    Time;StationName   ;   r;    t; fud;empty;EOR\n"
        "202401011200;Pécs     ;   1;  1.5; DDK;     ;EOR\n"
        "\n"
        "202401011200;Győr     ;    ; -999;-999;     ;EOR\n"
        "202401011200;Short\n"
    )

    assert rows[0] == {
        "Time": 202401011200, "StationName   ": "Pécs", "r": 1.0, "t": 1.5,
        "fud": "DDK", "empty": None, "EOR": "EOR",
    }
    # A column with a blank has no whole numbers
    assert isinstance(rows[0]["r"], float)
    assert (rows[1]["r"], rows[1]["t"], rows[1]["fud"]) == (None, -999, "-999")
    assert rows[2]["StationName   "] == "Short"
    assert rows[2]["t"] is None


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "empty"),
        ("/ only a comment\n", "empty"),
        ("a;b\n1;2;3\n", "more fields"),
        # A field longer than the csv module reads
        ("a\n" + "x" * 200_000, "not valid CSV"),
    ],
)
def test_parse_csv_rejects_an_unreadable_feed(text, message):
    """An unusable file is a ValueError, which counts as a failed download."""
    with pytest.raises(ValueError, match=message):
        parse_csv(text)


@pytest.mark.parametrize(
    ("value", "number"),
    [
        (5, 5), (2.5, 2.5), ("7", 7), ("7.5", 7.5),
        ("n/a", None), (None, None), (float("nan"), None), ("nan", None),
    ],
)
def test_to_number(value, number):
    """Only numbers and number texts are numbers."""
    assert to_number(value) == number


def test_latest_observations_without_a_time():
    """Rows without an observation time are never the newest ones."""
    assert not latest_observations([{"Time": None}])
    assert latest_observations([{"Time": None}, {"Time": 2}]) == [{"Time": 2}]


def test_station_records_drop_repeated_rows():
    """A repeated row is listed once; long fractions are shortened."""
    row = {"StationName": "A", "Distance_km": 1 / 3}

    assert station_records([row, dict(row)]) == [
        {"StationName": "A", "Distance_km": 0.3333333333}
    ]


def _stations_df():
    return table({
        "StationNumber": [1, 2, 3],
        "StationName": ["Near", "Mid", "Far"],
        "Latitude": [47.55, 47.7, 48.5],
        "Longitude": [19.0, 19.0, 19.0],
    })


def test_select_stations_keeps_stations_in_range_sorted():
    """Stations within the radius are kept, nearest first."""
    df = _stations_df()[::-1]

    result = select_stations(df, 47.5, 19.0, 30)

    assert column(result, "StationName") == ["Near", "Mid"]


def test_select_stations_falls_back_to_nearest():
    """When no station is in range, the nearest one is used."""
    df = _stations_df()[1:]

    result = select_stations(df, 47.5, 19.0, 5)

    assert column(result, "StationName") == ["Mid"]


def _with_readings(*temperatures):
    return [{**row, "t": t} for row, t in zip(_stations_df(), temperatures)]


def test_select_stations_leaves_out_stations_without_data():
    """A station with no readings is not available, even in range."""
    df = _with_readings(None, 5.0, 6.0)

    result = select_stations(df, 47.5, 19.0, 30)

    assert column(result, "StationName") == ["Mid"]


def test_select_stations_uses_nearest_with_data_when_the_only_one_in_range_has_none():
    """Only the nearest station is in range but has no data: the nearest with data is used."""
    df = _with_readings(None, 5.0, 6.0)

    result = select_stations(df, 47.5, 19.0, 5)

    assert column(result, "StationName") == ["Mid"]


def test_select_stations_raises_when_no_station_has_data():
    """A clear error is raised when every station is without data."""
    df = _with_readings(None, None, None)

    with pytest.raises(ValueError, match="has data"):
        select_stations(df, 47.5, 19.0, 20)


def test_select_stations_ignores_stations_without_coordinates():
    """Stations with unparseable coordinates are never selected."""
    df = _stations_df()
    for row, latitude in zip(df, ["n/a", "47.7", "48.5"]):
        row["Latitude"] = latitude

    result = select_stations(df, 47.5, 19.0, 5)

    assert column(result, "StationName") == ["Mid"]


def test_select_stations_raises_without_valid_coordinates():
    """A clear error is raised when no station has coordinates."""
    df = [{**row, "Latitude": None} for row in _stations_df()]

    with pytest.raises(ValueError, match="valid coordinates"):
        select_stations(df, 47.5, 19.0, 20)


@patch("custom_components.hungaromet_weather_and_forecast.weather_data.fetch_data")
def test_process_ten_minutes_data_uses_nearest_when_none_in_range(mock_fetch):
    """Processing succeeds with the nearest station instead of failing."""
    columns = [
        "r", "t", "ta", "tn", "tx", "u", "sg", "sr", "suv", "fs", "fsd", "fx",
        "fxd", "et5", "et10", "et20", "et50", "et100", "tsn", "tviz",
    ]
    mock_df = table({
        "Time": [202401011200, 202401011200],
        "StationNumber": [1, 2],
        "StationName": ["Near", "Far"],
        "Latitude": [47.7, 48.5],
        "Longitude": [19.0, 19.0],
        "Elevation": [100, 110],
        **{col: [1.0, 3.0] for col in columns},
    })
    mock_fetch.return_value = mock_df
    hass = Mock()
    hass.config.latitude = 47.5
    hass.config.longitude = 19.0

    result, station_info = process_ten_minutes_data(hass, distance_km=1)

    assert result["average_t"] == 1.0
    assert [s["StationName"] for s in station_info] == ["Near"]


@patch("custom_components.hungaromet_weather_and_forecast.weather_data.fetch_data")
def test_station_records_keep_every_column_as_json(mock_fetch):
    """Each selected station's full row is returned with JSON-safe values."""
    columns = [
        "r", "t", "ta", "tn", "tx", "u", "sg", "sr", "suv", "fs", "fsd", "fx",
        "fxd", "et5", "et10", "et20", "et50", "et100", "tsn", "tviz",
    ]
    mock_fetch.return_value = table({
        "Time": [202401011200, 202401011200],
        "StationNumber": [2, 1],
        "StationName": ["Far  ", "Near  "],
        "Latitude": [47.7, 47.55],
        "Longitude": [19.0, 19.0],
        "Elevation": [110, 100],
        **{col: [3.0, 1.0] for col in columns},
        "sg": [-999, -999],
    })
    hass = Mock()
    hass.config.latitude = 47.5
    hass.config.longitude = 19.0

    _, rows = process_ten_minutes_data(hass, distance_km=50)

    assert [row["StationName"] for row in rows] == ["Near", "Far"]
    assert rows[0]["t"] == 1.0
    assert rows[0]["sg"] is None
    assert rows[0]["Time"] == 202401011200
    assert rows[0]["Distance_km"] == pytest.approx(5.56, abs=0.01)
    assert json.loads(json.dumps(rows)) == rows
