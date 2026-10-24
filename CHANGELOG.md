# Changelog

Versioning follows [SemVer](https://semver.org/). The version in
`custom_components/sdui/manifest.json` must match the release tag (`v1.0.0` ↔ `1.0.0`).

## 1.0.0

First release as standalone integration `sdui` (successor of
[ToperGamesYT/sdui_timetable](https://github.com/ToperGamesYT/sdui_timetable)).

- Login with school, username and password ("Einloggen mit Sdui" / Keycloak OIDC and classic login)
- Automatic token renewal (refresh token, fallback to new login), re-authentication flow
- One entry per account; parent accounts select their children, one device per student
- Per student: calendar, next lesson, lessons today, substitutions/cancellations today and on the
  next school day, next school day, school start/end today and on the next school day
- Event `sdui_timetable_change` for new substitutions/cancellations
- Link to the Sdui school search in the setup dialog
- German and English translations

Breaking: new domain `sdui`, manual Bearer-token mode removed. Remove old `sdui_timetable` entries first.
