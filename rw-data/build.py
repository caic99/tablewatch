#!/usr/bin/env python3
"""Build the ranking page from every snapshot on file.

Reads all snapshots/*.json, ranks from the newest, and compares against the
previous one so the page can show movement. Older snapshots are never modified.

    python3 rw-data/build.py
"""
import json, os, glob, statistics, datetime

HERE     = os.path.dirname(os.path.abspath(__file__))
SNAPS    = os.path.join(HERE, 'snapshots')
TEMPLATE = os.path.join(os.path.dirname(HERE), 'page_template.html')
OUTPUT   = os.path.join(os.path.dirname(HERE), 'public', 'index.html')

ORDER = ['brunch', 'lunch', 'dinner']
EN    = {'brunch': '早午餐', 'lunch': '午餐', 'dinner': '晚餐'}
TIER  = {'ELITE': 'ELITE', 'RMB148 L - RMB258 D': '¥148/258',
         'RMB118 L - RMB178 D': '¥118/178', 'RMB78 L - RMB118 D': '¥78/118'}
STATE = {'o': 'open', 'f': 'few', 'g': 'gone', 'x': 'na'}


def price(raw):
    """Normalise a set price to ¥1,780 — the listing and meals endpoints disagree
    on whether to include the symbol, and one is a bare number."""
    t = (raw or '').replace('/位', '').replace('¥', '').replace('￥', '').replace(',', '').strip()
    if not t:
        return ''
    try:
        v = float(t)
    except ValueError:
        return raw
    return '¥' + (f'{v:,.2f}'.rstrip('0').rstrip('.') if v % 1 else f'{int(v):,}')
FEW_WEIGHT = 0.25          # 少量剩余 is weak evidence: small counters sit there permanently
CLOSURE_GUARD = 0.60       # only trim recurring closures for broadly-available restaurants
TAIL_MIN_DAYS = 2          # final-days dark run treated as an early exit, not demand
AXIS_START = '2026-09-04'  # tracking began after the 9.3 lunch cut-off, so that day has no lunch data: drop it


def load_snapshots():
    files = sorted(glob.glob(os.path.join(SNAPS, '*.json')))
    if not files:
        raise SystemExit('no snapshots found — run scrape.py first')
    return [json.load(open(f)) for f in files]


def merged(snaps):
    """The newest snapshot on the full festival axis, with masked cells back-filled.

    DiningCity drops each day from its date list once it has passed, so a fresh
    snapshot alone would shrink the strip day by day. Union the dates across all
    snapshots. Any cell the newest snapshot lacks or masks as 'x' (the scraper's
    same-day post-noon cut-off for brunch/lunch) takes the value from the most
    recent snapshot that still recorded it, so past days keep their lunch state."""
    latest = snaps[-1]
    dates = sorted({d for s in snaps for d in s['dates'] if d >= AXIS_START})
    idx = [{d: i for i, d in enumerate(s['dates'])} for s in snaps]
    out = dict(latest, dates=dates, restaurants={})
    for rid, r in latest['restaurants'].items():
        avail, ever_open = {}, {}
        for ml in r['meals']:
            cells, seen_open = [], []
            for d in dates:
                # was this sitting ever observed on sale, in any snapshot?
                seen_open.append('1' if any(
                    d in ix and rid in s['restaurants'] and ml in s['restaurants'][rid]['avail']
                    and s['restaurants'][rid]['avail'][ml][ix[d]] in 'of'
                    for s, ix in zip(snaps, idx)) else '0')
                v = 'x'
                for s, ix in zip(reversed(snaps), reversed(idx)):
                    rr = s['restaurants'].get(rid)
                    if rr and d in ix and ml in rr['avail'] and rr['avail'][ml][ix[d]] != 'x':
                        v = rr['avail'][ml][ix[d]]
                        break
                cells.append(v)
            avail[ml] = ''.join(cells)
            ever_open[ml] = ''.join(seen_open)
        out['restaurants'][rid] = dict(r, avail=avail, everOpen=ever_open)
    return out


