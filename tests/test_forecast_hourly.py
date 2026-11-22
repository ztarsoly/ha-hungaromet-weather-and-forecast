"""Tests for filling the hours between met.hu's 6-hour forecast rows."""

# The tests check the integration's internals
# pylint: disable=protected-access

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.hungaromet_weather_and_forecast import forecast
from custom_components.hungaromet_weather_and_forecast import weather as w
from custom_components.hungaromet_weather_and_forecast.forecast_scraper import (
    HUNGARY_TZ,
    ForecastData,
    ForecastPeriod,
    _derive,
    bearing_to_direction,
    continue_from_recorded,
    current_period,
    find_current_hour,
    interpolate_hourly,
    keep_dropped_row,
)


def _at(day, hour, **values):
    return ForecastPeriod(
        forecast_time=datetime(2026, 10, day, hour, tzinfo=HUNGARY_TZ), **values
    )


def test_temperature_example():
    """13:00 16 °C and 19:00 10 °C give one degree less each hour."""
    hourly = interpolate_hourly([_at(28, 13, temperature=16), _at(28, 19, temperature=10)])

    assert [(p.forecast_time.hour, p.temperature) for p in hourly] == [
        (13, 16.0),
        (14, 15.0),
        (15, 14.0),
        (16, 13.0),
        (17, 12.0),
        (18, 11.0),
        (19, 10),
    ]


def test_values_are_rounded_to_two_decimals():
    """Test values are rounded to two decimals."""
    hourly = interpolate_hourly(
        [_at(28, 13, temperature=16, pressure=1020), _at(28, 19, temperature=15, pressure=1022)]
    )

    assert hourly[1].temperature == 15.83  # 16 - 1/6
    assert hourly[1].pressure == 1020.33
    assert hourly[4].temperature == 15.33


def test_period_values_belong_to_the_whole_period():
    """The condition and precipitation come from the row starting the period."""
    hourly = interpolate_hourly(
        [
            _at(28, 13, weather_condition="rainy", cloud_cover=80, precipitation=3.0),
            _at(28, 19, weather_condition="clear-night", cloud_cover=10, precipitation=0.0),
        ]
    )

    afternoon = hourly[:6]
    assert {p.weather_condition for p in afternoon} == {"rainy"}
    # 3 mm over 6 hours: 0.5 mm per hour, the same total
    assert [p.precipitation for p in afternoon] == [0.5] * 6
    assert hourly[6].weather_condition == "clear-night"


def test_cloud_cover_changes_like_temperature():
    """Test cloud cover changes like temperature."""
    hourly = interpolate_hourly(
        [_at(28, 13, cloud_cover=34), _at(28, 19, cloud_cover=80)]
    )

    assert [p.cloud_cover for p in hourly] == [
        34.0, 41.67, 49.33, 57.0, 64.67, 72.33, 80
    ]


def test_precipitation_split_rounds_to_two_decimals():
    """Test precipitation split rounds to two decimals."""
    hourly = interpolate_hourly([_at(28, 13, precipitation=1.0), _at(28, 19)])
    assert hourly[0].precipitation == 0.17


def test_wind_bearing_turns_the_short_way_round():
    """Test wind bearing turns the short way round."""
    hourly = interpolate_hourly(
        [_at(28, 13, wind_bearing=330), _at(28, 19, wind_bearing=30)]
    )

    assert [p.wind_bearing for p in hourly[:4]] == [330.0, 340.0, 350.0, 0.0]
    assert [p.wind_direction for p in hourly[:4]] == ["nnw", "nnw", "n", "n"]


@pytest.mark.parametrize(
    ("bearing", "direction"),
    [
        (0, "n"),
        (11.2, "n"),
        (12, "nne"),
        (34, "ne"),
        (90, "e"),
        (200, "ssw"),
        (337.6, "nnw"),
        (348.8, "n"),
    ],
)
def test_bearing_to_direction_uses_16_points(bearing, direction):
    """Test bearing to direction uses 16 points."""
    # Like Home Assistant's weather cards: 12° is NNE (met.hu says "északi")
    assert bearing_to_direction(bearing) == direction


def test_missing_values_keep_the_start_value():
    """Test missing values keep the start value."""
    hourly = interpolate_hourly(
        [
            _at(28, 13, temperature=16, wind_bearing=None, wind_direction="nne"),
            _at(28, 19, temperature=None, wind_bearing=90),
        ]
    )

    assert {p.temperature for p in hourly[:6]} == {16}
    assert {p.wind_bearing for p in hourly[:6]} == {None}
    assert {p.wind_direction for p in hourly[:6]} == {"nne"}
    assert hourly[0].precipitation is None


