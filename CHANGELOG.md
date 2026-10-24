# Changelog

Versioning follows [SemVer](https://semver.org/). The version in
`custom_components/sdui/manifest.json` must match the release tag (`v1.0.0` ↔ `1.0.0`,
`v1.0.0b1` ↔ `1.0.0b1` for betas; publish betas as GitHub pre-release).

## 1.0.0b2 (beta)

- Setup dialog reworked: clearer titles and texts, result count in the school list, dropdown for many hits
- Timetable of the last 7 days is fetched too (calendar shows the past week)

## 1.0.0b1 (beta)

First release as standalone integration `sdui` (successor of
[ToperGamesYT/sdui_timetable](https://github.com/ToperGamesYT/sdui_timetable)).

- Login with school, username and password ("Einloggen mit Sdui" / Keycloak OIDC and classic login)
- Automatic token renewal (refresh token, fallback to new login), re-authentication flow
- One entry per account; parent accounts select their children, one device per student
- Per student: calendar, next lesson, lessons today, substitutions/cancellations today and on the
  next school day, next school day, school start/end today and on the next school day
- Event `sdui_timetable_change` for new substitutions/cancellations
- School search in the setup dialog (name, city or slink)
- Children of parent accounts detected via /users/self/family; timetable ids can also be entered manually
- Diagnostics download with the shape of the API responses (no personal data)
- German and English translations

Breaking: new domain `sdui`, manual Bearer-token mode removed. Remove old `sdui_timetable` entries first.
