"""Constants for the Sdui integration."""

DOMAIN = "sdui"
API_BASE = "https://api.sdui.app/v1"
# Keycloak (OIDC) login used by schools with "Einloggen mit Sdui"
IAM_BASE = "https://iam.sdui-group.app"
IAM_CLIENT_ID = "sdui-app"
IAM_REDIRECT_URI = "https://sdui.app/callback"

SCAN_INTERVAL_MINUTES = 30
# Days of timetable fetched per update: today - PAST_DAYS .. today + FETCH_DAYS
FETCH_DAYS = 14
PAST_DAYS = 7  # calendar shows the last week too

CONF_TOKEN = "token"
CONF_SLINK = "slink"
CONF_SCHOOL = "school"  # search term in the setup form
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_ACCOUNT_ID = "account_id"
CONF_STUDENTS = "students"  # {user_id: display name}
CONF_EXTRA_IDS = "extra_ids"  # manually entered timetable ids (setup form only)
CONF_HIDDEN_COURSES = "hidden_courses"  # options: {user_id: [course name, ...]}
CONF_STRIP_CLASS = "strip_class_prefix"  # options: "3d - Deutsch" -> "Deutsch"

# Fired for every new substitution or cancellation
EVENT_CHANGE = "sdui_timetable_change"

KIND_SUBSTITUTION = "SUBSTITUTION"
# The API spells it "CANCLED"; accept the correct spellings as well
KINDS_CANCELLED = ("CANCLED", "CANCELED", "CANCELLED")

PLATFORMS = ["sensor", "calendar"]
