[![Made for Home Assistant](https://img.shields.io/badge/Made%20for-Home%20Assistant-blue?style=for-the-badge&logo=homeassistant)](https://www.home-assistant.io/)
[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge)](https://github.com/hacs/integration)

# Sdui for Home Assistant
![Logo](/assets/logo.png)

Timetable, substitutions and cancellations from [Sdui](https://sdui.app) in Home Assistant.
Log in with your normal Sdui credentials – parent accounts get all children in one entry.

Not affiliated with Sdui GmbH.

## Features
- Login with school, username and password; the token renews itself
- Parent accounts: select several children, one device per child
- Per student:
  - calendar with all lessons (substitutions/cancellations marked)
  - next lesson, lessons today
  - substitutions/cancellations today and on the next school day
  - next school day, school start/end today and on the next school day (cancelled lessons are ignored)
- Event `sdui_timetable_change` for every new substitution or cancellation (for notifications)
- Updates every 30 minutes, timetable for the next 14 days

## Installation via HACS
1. **HACS → ⋮ → Custom repositories**, add `https://github.com/AndyKausG/sdui_timetable`, type **Integration**.
2. Install **Sdui**, restart Home Assistant.
3. **Settings → Devices & Services → Add Integration → Sdui**.

## Login
| Field | Value |
|---|---|
| School | the *slink* of your school: the part after `sdui.app/` in your school's login page, e.g. `https://sdui.app/my-school/login` → `my-school` (the full URL works too) |
| Username or e-mail | the same login you use on sdui.app |
| Password | your Sdui password |

Parent accounts then choose the children to add. The selection can be changed later via **Configure**.

Supported login variants (detected automatically from the school):
- **"Einloggen mit Sdui"** – Sdui's Keycloak login (`iam.sdui-group.app`, OIDC with PKCE). Access tokens are valid for ~5 minutes and are renewed with the refresh token, falling back to a new login. If your school is not detected, paste the address of the Sdui login page (`https://iam.sdui-group.app/realms/s-...`) into the *School* field.
- the classic Sdui login (username/password directly on sdui.app)

Credentials are stored in the config entry (like other cloud integrations). If the login fails permanently (e.g. password changed), Home Assistant shows a **re-authenticate** prompt.

## Entities (per student)
| Entity | Description |
|---|---|
| `calendar.*_timetable` | all lessons |
| `sensor.*_next_lesson` | current or next lesson (subject), details as attributes |
| `sensor.*_lessons_today` | number of lessons that take place today, list as attribute |
| `sensor.*_changes_today` | number of substitutions/cancellations today, list as attribute |
| `sensor.*_next_school_day` | date of the next day with lessons |
| `sensor.*_changes_next_school_day` | substitutions/cancellations on the next school day |
| `sensor.*_school_start_today` / `_school_end_today` | first/last lesson that takes place |
| `sensor.*_school_start_next_school_day` / `_school_end_next_school_day` | same for the next school day |

Entity ids depend on the language and the student's name.

## Notification example
```yaml
automation:
  - alias: Sdui change
    triggers:
      - trigger: event
        event_type: sdui_timetable_change
    actions:
      - action: notify.mobile_app_phone
        data:
          title: "{{ trigger.event.data.student }}: {{ 'Ausfall' if trigger.event.data.type == 'cancellation' else 'Vertretung' }}"
          message: >
            {{ trigger.event.data.date }} {{ trigger.event.data.hour }}. Stunde
            {{ trigger.event.data.subject }} {{ trigger.event.data.comment or '' }}
```
Event data: `student_id`, `student`, `type` (`substitution`/`cancellation`), `date`, `start`, `end`, `hour`, `subject`, `kind_label`, `teachers`, `rooms`, `comment`.
Events are fired from the second update after a start, so a restart does not repeat known changes.

## Migration from `sdui_timetable`
This integration uses the domain `sdui` and replaces the original `sdui_timetable` integration:
1. Remove the old *SDUI Timetable* entries and uninstall the old integration in HACS.
2. Install this one and add your account once.
Entity ids change; update dashboards and automations.

## Troubleshooting
```yaml
logger:
  logs:
    custom_components.sdui: debug
```

## Credits & license
Based on [ToperGamesYT/sdui_timetable](https://github.com/ToperGamesYT/sdui_timetable) (lesson parsing, calendar idea).
Licensed under GPLv3, see [LICENSE](LICENSE).

## Releases
See [CHANGELOG.md](CHANGELOG.md). Releases are GitHub releases with tag `vX.Y.Z` matching the `version` in `manifest.json`; HACS offers them as updates.
