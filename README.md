# Stargaze SD

When and where to see stars near San Diego, this week.

**Live:** https://samiprehn.github.io/stargaze/

For each of the next 7 nights, picks the best dark-sky site within driving distance and shows:
- Quality dark hours and forecast cloud cover
- Moon phase and illumination
- Whether the Milky Way's galactic core is above the horizon
- A 0–100% score relative to the night's seasonal ceiling

Sites: Mt Laguna, Borrego Springs, Palomar, Cuyamaca Rancho, Julian, Pine Valley, Los Peñasquitos Ranch House, Kumeyaay Lake.

## How the score works

For each (site, night) the page samples the **8pm–midnight** window in 15-minute steps. Every astro-dark sample (sun below -18°) earns partial credit:

```
cloudFactor = (1 − (low+mid)/100)² × (1 − 0.4 × high/100)
moonFactor  = 1                                  # moon below horizon
            = 1 − illum × min(1, altitude°/40°)  # moon up
credit      = cloudFactor × moonFactor × 15min
```

Low/mid clouds kill stargazing (squared penalty); thin high cirrus only dims it. A bright moon high in the sky wipes out faint targets, but a crescent or a moon barely above the horizon costs much less than the old all-or-nothing skip.

Sky score = quality hours × `bortleBonus` (1.0 at Bortle 2 down to 0.4 at Bortle 6). The displayed percentage is `score / nightMax`, where `nightMax` is the same calculation under perfect conditions (no clouds, no moon, Bortle 2). Winter nights have a higher ceiling than summer nights because more of the 8pm–midnight window is astronomically dark.

**Ranking** (which site wins a night) additionally multiplies by a drive factor: 1.0 on Saturday/Sunday nights, `1 / (1 + (driveMin/60)²)` on weeknights including Friday — so close sites win midweek and the long dark-sky drives only win on weekends. The score bar and the ntfy alert stay drive-independent.

When none of the 7 nights have a moon under 10% illumination, a banner surfaces the next new-moon date.

## Stack

Single-file HTML, fully client-side. No backend, no API keys.

- **Open-Meteo forecast API** — keyless, CORS-friendly; one multi-location request covers all 8 sites with layered cloud cover (low/mid/high)
- **Inline ephemeris** — compact suncalc-style port for sun/moon position, illumination, and phase. No dependencies.
- Twinkling stars in the background, fixed-position SVG moon icon showing tonight's actual phase

## Run locally

```sh
open index.html
```

Open-Meteo sends CORS headers, so opening from `file://` works fine.