def rank(snap):
    """Score every restaurant in one snapshot. Returns (rows, unverified, closed_count)."""
    dates = snap['dates']
    wd = {d: datetime.date(*map(int, d.split('-'))).weekday() for d in dates}
    R = snap['restaurants']

    # market difficulty per (date, service): how easy that slot normally is to get
    ease = {}
    for ml in ORDER:
        pool = [r for r in R.values() if ml in r['meals']]
        if not pool:
            continue
        for i, d in enumerate(dates):
            live = [r for r in pool if r['avail'][ml][i] != 'x']
            if not live:
                continue
            gone = sum(1 for r in live if r['avail'][ml][i] == 'g')
            ease[(d, ml)] = 1 - gone / len(live)
    mean = statistics.mean(ease.values())
    W = {k: v / mean for k, v in ease.items()}

    rows, unverified, closed_total = [], [], 0
    for rid, r in R.items():
        slots = [(i, d, ml) for ml in r['meals'] for i, d in enumerate(dates)
                 if r['avail'][ml][i] != 'x']
        if not slots:
            continue
        state = {(i, ml): r['avail'][ml][i] for i, d, ml in slots}
        live = sum(1 for i, d, ml in slots if state[(i, ml)] != 'g')

        # a weekday that is dark for every occurrence is a closure, not demand
        dropped = set()
        if live / len(slots) >= CLOSURE_GUARD:
            for ml in r['meals']:
                for day in range(7):
                    grp = [(i, d, ml) for i, d, m2 in slots if m2 == ml and wd[d] == day]
                    if grp and all(state[(i, ml)] == 'g' for i, d, _ in grp):
                        dropped |= set(grp)
            # a run of final days where every sitting was dark from the first snapshot
            # onward is the restaurant leaving the festival early, not a sell-out:
            # DiningCity's calendar cannot tell the two apart, so infer it here
            ever = r.get('everOpen') or {}
            tail = []
            for i in range(len(dates) - 1, -1, -1):
                cells = [(i, dates[i], ml) for ml in r['meals'] if (i, ml) in state]
                if cells and all(state[(i, ml)] == 'g' and ever.get(ml, '1' * len(dates))[i] == '0'
                                 for _, _, ml in cells):
                    tail += cells
                else:
                    break
            if len({d for _, d, _ in tail}) >= TAIL_MIN_DAYS:
                dropped |= set(tail)
        elig = [s for s in slots if s not in dropped]
        if not elig:
            continue
        closed_total += len(dropped)

        gone = [s for s in elig if state[(s[0], s[2])] == 'g']
        few  = [s for s in elig if state[(s[0], s[2])] == 'f']
        open_ = [s for s in elig if state[(s[0], s[2])] == 'o']

        # listed as bookable yet never published a single sitting: a dead listing
        if not few and not open_ and r['cap'] != 'no':
            unverified.append({'name': r['name'], 'loc': (r['loc'] or ['—'])[0]})
            continue
        if not gone and not few:
            continue

        denom = sum(W[(d, ml)] for i, d, ml in elig)
        if denom == 0:
            continue
        num = sum(W[(d, ml)] for i, d, ml in gone) + FEW_WEIGHT * sum(W[(d, ml)] for i, d, ml in few)

        svcs = []
        for ml in r['meals']:
            strip = [STATE[c] for c in r['avail'][ml]]
            for i, d, m2 in dropped:
                if m2 == ml:
                    strip[i] = 'na'
            mn = r['menus'].get(ml) or {}
            svcs.append({'t': mn.get('t') or EN[ml], 'p': price(mn.get('p')),
                         'en': ml, 'min': mn.get('min'),
                         # names only: the notes stay in the snapshot, off the page
                         'courses': [{'g': c.get('g', ''), 'n': c['n']}
                                     for c in (mn.get('courses') or []) if c.get('n')],
                         'gone': strip.count('gone'), 'few': strip.count('few'),
                         'poss': sum(1 for x in strip if x != 'na'), 'strip': strip})

        rows.append({'id': rid, 'name': r['name'],
                     'url': 'https://restaurantweek.diningcity.cn/lang/zh/cities/'
                            f"{snap['city']}/restaurants/{r['dir']}",
                     'loc': (r['loc'] or ['—'])[0], 'cui': '·'.join(r['cui'][:2]) or '—',
                     'cuis': r['cui'], 'minSeats': min([m.get('min') or 99 for m in r['menus'].values()] or [99]),
                     'tier': TIER.get(r['pl'], r['pl']), 'rating': r['rating'],
                     'score': round(100 * num / denom, 1), 'gone': len(gone), 'few': len(few),
                     'poss': len(elig), 'closed': len(dropped), 'flag': r['cap'], 'svcs': svcs})

    rows.sort(key=lambda r: (-r['score'], -(r['gone'] + FEW_WEIGHT * r['few']), -(r['rating'] or 0)))
    for i, r in enumerate(rows, 1):
        r['rank'] = i
    return rows, unverified, closed_total