def test_last_row_is_kept_as_one_hour():
    """Test last row is kept as one hour."""
    hourly = interpolate_hourly([_at(28, 13, temperature=16, precipitation=6.0)])
    assert len(hourly) == 1
    only = hourly[0]
    assert only.temperature == 16
    assert only.precipitation == 1.0  # its 6-hour total spread per hour


def test_rows_without_time_are_skipped():
    """Test rows without time are skipped."""
    assert not interpolate_hourly([ForecastPeriod()])


def test_daylight_saving_change_counts_real_hours():
    """On 25 October 01:00 to 07:00 local is 7 real hours (03:00 happens twice)."""
    hourly = interpolate_hourly(
        [_at(25, 1, temperature=0), _at(25, 7, temperature=7)]
    )

    assert len(hourly) == 8  # 7 hours, plus the last row
    assert [p.temperature for p in hourly] == [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7]
    times = [p.forecast_time.astimezone(HUNGARY_TZ).strftime("%H:%M %Z") for p in hourly]
    assert times[:4] == ["01:00 CEST", "02:00 CEST", "02:00 CET", "03:00 CET"]


def test_find_current_hour():
    """Test find current hour."""
    hourly = interpolate_hourly([_at(28, 13, temperature=16), _at(28, 19, temperature=10)])

    now = datetime(2026, 10, 28, 15, 40, tzinfo=HUNGARY_TZ)
    assert find_current_hour(hourly, now).temperature == 14.0
    assert find_current_hour(hourly, datetime(2026, 10, 29, 9, tzinfo=HUNGARY_TZ)) is None


def _data():

    periods = [_at(28, 13, temperature=16), _at(28, 19, temperature=10)]
    hourly = interpolate_hourly(periods)
    return ForecastData(settlement="X", current=hourly[2], periods=periods, hourly=hourly)


def test_current_period_without_any_rows():
    """Test current period without any rows."""
    assert current_period([], [], datetime(2026, 10, 28, 16, tzinfo=HUNGARY_TZ)) is None


def test_hourly_forecast_starts_at_the_current_hour():
    """Test hourly forecast starts at the current hour."""
    data = _data()  # current hour 15:00
    coordinator = MagicMock(data=data, config_entry=MagicMock(entry_id="e1", title="X"))
    with patch.object(w.SingleCoordinatorWeatherEntity, "__init__", return_value=None):
        entity = w.HungarometForecastWeather(coordinator)
    entity.coordinator = coordinator

    hourly = entity._async_forecast_hourly()
    assert hourly[0]["datetime"].startswith("2026-10-28T15:00")
    assert len(hourly) == 5  # 15:00 to 19:00

    # Without a current hour, every hour is listed
    data.current = None
    assert len(entity._async_forecast_hourly()) == 7


def _download(*rows, now):
    """A forecast as parsed from met.hu, with its hours worked out."""
    data = ForecastData(settlement="X", periods=list(rows))
    _derive(data, now)
    return data


def test_dropped_row_is_kept_from_the_previous_download():
    """At 17:22 met.hu has dropped the 13:00 row; 17:00 is still worked out."""
    now = datetime(2026, 10, 28, 17, 22, tzinfo=HUNGARY_TZ)
    previous = _download(
        _at(28, 13, temperature=16, temperature_max=16),
        _at(28, 19, temperature=10),
        now=datetime(2026, 10, 28, 16, 58, tzinfo=HUNGARY_TZ),
    )
    new = _download(_at(28, 19, temperature=10), _at(29, 1, temperature=8), now=now)
    assert new.current.forecast_time.hour == 19  # the bug: no 17:00 hour

    data = keep_dropped_row(new, previous, now)

    assert [p.forecast_time.hour for p in data.periods] == [13, 19, 1]
    assert (data.current.forecast_time.hour, data.current.temperature) == (17, 12.0)
    assert data.hourly[0].forecast_time.hour == 13
    # Today's maximum from the dropped row is kept too
    assert data.daily[0].temperature_max == 16


