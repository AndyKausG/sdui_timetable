"""Constants for the Sdui integration."""

DOMAIN = "sdui_timetable"
API_BASE = "https://api.sdui.app/v1"
IDP_RETURN_URL = "https://idp.sdui.app/api/auth/return"
# Keycloak (OIDC) login used by schools with "Einloggen mit Sdui"
IAM_BASE = "https://iam.sdui-group.app"
IAM_CLIENT_ID = "sdui-app"
IAM_REDIRECT_URI = "https://sdui.app/callback"
SCAN_INTERVAL_MINUTES = 30

CONF_TOKEN = "token"
CONF_USER_ID = "user_id"
CONF_SLINK = "slink"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"

PLATFORM_SENSOR = "sensor"
PLATFORM_CALENDAR = "calendar"
