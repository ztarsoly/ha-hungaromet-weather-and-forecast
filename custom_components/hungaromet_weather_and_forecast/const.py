"""Constants of the HungaroMet Weather & Forecast integration."""

DOMAIN = "hungaromet_weather_and_forecast"
CONF_DISTANCE_KM = "distance_km"
DEFAULT_DISTANCE_KM = 20
# The settlement name typed in the forms, and the met.hu settlement it resolved to
CONF_FORECAST_SETTLEMENT = "forecast_settlement"
CONF_FORECAST = "forecast"
# Where the weather station sensors measure from: home, or the forecast settlement
CONF_LOCATION = "location"
LOCATION_HOME = "home"
LOCATION_SETTLEMENT = "settlement"
# Entries are locations (weather, forecast, regional radar) unless marked as the
# single, automatically created national radar entry
CONF_ENTRY_TYPE = "entry_type"
ENTRY_TYPE_RADAR = "radar"
RADAR_ENTRY_UNIQUE_ID = "national_radar"
RADAR_ENTRY_TITLE = "Radar"

URL_PROTOCOL = "https://"
URL_BASE = "odp.met.hu"

URL_SYNOPTIC = f"{URL_PROTOCOL}{URL_BASE}/weather/weather_reports/synoptic/hungary"
URL_RADAR = f"{URL_PROTOCOL}{URL_BASE}/weather/radar"

URL_DAILY = f"{URL_SYNOPTIC}/daily/csv/HABP_1D_LATEST.csv.zip"
URL_HOURLY = f"{URL_SYNOPTIC}/hourly/csv/HABP_1H_SYNOP_LATEST.csv.zip"
URL_TEN_MINUTES = f"{URL_SYNOPTIC}/10_minutes/csv/HABP_10M_SYNOP_LATEST.csv.zip"
RADAR_BASE_URL = f"{URL_RADAR}/composite/png/refl2D_pscappi/"
