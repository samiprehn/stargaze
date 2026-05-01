# Stargaze SD

When and where to see stars near San Diego, this week.

**Live:** https://samiprehn.github.io/stargaze/

For each of the next 7 nights, picks the best dark-sky site within driving distance and shows:
- Clear-dark hours and forecast cloud cover
- Moon phase and illumination
- Whether the Milky Way's galactic core is above the horizon
- A 0–100% score relative to the night's seasonal ceiling

Sites: Mt Laguna, Borrego Springs, Palomar, Cuyamaca Rancho, Julian, Pine Valley, Los Peñasquitos Ranch House, Kumeyaay Lake.

## How the score works

For each (site, night) the page samples the **8pm–midnight** window in 15-minute steps. A sample counts toward the score only when:

- The sun is below -18° (astronomical twilight)
- AND the moon is below the horizon

Each qualifying sample contributes `(1 - cloud%) × 15min × bortleBonus`, where `bortleBonus` ranges from 1.0 (Bortle 2) to 0.4 (Bortle 6).

The displayed percentage is `score / nightMax`, where `nightMax` is the same calculation under perfect conditions (no clouds, no moon, Bortle 2). Winter nights have a higher ceiling than summer nights because more of the 8pm–midnight window is astronomically dark.

When none of the 7 nights have a moon under 10% illumination, a banner surfaces the next new-moon date.

## Stack

Single-file HTML, fully client-side. No backend, no API keys.

- **US Census Geocoder is not used** — sites are hardcoded with their NWS gridpoints, lat/lon, and Bortle class
- **NWS gridpoint API** — `api.weather.gov/gridpoints/...` for hourly cloud cover per site
- **Inline ephemeris** — compact suncalc-style port for sun/moon position, illumination, and phase. No dependencies.
- Twinkling stars in the background, fixed-position SVG moon icon showing tonight's actual phase

## Run locally

```sh
open index.html
```

NWS sends CORS headers, so opening from `file://` works fine.
