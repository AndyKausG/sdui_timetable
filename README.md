[![Made for Home Assistant](https://img.shields.io/badge/Made%20for-Home%20Assistant-blue?style=for-the-badge&logo=homeassistant)](https://www.home-assistant.io/)
[![hacs_badge](https://img.shields.io/badge/HACS-Default-orange.svg?style=for-the-badge)](https://github.com/hacs/integration)

# SDUI Timetable
![Logo](/assets/logo.png)

A custom Home Assistant integration to display your school timetable from SDUI.

## Features
- Calendar mode
- Shows the number of lessons for today
- Displays the first lesson with time, subject, and status
- Filters out cancelled lessons
- Updates automatically every 15 minutes

## Installation via HACS
1. Open **HACS → Integrations → Custom repositories**.
2. Add  
   `https://github.com/topergamesyt/sdui_timetable`  
   and select **Integration**.
3. Install the integration.
4. Restart Home Assistant and add the integration via **Settings → Devices & Services → Add Integration → SDUI Timetable**.

## Login

### Username & password (recommended)
Choose **Username & password** and enter:

| Field | Value |
|---|---|
| School | the *slink* of your school, i.e. the part after `sdui.app/` in your school's login page, e.g. `https://sdui.app/my-school/login` → `my-school` (pasting the full URL works too) |
| Username or e-mail | the same login you use on sdui.app |
| Password | your Sdui password |
| Timetable user_id (optional) | leave empty: the integration reads your account; parent accounts get a selection of their children. Add the integration once per child. Alternatively enter the id from `https://sdui.app/timetable/users/<user_id>` |

Supported login variants (detected automatically from the school):
- **"Einloggen mit Sdui"** – Sdui's Keycloak login (`iam.sdui-group.app`, OIDC with PKCE). Access tokens are valid for ~5 minutes and are renewed with the refresh token, falling back to a new login. If your school is not detected, paste the address of the Sdui login page (`https://iam.sdui-group.app/realms/s-...`) into the *School* field instead of the slink.
- the classic Sdui login (username/password directly on sdui.app)

The token is renewed automatically before it expires and whenever the API answers with 401. Credentials are stored in the config entry (like other cloud integrations). If the login fails permanently (e.g. password changed), Home Assistant shows a **re-authenticate** prompt.

Existing token-only entries keep working until the token expires; the re-authenticate prompt then lets you switch to username & password. You can also switch any time via **Configure** (gear icon).

### Bearer token (manual, fallback)
Choose **Bearer token** and enter the token and your user_id. Sdui access tokens are valid for about 5 minutes only, so this mode is only useful for testing.

Find your user id in the link to your timetable:

https:// sdui.app/timetable/users/**your_user_id**?viewType=week&daysToShow=5&startDate=2025-09-01

Find your token in your browser (developer tools → Network → request to `api.sdui.app` → `Authorization: Bearer …`).

![1](/assets/1.png)

![2](/assets/2.png)

![3](/assets/3.png)

![4](/assets/4.png)

## Troubleshooting
Enable debug logging to see each login step:

```yaml
logger:
  logs:
    custom_components.sdui_timetable: debug
```