def test_no_previous_download_leaves_the_forecast_unchanged():
    """Test no previous download leaves the forecast unchanged."""
    now = datetime(2026, 10, 28, 17, 22, tzinfo=HUNGARY_TZ)
    new = _download(_at(28, 19, temperature=10), now=now)
    assert keep_dropped_row(new, None, now) is new
    assert [p.forecast_time.hour for p in new.periods] == [19]


@pytest.mark.parametrize(
    "previous_rows",
    [
        # The same rows: nothing was dropped
        [_at(28, 19, temperature=10)],
        # Yesterday's rows have ended and are not today's
        [_at(27, 13, temperature=4)],
    ],
)
def test_only_running_or_todays_rows_are_kept(previous_rows):
    """Test only running or today's rows are kept."""
    now = datetime(2026, 10, 28, 17, 22, tzinfo=HUNGARY_TZ)
    previous = _download(*previous_rows, now=now)
    new = _download(_at(28, 19, temperature=10), now=now)

    data = keep_dropped_row(new, previous, now)

    assert [p.forecast_time.hour for p in data.periods] == [19]


def test_todays_minimum_and_maximum_survive_the_evening():
    """At 20:52 met.hu's table starts at 19:00; 07:00 and 13:00 are gone."""
    morning = datetime(2026, 10, 29, 10, 7, tzinfo=HUNGARY_TZ)
    previous = _download(
        _at(28, 19, temperature=12),  # yesterday's, ended at 01:00
        _at(29, 1, temperature=10),
        _at(29, 7, temperature=9, temperature_min=8),
        _at(29, 13, temperature=16, temperature_max=19),
        _at(29, 19, temperature=13),
        now=morning,
    )
    now = datetime(2026, 10, 29, 20, 52, tzinfo=HUNGARY_TZ)
    new = _download(_at(29, 19, temperature=13), _at(30, 1, temperature=10), now=now)
    assert (new.daily[0].temperature_min, new.daily[0].temperature_max) == (13, 13)

    data = keep_dropped_row(new, previous, now)

    assert [p.forecast_time.hour for p in data.periods] == [1, 7, 13, 19, 1]
    today = data.daily[0]
    assert today.forecast_time.day == 29
    assert (today.temperature_min, today.temperature_max) == (8, 19)
    # The current hour is still the evening one
    assert data.current.forecast_time.hour == 20


def test_new_download_without_rows_is_left_alone():
    """Test new download without rows is left alone."""
    now = datetime(2026, 10, 28, 17, 22, tzinfo=HUNGARY_TZ)
    empty = ForecastData(settlement="X")
    previous = _download(_at(28, 13, temperature=16), now=now)
    assert not keep_dropped_row(empty, previous, now).periods
    untimed = ForecastData(settlement="X", periods=[ForecastPeriod()])
    assert keep_dropped_row(untimed, previous, now).periods == [ForecastPeriod()]


def test_daily_forecast_starts_at_today_after_midnight():
    """At midnight last evening's 19:00 row is still kept for the hours until 01:00."""
    rows = (
        _at(28, 19, temperature=10),
        _at(29, 1, temperature=8),
        _at(29, 7, temperature=6),
    )
    data = _download(*rows, now=datetime(2026, 10, 28, 23, 7, tzinfo=HUNGARY_TZ))
    assert [d.forecast_time.day for d in data.daily] == [28, 29]

    # The download at midnight starts the daily forecast at the new day
    data = _download(*rows, now=datetime(2026, 10, 29, 0, 0, tzinfo=HUNGARY_TZ))

    assert [d.forecast_time.day for d in data.daily] == [29]
    assert (data.daily[0].temperature_min, data.daily[0].temperature_max) == (6, 8)
    # The hourly forecast still has the hours before 01:00
    assert data.current.forecast_time.hour == 0
    assert data.current.temperature == 8.33


def test_ended_period_is_not_kept():
    """At 01:07 last evening's 19:00 row has ended, so it is not put back."""
    now = datetime(2026, 10, 29, 1, 7, tzinfo=HUNGARY_TZ)
    previous = _download(
        _at(28, 19, temperature=10),
        _at(29, 1, temperature=8),
        now=datetime(2026, 10, 29, 0, 7, tzinfo=HUNGARY_TZ),
    )
    new = _download(_at(29, 1, temperature=8), _at(29, 7, temperature=6), now=now)

    data = keep_dropped_row(new, previous, now)

    assert [p.forecast_time.hour for p in data.periods] == [1, 7]

