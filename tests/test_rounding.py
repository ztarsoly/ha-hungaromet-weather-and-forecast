"""Tests for rounding the calculated values, halves going away from zero."""

import math
from datetime import datetime

import pytest

from custom_components.hungaromet_weather_and_forecast.forecast_scraper import (
    HUNGARY_TZ,
    ForecastPeriod,
    interpolate_hourly,
    mean_bearing,
)
from custom_components.hungaromet_weather_and_forecast.rounding import (
    round_half_away,
)


@pytest.mark.parametrize(
    ("value", "decimals", "expected"),
    [
        (2.5, 0, 3),
        (-2.5, 0, -3),
        (-2.4, 0, -2),
        (-2.6, 0, -3),
        (-0.5, 0, -1),
        (0.125, 2, 0.13),
        (-0.125, 2, -0.13),
        # Halves that are not exact in binary
        (0.015, 2, 0.02),
        (1.005, 2, 1.01),
        (-2.675, 2, -2.68),
        (2.4999999999999996, 0, 3),
        (math.inf, 2, math.inf),
    ],
)
def test_halves_go_away_from_zero(value, decimals, expected):
    """A half is rounded to the value further from zero."""
    assert round_half_away(value, decimals) == expected


@pytest.mark.parametrize("value", [-0.4, -0.0, 0.0])
def test_zero_has_no_sign(value):
    """Nothing is shown as -0."""
    assert str(round_half_away(value)) == "0.0"


def test_precipitation_split_halves_go_away_from_zero():
    """1 mm over 8 hours is 0.125 mm per hour."""
    rows = [
        ForecastPeriod(
            forecast_time=datetime(2026, 7, 28, hour, tzinfo=HUNGARY_TZ),
            precipitation=1.0,
        )
        for hour in (7, 15)
    ]

    assert interpolate_hourly(rows)[0].precipitation == 0.13


@pytest.mark.parametrize(("start", "end"), [(0, 355), (355, 0)])
def test_a_bearing_west_of_north_rounds_as_its_compass_value(start, end):
    """Halfway between 355 and 0 degrees is 357.5, shown as 358."""
    rows = [
        ForecastPeriod(
            forecast_time=datetime(2026, 7, 28, hour, tzinfo=HUNGARY_TZ),
            wind_bearing=bearing,
        )
        for hour, bearing in ((7, start), (13, end))
    ]

    assert interpolate_hourly(rows)[3].wind_bearing == 358


def test_a_mean_bearing_just_west_of_north():
    """An average below 0 degrees is turned into 0-359 first."""
    assert mean_bearing([350, 354]) == 352
    assert mean_bearing([359.8, 359.8]) == 0
