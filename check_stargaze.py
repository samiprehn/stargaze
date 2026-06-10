"""Stargaze SD alerts.

Once a day, computes the same sky score Stargaze SD shows on the page
(quality dark hours x Bortle bonus, normalized to the night's seasonal max)
for every site over the next 7 nights. If any night x site clears 90%,
sends a single ntfy notification linking back to the live page. seen.json
caps alerts to once per day.

Mirrors the scoring in index.html: per 15-min astro-dark sample,
cloud_factor x moon_factor partial credit (drive time is ranking-only on
the site and never affects the alert).
"""

import datetime as dt
import json
import math
import os

import requests
from zoneinfo import ZoneInfo

NTFY_TOPIC = os.environ['NTFY_TOPIC']
SITE_URL = 'https://samiprehn.github.io/stargaze/'
SEEN_FILE = 'seen.json'
PACIFIC = ZoneInfo('America/Los_Angeles')

# Threshold: alert if any site x night >= this fraction of the seasonal max.
ALERT_THRESHOLD = 0.90

# Sites — mirrors index.html
SITES = [
    {'name': 'Mt Laguna',                'lat': 32.8694, 'lon': -116.4192, 'bortle': 3, 'driveMin': 60},
    {'name': 'Borrego Springs',          'lat': 33.2588, 'lon': -116.3742, 'bortle': 2, 'driveMin': 120},
    {'name': 'Palomar Mountain',         'lat': 33.3597, 'lon': -116.8639, 'bortle': 3, 'driveMin': 95},
    {'name': 'Cuyamaca Rancho',          'lat': 32.9486, 'lon': -116.5894, 'bortle': 3, 'driveMin': 60},
    {'name': 'Julian',                   'lat': 33.0786, 'lon': -116.6022, 'bortle': 4, 'driveMin': 75},
    {'name': 'Pine Valley',              'lat': 32.7553, 'lon': -116.6094, 'bortle': 4, 'driveMin': 50},
    {'name': 'Los Peñasquitos Ranch House', 'lat': 32.9244, 'lon': -117.1289, 'bortle': 6, 'driveMin': 25},
    {'name': 'Kumeyaay Lake',            'lat': 32.8418, 'lon': -117.0359, 'bortle': 6, 'driveMin': 25},
]

# Window: 8pm to midnight local
WINDOW_START_HOUR = 20
WINDOW_END_HOUR = 24
STEP_MIN = 15
HOURS_PER_STEP = STEP_MIN / 60

# ── Astronomy (suncalc-style port) ───────────────────────────────────
RAD = math.pi / 180
OBLIQUITY = RAD * 23.4397


def to_julian(d):
    return d.timestamp() / 86400 + 2440587.5


def to_days(d):
    return to_julian(d) - 2451545


def solar_mean_anomaly(d):
    return RAD * (357.5291 + 0.98560028 * d)


def ecliptic_longitude(M):
    C = RAD * (1.9148 * math.sin(M) + 0.02 * math.sin(2 * M) + 0.0003 * math.sin(3 * M))
    return M + C + RAD * 102.9372 + math.pi


def sun_coords(d):
    M = solar_mean_anomaly(d)
    L = ecliptic_longitude(M)
    return (
        math.asin(math.sin(OBLIQUITY) * math.sin(L)),  # dec
        math.atan2(math.sin(L) * math.cos(OBLIQUITY), math.cos(L)),  # ra
    )


def moon_coords(d):
    L = RAD * (218.316 + 13.176396 * d)
    M = RAD * (134.963 + 13.064993 * d)
    F = RAD * (93.272 + 13.229350 * d)
    lng = L + RAD * 6.289 * math.sin(M)
    lat = RAD * 5.128 * math.sin(F)
    return (
        math.asin(math.sin(lat) * math.cos(OBLIQUITY) + math.cos(lat) * math.sin(OBLIQUITY) * math.sin(lng)),  # dec
        math.atan2(math.sin(lng) * math.cos(OBLIQUITY) - math.tan(lat) * math.sin(OBLIQUITY), math.cos(lng)),  # ra
        385001 - 20905 * math.cos(M),  # dist (km)
    )


def sidereal_time(d, lw):
    return RAD * (280.16 + 360.9856235 * d) - lw


def altitude_from_ha(H, phi, dec):
    return math.asin(math.sin(phi) * math.sin(dec) + math.cos(phi) * math.cos(dec) * math.cos(H))


def sun_altitude(date, lat, lon):
    d = to_days(date)
    dec, ra = sun_coords(d)
    H = sidereal_time(d, RAD * -lon) - ra
    return altitude_from_ha(H, RAD * lat, dec)


def moon_altitude(date, lat, lon):
    d = to_days(date)
    dec, ra, _ = moon_coords(d)
    H = sidereal_time(d, RAD * -lon) - ra
    return altitude_from_ha(H, RAD * lat, dec)


def moon_illumination(date):
    d = to_days(date)
    s_dec, s_ra = sun_coords(d)
    m_dec, m_ra, m_dist = moon_coords(d)
    sdist = 149598000
    phi = math.acos(math.sin(s_dec) * math.sin(m_dec)
                    + math.cos(s_dec) * math.cos(m_dec) * math.cos(s_ra - m_ra))
    inc = math.atan2(sdist * math.sin(phi), m_dist - sdist * math.cos(phi))
    return (1 + math.cos(inc)) / 2


