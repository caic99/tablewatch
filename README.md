# tablewatch

上海餐厅周预定情况排名 — which Restaurant Week tables are already gone.

DiningCity never publishes booking counts, so this reads demand backwards: every
restaurant, every festival date, every service is checked for what is still
bookable for two, and restaurants are ranked by how much of their own inventory
has been taken — weighted so that selling out a quiet weekday lunch counts for
more than selling out a Saturday night.

## Layout

| Path | What |
|---|---|
| `rw-data/scrape.py` | Captures one snapshot into `rw-data/snapshots/<timestamp>.json`. Never overwrites. |
| `rw-data/build.py`  | Ranks from the newest snapshot and renders `public/index.html`. |
| `rw-data/snapshots/` | Append-only history. Every scheduled run adds one file. |
| `page_template.html` | The page; `/*__DATA__*/` is replaced with the ranking JSON. |
| `public/index.html` | The built site Vercel serves. |

## Refresh

`.github/workflows/refresh.yml` runs every 6 hours during the festival, commits
the new snapshot and rebuilt page, and Vercel deploys on push. After the festival
ends the API returns no dates and the run exits cleanly without committing.

To refresh by hand:

```bash
python3 rw-data/scrape.py && python3 rw-data/build.py
```

## Page behaviour

* Past sittings fade at open time, using Shanghai's date; today's brunch/lunch
  close at 12:00, dinner stays open. `?today=YYYY-MM-DD&hour=HH` previews any moment.
* Clicking a row writes `#r-<restaurant-slug>` to the address bar; opening such a
  link scrolls to and highlights the row.
* Clicking a set-menu name shows its courses (names only; notes stay in the snapshots).