async def test_coordinator_keeps_the_dropped_row():
    """Test coordinator keeps the dropped row."""
    now = datetime(2026, 10, 28, 17, 22, tzinfo=HUNGARY_TZ)
    coordinator = object.__new__(forecast.ForecastCoordinator)
    coordinator.hass = MagicMock()
    coordinator.settlement = MagicMock()
    coordinator._store = MagicMock(async_save=AsyncMock())
    coordinator.data = _download(
        _at(28, 13, temperature=16), _at(28, 19, temperature=10), now=now
    )
    new = _download(_at(28, 19, temperature=10), _at(29, 1, temperature=8), now=now)
    with (
        patch.object(forecast, "async_get_clientsession"),
        patch.object(forecast, "fetch_forecast", AsyncMock(return_value=new)),
        patch.object(forecast.dt_util, "now", return_value=now),
    ):
        data = await coordinator._async_update_data()

    assert data.current.forecast_time.hour == 17


BEFORE = datetime(2026, 10, 28, 1, 1, tzinfo=HUNGARY_TZ)
AFTER = datetime(2026, 10, 28, 2, 1, tzinfo=HUNGARY_TZ)


def _night_before(**first):
    """A download at 01:01 with the 01:00 and 07:00 rows."""
    return _download(
        _at(28, 1, temperature=1.0, pressure=1018.0, **first),
        _at(28, 7, temperature=0.0, pressure=1017.0),
        now=BEFORE,
    )


def test_hours_continue_from_the_recorded_values():
    """Test hours continue from the recorded values.

    The new forecast has 6 degrees for 01:00, where 1 was published; the hours
    do not jump to the new rows but run from the recorded value to the 07:00 row.
    """
    new = _download(
        _at(28, 1, temperature=6.0, pressure=1009.0),
        _at(28, 7, temperature=0.0, pressure=1017.0),
        now=AFTER,
    )
    assert new.current.temperature == 5.0

    data = continue_from_recorded(new, _night_before(), AFTER)

    assert data.current.forecast_time.hour == 2
    assert (data.current.temperature, data.current.pressure) == (0.83, 1017.83)
    temperatures = {p.forecast_time.hour: p.temperature for p in data.hourly}
    assert temperatures[1] == 1.0  # recorded
    assert temperatures[4] == 0.5
    assert temperatures[7] == 0.0  # the row itself


def test_hours_after_the_next_row_follow_the_new_rows():
    """Test hours after the next row follow the new rows."""
    new = _download(
        _at(28, 1, temperature=6.0),
        _at(28, 7, temperature=0.0),
        _at(28, 13, temperature=12.0),
        now=AFTER,
    )

    data = continue_from_recorded(new, _night_before(), AFTER)

    assert {p.forecast_time.hour: p.temperature for p in data.hourly}[10] == 6.0


def test_condition_and_precipitation_stay_with_the_new_rows():
    """Test condition and precipitation stay with the new rows."""
    new = _download(
        _at(28, 1, temperature=6.0, weather_condition="rainy", precipitation=6.0),
        _at(28, 7, temperature=0.0),
        now=AFTER,
    )

    data = continue_from_recorded(new, _night_before(weather_condition="sunny"), AFTER)

    assert (data.current.weather_condition, data.current.precipitation) == ("rainy", 1.0)


def test_the_wind_bearing_turns_from_the_recorded_bearing():
    """Test the wind bearing turns from the recorded bearing."""
    previous = _download(
        _at(28, 1, wind_bearing=350.0), _at(28, 7, wind_bearing=10.0), now=BEFORE
    )
    new = _download(
        _at(28, 1, wind_bearing=90.0), _at(28, 7, wind_bearing=10.0), now=AFTER
    )

    data = continue_from_recorded(new, previous, AFTER)

    assert (data.current.wind_bearing, data.current.wind_direction) == (353.33, "n")


def test_values_missing_from_a_row_are_left_as_they_are():
    """Test values missing from a row are left as they are."""
    new = _download(
        _at(28, 1, temperature=6.0), _at(28, 7, cloud_cover=50.0), now=AFTER
    )

    data = continue_from_recorded(new, _night_before(), AFTER)

    assert data.current.cloud_cover is None
    assert data.current.temperature == 6.0  # the new rows' own


def test_no_previous_download_keeps_the_hours_from_the_rows():
    """Test no previous download keeps the hours from the rows."""
    new = _download(_at(28, 1, temperature=6.0), _at(28, 7, temperature=0.0), now=AFTER)
    hourly = list(new.hourly)

    assert continue_from_recorded(new, None, AFTER).hourly == hourly
    assert continue_from_recorded(new, _download(now=AFTER), AFTER).hourly == hourly