# ── Cloud layers (Open-Meteo) ────────────────────────────────────────
def fetch_cloud_layers():
    """One multi-location request: hourly low/mid/high cloud cover per site."""
    r = requests.get(
        'https://api.open-meteo.com/v1/forecast',
        params={
            'latitude': ','.join(str(s['lat']) for s in SITES),
            'longitude': ','.join(str(s['lon']) for s in SITES),
            'hourly': 'cloud_cover_low,cloud_cover_mid,cloud_cover_high',
            'forecast_days': 7,
            'timezone': 'America/Los_Angeles',
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def clouds_at(result, local_dt):
    """local_dt must be tz-aware; rounds to nearest hour in Pacific time."""
    rounded = (local_dt + dt.timedelta(minutes=30)).astimezone(PACIFIC)
    key = rounded.strftime('%Y-%m-%dT%H:00')
    h = result['hourly']
    try:
        i = h['time'].index(key)
    except ValueError:
        return None
    return {
        'low': h['cloud_cover_low'][i],
        'mid': h['cloud_cover_mid'][i],
        'high': h['cloud_cover_high'][i],
    }


# ── Scoring (mirrors index.html) ─────────────────────────────────────
def bortle_bonus(b):
    return max(0.3, 1 - (b - 2) * 0.15)


def cloud_factor(c):
    low_mid = min(100, c['low'] + c['mid'])
    return ((1 - low_mid / 100) ** 2) * (1 - 0.4 * c['high'] / 100)


def moon_factor(date, lat, lon):
    alt_deg = moon_altitude(date, lat, lon) * (180 / math.pi)
    if alt_deg <= 0:
        return 1
    return 1 - moon_illumination(date) * min(1, alt_deg / 40)


def night_window(noon_local):
    start = noon_local + dt.timedelta(hours=WINDOW_START_HOUR - 12)
    end = noon_local + dt.timedelta(hours=WINDOW_END_HOUR - 12)
    return start, end


def score_night(site_result, lat, lon, bortle, noon_local):
    start, end = night_window(noon_local)
    samples = round((end - start).total_seconds() / 60 / STEP_MIN)
    quality_h = 0.0
    for i in range(samples):
        t = start + dt.timedelta(minutes=i * STEP_MIN)
        sun_alt = sun_altitude(t, lat, lon) * (180 / math.pi)
        if sun_alt >= -18:
            continue
        c = clouds_at(site_result, t)
        if c is None:
            continue
        quality_h += cloud_factor(c) * moon_factor(t, lat, lon) * HOURS_PER_STEP
    return quality_h * bortle_bonus(bortle)


def max_score_for_night(lat, lon, noon_local):
    """Theoretical ceiling: full astro-dark in the window x Bortle 2 bonus (=1)."""
    start, end = night_window(noon_local)
    samples = round((end - start).total_seconds() / 60 / STEP_MIN)
    astro_dark_h = 0.0
    for i in range(samples):
        t = start + dt.timedelta(minutes=i * STEP_MIN)
        sun_alt = sun_altitude(t, lat, lon) * (180 / math.pi)
        if sun_alt < -18:
            astro_dark_h += HOURS_PER_STEP
    return astro_dark_h


# ── State ────────────────────────────────────────────────────────────
def load_state():
    try:
        with open(SEEN_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save_state(state):
    with open(SEEN_FILE, 'w') as f:
        json.dump(state, f, indent=2)


# ── Main ─────────────────────────────────────────────────────────────
def main():
    today = dt.date.today()
    today_iso = today.isoformat()
    state = load_state()
    if state.get('last_alert_date') == today_iso:
        print(f'Already alerted today ({today_iso}); exiting.')
        return

    cloud_data = fetch_cloud_layers()

    best = None
    for n in range(7):
        day = today + dt.timedelta(days=n)
        noon_local = dt.datetime(day.year, day.month, day.day, 12, 0, 0, tzinfo=PACIFIC)
        for s, result in zip(SITES, cloud_data):
            score = score_night(result, s['lat'], s['lon'], s['bortle'], noon_local)
            cap = max_score_for_night(s['lat'], s['lon'], noon_local)
            pct = (score / cap) if cap > 0 else 0
            print(f"  {day.strftime('%a %b %-d')} {s['name']}: {pct*100:.0f}%")
            if best is None or pct > best['pct']:
                best = {'pct': pct, 'site': s, 'day': day, 'score': score}

    if not best or best['pct'] < ALERT_THRESHOLD:
        pct = (best['pct'] * 100) if best else 0
        print(f'No site/night above {int(ALERT_THRESHOLD*100)}% (best was {pct:.0f}%).')
        return

    pct_int = round(best['pct'] * 100)
    day_str = best['day'].strftime('%a %b %-d')
    title = f"🌌 Great stargazing {day_str}"
    message = f"{best['site']['name']} · {pct_int}% · ~{best['score']:.1f}h quality dark · {best['site']['driveMin']} min drive"

    requests.post(
        'https://ntfy.sh/',
        json={
            'topic': NTFY_TOPIC,
            'title': title,
            'message': message,
            'priority': 4,
            'click': SITE_URL,
            'tags': ['stars'],
        },
        timeout=30,
    )
    print(f'Alerted: {message}')

    state['last_alert_date'] = today_iso
    state['last_alert'] = {'site': best['site']['name'], 'day': best['day'].isoformat(), 'pct': round(best['pct'], 3)}
    save_state(state)


if __name__ == '__main__':
    main()
