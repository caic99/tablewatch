#!/usr/bin/env python3
"""Capture one availability snapshot of Shanghai Restaurant Week.

Writes a NEW timestamped file under snapshots/ and never touches existing ones,
so every run adds to the history rather than replacing it.

    python3 rw-data/scrape.py
"""
import json, os, sys, re, html as _html, datetime, urllib.request, urllib.error
import concurrent.futures as cf
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo('Asia/Shanghai')

PROJECT = 'rwcn_autumn_2026'
CITY    = 'shanghai'
KEY     = 'cgecegcegcc'
API     = 'https://api.diningcity.asia/public'
HERE    = os.path.dirname(os.path.abspath(__file__))
SNAPS   = os.path.join(HERE, 'snapshots')
ORDER   = ['brunch', 'lunch', 'dinner']
HEADERS = {'lang': 'zh', 'Accept': 'application/json', 'User-Agent': 'Mozilla/5.0',
           'Origin': 'https://restaurantweek.diningcity.cn'}


_TOKEN = re.compile(r'class="(course-group-zh|course-heaer-zh|desc-zh)"[^>]*>(.*?)</div>', re.S)
_TAG = re.compile(r'<[^>]+>')


def parse_courses(menu_html):
    """[{g: course group, n: dish, d: note}] in menu order, or [] if unparseable."""
    if not menu_html:
        return []
    txt = lambda x: re.sub(r'\s+', ' ', _html.unescape(_TAG.sub('', x))).strip()
    out, group, open_dish = [], '', False
    for kind, body in _TOKEN.findall(menu_html):
        t = txt(body)
        if kind == 'course-group-zh':
            group, open_dish = t, False
        elif kind == 'course-heaer-zh':
            open_dish = bool(t)
            if t:
                out.append({'g': group, 'n': t, 'd': ''})
        elif kind == 'desc-zh':
            # a note belongs to the dish it directly follows; later ones are promo copy
            if open_dish and out:
                out[-1]['d'] = t
            open_dish = False
    return out


def get(url, tries=3):
    for n in range(tries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            return json.loads(urllib.request.urlopen(req, timeout=30).read())
        except Exception:
            if n == tries - 1:
                raise
    return None


def listing(**params):
    q = f'api-key={KEY}&per_page=400&page=1&lang=zh&order_by=featured'
    for k, v in params.items():
        q += f'&{k}={v}'
    d = get(f'{API}/extras_events/{PROJECT}/cities/{CITY}/restaurants?{q}')
    return d if isinstance(d, list) else list(d.values())


def main():
    started = datetime.datetime.now(SHANGHAI).replace(tzinfo=None)   # festival-local, not the runner's clock
    print(f'capturing {PROJECT} / {CITY} at {started:%Y-%m-%d %H:%M}')

    qp = get(f'{API}/extras_events/{PROJECT}/cities/{CITY}/restaurants/query_params'
             f'?platform=web&api-key={KEY}')
    dates = qp.get('dates') or []
    if not dates:
        print('no active dates — the festival is over, nothing to capture'); return   # exit 0 so a scheduled run stays green
    print(f'  {len(dates)} dates: {dates[0]} .. {dates[-1]}')

    master = {str(x['id']): x for x in listing()}
    print(f'  {len(master)} restaurants')

    today = started.date().isoformat()

    def eligible(d, ml):
        # same-day lunch/brunch is past its cut-off and reads as sold out
        return not (d == today and ml != 'dinner')

    jobs = [(d, ml) for d in dates for ml in ORDER]
    avail = {}

    def fetch(job):
        d, ml = job
        rows = listing(date=d, meal_type=ml, seats=2)
        return job, {str(x['id']): x.get('capacity_desc') for x in rows}

    with cf.ThreadPoolExecutor(6) as ex:
        for job, res in ex.map(fetch, jobs):
            avail[job] = res
    print(f'  {len(jobs)} date-service queries done')

    # per-restaurant set menus (titles, prices, minimum party size)
    def meals(rid):
        try:
            d = get(f'{API}/extras_events/{PROJECT}/restaurants/{rid}/meals'
                    f'?api-key={KEY}&platform=web&lang=zh')
            return rid, {m['meal_type']: {'t': m['menu_title'],
                                          'p': (m.get('price') or '').replace('/位', ''),
                                          'min': m.get('minimum_seats'),
                                          'courses': parse_courses(m.get('menu'))} for m in d}
        except Exception:
            return rid, {}

    menus = {}
    with cf.ThreadPoolExecutor(8) as ex:
        for rid, mm in ex.map(meals, master.keys()):
            menus[rid] = mm
    print(f'  {len(menus)} menu lookups done')

    CH = {'more': 'o', 'less': 'f'}
    snap = {'capturedAt': started.isoformat(timespec='minutes'),
            'project': PROJECT, 'city': CITY, 'dates': dates, 'restaurants': {}}

    for rid, x in master.items():
        offered = [m for m in ORDER if m in {mm['meal_type'] for mm in (x.get('meals_with_price') or [])}]
        if not offered:
            offered = [m for m in ORDER if m in menus.get(rid, {})]
        snap['restaurants'][rid] = {
            'name': x['name'], 'dir': x['dirname'],
            'loc': [l['name'] for l in (x.get('locations') or [])],
            'cui': [c['name'] for c in (x.get('cuisines') or [])],
            'pl': x.get('price_level'), 'rating': x.get('ratings_avg'),
            'cap': x.get('capacity_desc'), 'meals': offered,
            'menus': {m: menus.get(rid, {}).get(m, {}) for m in offered},
            'avail': {m: ''.join('x' if not eligible(d, m)
                                 else CH.get(avail[(d, m)].get(rid), 'g') for d in dates)
                      for m in offered}}

    os.makedirs(SNAPS, exist_ok=True)
    out = os.path.join(SNAPS, snap['capturedAt'].replace(':', '-') + '.json')
    if os.path.exists(out):
        sys.exit(f'refusing to overwrite existing snapshot {out}')
    with open(out, 'w') as f:
        json.dump(snap, f, ensure_ascii=False)
    print(f'wrote {out}  ({os.path.getsize(out):,} bytes)')
    print(f'{len(os.listdir(SNAPS))} snapshot(s) on file — now run build.py')


if __name__ == '__main__':
    main()