def test_hours_before_the_last_recorded_one_are_still_recorded_without_a_next_row():
    """Test hours up to the last recorded one stay recorded when no row follows."""
    new = _download(_at(28, 1, temperature=6.0), now=AFTER)

    data = continue_from_recorded(new, _night_before(), AFTER)

    assert [p.temperature for p in data.hourly] == [1.0]


def test_without_a_recorded_hour_before_now_only_the_older_hours_are_recorded():
    """Test without a recorded hour before now, only the older hours are recorded."""
    now = datetime(2026, 10, 28, 10, 30, tzinfo=HUNGARY_TZ)
    new = _download(_at(28, 7, temperature=-1.0), _at(28, 13, temperature=7.0), now=now)

    data = continue_from_recorded(new, _night_before(), now)

    assert {p.forecast_time.hour: p.temperature for p in data.hourly}[7] == 0.0
    assert data.current.temperature == 3.0  # worked out from the new rows


async def test_coordinator_continues_from_the_recorded_values():
    """Test coordinator continues from the recorded values."""
    coordinator = object.__new__(forecast.ForecastCoordinator)
    coordinator.hass = MagicMock()
    coordinator.settlement = MagicMock()
    coordinator._store = MagicMock(async_save=AsyncMock())
    coordinator.data = _night_before()
    new = _download(
        _at(28, 1, temperature=6.0), _at(28, 7, temperature=0.0), now=AFTER
    )
    with (
        patch.object(forecast, "async_get_clientsession"),
        patch.object(forecast, "fetch_forecast", AsyncMock(return_value=new)),
        patch.object(forecast.dt_util, "now", return_value=AFTER),
    ):
        data = await coordinator._async_update_data()

    assert data.current.temperature == 0.83


DEBRECEN_LOCATION = (47.543, 21.648)


def _sun_day(month, day, conditions=("clear-night", "sunny", "sunny", "clear-night")):
    """A day's rows at 01:00, 07:00, 13:00 and 19:00, and the next day's first."""
    rows = [
        ForecastPeriod(
            forecast_time=datetime(2026, month, day, hour, tzinfo=HUNGARY_TZ),
            weather_condition=condition,
        )
        for hour, condition in zip((1, 7, 13, 19), conditions)
    ]
    rows.append(
        ForecastPeriod(
            forecast_time=datetime(2026, month, day + 1, 1, tzinfo=HUNGARY_TZ),
            weather_condition=conditions[0],
        )
    )
    data = ForecastData(settlement="Debrecen", location=DEBRECEN_LOCATION, periods=rows)
    _derive(data, rows[0].forecast_time)
    return data


def _conditions(data):
    return {p.forecast_time.hour: p.weather_condition for p in data.hourly[:24]}


def test_clear_hours_turn_to_night_when_the_sun_sets_before_the_night_row():
    """In November the sun is down from about 16:05 to 06:30."""
    conditions = _conditions(_sun_day(11, 8))

    assert conditions[6] == "clear-night"
    assert conditions[7] == "sunny"
    assert conditions[16] == "sunny"
    assert conditions[17] == "clear-night"
    assert conditions[18] == "clear-night"


def test_clear_hours_stay_sunny_when_the_sun_sets_after_the_night_row():
    """In July the sun is up from about 05:00 to 20:30, past the night rows."""
    conditions = _conditions(_sun_day(7, 1))

    assert conditions[4] == "clear-night"
    assert conditions[5] == "sunny"
    assert conditions[19] == "sunny"
    assert conditions[20] == "sunny"
    assert conditions[21] == "clear-night"


def test_only_clear_hours_follow_the_sun():
    """Test only clear hours follow the sun."""
    data = _sun_day(11, 8, ("fog", "partlycloudy", "rainy", "cloudy"))

    conditions = _conditions(data)
    assert conditions[5] == "fog"
    assert conditions[17] == "rainy"
    assert conditions[22] == "cloudy"


def test_rows_keep_met_hus_condition():
    """The rows are what is saved, and the daily forecast reads them."""
    data = _sun_day(11, 8)

    assert [p.weather_condition for p in data.periods[:4]] == [
        "clear-night", "sunny", "sunny", "clear-night"
    ]