def main():
    snaps = load_snapshots()
    latest = merged(snaps)
    rows, unverified, _ = rank(latest)
    closed = sum(r['closed'] for r in rows)

    # movement against the previous snapshot, if we have one
    history = []
    if len(snaps) > 1:
        prev_rows, _, _ = rank(merged(snaps[:-1]))
        prev = {r['id']: r for r in prev_rows}
        for r in rows:
            p = prev.get(r['id'])
            r['prevRank'] = p['rank'] if p else None
            r['deltaGone'] = r['gone'] - p['gone'] if p else None
    for s in snaps:
        rs, _, _ = rank(s)
        history.append({'at': s['capturedAt'], 'ranked': len(rs),
                        'gone': sum(x['gone'] for x in rs),
                        'slots': sum(x['poss'] for x in rs)})

    cap = datetime.datetime.fromisoformat(latest['capturedAt'])
    districts, cuisines = {}, {}
    for r in rows:
        districts[r['loc']] = districts.get(r['loc'], 0) + 1
        for c in r['cuis']:
            cuisines[c] = cuisines.get(c, 0) + 1

    data = {
        'meta': {'restaurants': len(latest['restaurants']), 'ranked': len(rows),
                 'unverified': len(unverified), 'closed': closed,
                 'snapshots': len(snaps),
                 'capturedAt': f'{cap.year}年{cap.month}月{cap.day}日 {cap:%H:%M}',
                 'capturedISO': latest['capturedAt']},
        'dates': latest['dates'],
        'weekendIdx': [i for i, d in enumerate(latest['dates'])
                       if datetime.date(*map(int, d.split('-'))).weekday() >= 5],
        'rows': rows, 'unverified': unverified, 'history': history,
        'tiers': sorted({r['tier'] for r in rows}, key=lambda t: (t != 'ELITE', t)),
        'districts': [k for k, _ in sorted(districts.items(), key=lambda kv: -kv[1])],
        'cuisines': [{'name': k, 'n': v} for k, v in
                     sorted(cuisines.items(), key=lambda kv: (-kv[1], kv[0]))],
        'partySizes': sorted({r['minSeats'] for r in rows if r['minSeats'] < 99})}

    html = open(TEMPLATE).read().replace('/*__DATA__*/', json.dumps(data, ensure_ascii=False))
    open(OUTPUT, 'w').write(html)

    print(f'{len(snaps)} snapshot(s); newest {latest["capturedAt"]}')
    print(f'ranked {len(rows)} / {len(latest["restaurants"])}  ·  '
          f'{closed} closure sittings trimmed  ·  {len(unverified)} dead listing(s) excluded')
    if len(snaps) > 1:
        d = history[-1]['gone'] - history[-2]['gone']
        print(f'change since {history[-2]["at"]}: {d:+d} sittings sold out')
    print(f'wrote {OUTPUT}  ({os.path.getsize(OUTPUT):,} bytes)')


if __name__ == '__main__':
    main()
