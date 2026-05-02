"""Stargaze SD alerts.

Once a day, computes the same score Stargaze SD shows on the page
(score normalized to seasonal max %) for every site over the next 7
nights. If any night×site clears 90%, sends a single ntfy notification
linking back to the live page. seen.json caps alerts to once per day.
"""

import datetime as dt
import json
import math
import os
import re

import requests

NTFY_TOPIC = os.environ['NTFY_TOPIC']
SITE_URL = 'https://samiprehn.github.io/stargaze/'
SEEN_FILE = 'seen.json'

# Threshold: alert if any site×night >= this fraction of the seasonal max.
ALERT_THRESHOLD = 0.90

# Sites — mirrors index.html
SITES = [
    {'name': 'Mt Laguna',                'lat': 32.8694, 'lon': -116.4192, 'grid': 'SGX/85,16', 'bortle': 3, 'driveMin': 60},
    {'name': 'Borrego Springs',          'lat': 33.2588, 'lon': -116.3742, 'grid': 'SGX/89,33', 'bortle': 2, 'driveMin': 120},
    {'name': 'Palomar Mountain',         'lat': 33.3597, 'lon': -116.8639, 'grid': 'SGX/72,40', 'bortle': 3, 'driveMin': 95},
    {'name': 'Cuyamaca Rancho',          'lat': 32.9486, 'lon': -116.5894, 'grid': 'SGX/79,21', 'bortle': 3, 'driveMin': 60},
    {'name': 'Julian',                   'lat': 33.0786, 'lon': -116.6022, 'grid': 'SGX/80,26', 'bortle': 4, 'driveMin': 75},
    {'name': 'Pine Valley',              'lat': 32.7553, 'lon': -116.6094, 'grid': 'SGX/77,12', 'bortle': 4, 'driveMin': 50},
    {'name': 'Los Peñasquitos Ranch House', 'lat': 32.9244, 'lon': -117.1289, 'grid': 'SGX/59,23', 'bortle': 6, 'driveMin': 25},
    {'name': 'Kumeyaay Lake',            'lat': 32.8418, 'lon': -117.0359, 'grid': 'SGX/62,19', 'bortle': 6, 'driveMin': 25},
]

UA = {'User-Agent': 'stargaze-alerts (sami.prehn@gmail.com)'}

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
    dec, ra = moon_coords(d)
    H = sidereal_time(d, RAD * -lon) - ra
    return altitude_from_ha(H, RAD * lat, dec)


# ── NWS forecast ─────────────────────────────────────────────────────
_DUR_RE = re.compile(r'P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?)?')


def parse_duration_seconds(iso):
    m = _DUR_RE.match(iso)
    if not m:
        return 0
    d, h, mn = (int(x) if x else 0 for x in m.groups())
    return ((d * 24 + h) * 60 + mn) * 60


def value_at_time(values, target_dt):
    for entry in values:
        start_str, dur_str = entry['validTime'].split('/')
        start = dt.datetime.fromisoformat(start_str.replace('Z', '+00:00'))
        end = start + dt.timedelta(seconds=parse_duration_seconds(dur_str))
        if start <= target_dt < end:
            return entry['value']
    return None


def fetch_grid(grid):
    r = requests.get(f'https://api.weather.gov/gridpoints/{grid}', headers=UA, timeout=30)
    r.raise_for_status()
    props = r.json().get('properties', {})
    return (props.get('skyCover') or {}).get('values', []) or []


# ── Scoring (mirrors index.html) ─────────────────────────────────────
def bortle_bonus(b):
    return max(0.3, 1 - (b - 2) * 0.15)


def night_window(noon_local):
    """Returns (start, end) datetimes for the 8pm-midnight evening window
    relative to local noon-of-day (Pacific time)."""
    start = noon_local + dt.timedelta(hours=WINDOW_START_HOUR - 12)
    end = noon_local + dt.timedelta(hours=WINDOW_END_HOUR - 12)
    return start, end


def score_night(sky_values, lat, lon, bortle, noon_local):
    start, end = night_window(noon_local)
    samples = round((end - start).total_seconds() / 60 / STEP_MIN)
    clear_dark_h = 0.0
    for i in range(samples):
        t = start + dt.timedelta(minutes=i * STEP_MIN)
        sun_alt = sun_altitude(t, lat, lon) * (180 / math.pi)
        if sun_alt >= -18:
            continue
        cloud = value_at_time(sky_values, t)
        if cloud is None:
            continue
        moon_alt = moon_altitude(t, lat, lon) * (180 / math.pi)
        if moon_alt > 0:
            continue
        clear_dark_h += (1 - cloud / 100) * HOURS_PER_STEP
    return clear_dark_h * bortle_bonus(bortle)


def max_score_for_night(lat, lon, noon_local):
    """Theoretical ceiling: full astro-dark in the window × Bortle 2 bonus (=1)."""
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

    # Pacific time (US/Pacific). For SD, naive local-noon-of-day works fine for
    # window math because the sun/moon altitude functions use UTC timestamps.
    pacific = dt.timezone(dt.timedelta(hours=-7))  # PDT in May; fine for evening calc
    grids_cache = {}

    best = None
    for n in range(7):
        day = today + dt.timedelta(days=n)
        noon_local = dt.datetime(day.year, day.month, day.day, 12, 0, 0, tzinfo=pacific)
        for s in SITES:
            if s['grid'] not in grids_cache:
                try:
                    grids_cache[s['grid']] = fetch_grid(s['grid'])
                except Exception as e:
                    print(f"  fetch {s['grid']} failed: {e}")
                    grids_cache[s['grid']] = []
            sky = grids_cache[s['grid']]
            score = score_night(sky, s['lat'], s['lon'], s['bortle'], noon_local)
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
    message = f"{best['site']['name']} · {pct_int}% · ~{best['score']:.1f}h dark · {best['site']['driveMin']} min drive"

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
