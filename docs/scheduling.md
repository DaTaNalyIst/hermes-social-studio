# How posting times are chosen

`social add` looks at every free half-hour from **45 minutes from now** to **7 days ahead**, in your
audience's timezone (`general.timezone`), and gives each a score:

```
score = prior(platform, weekday, hour) x learned weight(platform, weekday, hour) x urgency
urgency = 1 / (1 + 0.12 x days from the earliest slot)
```

It picks the highest score that respects every rule:

- **Quiet hours:** no posts between `quiet_hours` (default 00:00-06:00).
- **Daily caps:** per platform and lane (`daily_cap`; Stories use `story_cap`, default 5).
- **Minimum gaps:** between posts on the same platform and lane (`min_gap_minutes`; Stories 60 min).
- **Pinned times:** a target with `"at": "2026-10-09 19:00"` keeps that time.
- **ASAP:** `"when": "asap"` takes the first allowed slot.

The publisher runs every 30 minutes, so a post goes out up to ~30 minutes after its slot (more if
GitHub delays scheduled runs).

## Priors

Weights are 1.0 inside a best window, 0.6 in the hour on either side of one, and 0.35 at other
daytime hours (0.12 at night). Fridays get ×0.8 and weekends ×0.55 on platforms whose research shows
weekends are weakest.

| Platform | Source | Best windows (local time) |
|---|---|---|
| Facebook | Sprout Social 2026 | Mon 12-13; Tue & Wed 12-20; Thu 12-14 and 20 |
| Instagram | Sprout Social 2026 | Mon 14-16; Tue 13-19; Wed 12-21 and 23; Thu 12-14 |
| Threads, Bluesky | Sprout Social 2026 windows for X | Mon 14-15 and 17; Tue-Thu 12-18 |
| YouTube | heuristic | Mon-Thu 14-17; Fri 12-15; weekends 9-11 |
| Telegram | heuristic | daily 8-9, 12-13, 18-21 |
| Stories (FB, IG) | heuristic | daily 8-10, 12-13, 18-22 |

Sprout Social's 2026 data covers November 2025 to February 2026, measured in each account's local
time. Your own data quickly matters more than these averages.

## Learning

Once a day the `insights` workflow reads engagement for posts that are 72 hours old (7 days for
YouTube):

| Platform | Engagement score |
|---|---|
| Facebook | reactions + 2 x comments + 3 x shares |
| Instagram | likes + 2 x comments + 3 x saves + 3 x shares |
| Threads | likes + 2 x replies + 3 x reposts + 3 x quotes |
| Bluesky | likes + 2 x replies + 3 x reposts + 3 x quotes |
| YouTube | likes + 2 x comments + views / 100 |

After a platform has 3 evaluated posts, each new score is compared with the median of its last 30. The
weekday and hour it went out at are nudged by `sqrt(ratio)`, clipped to between 0.5x and 2x, at
`learning.rate` (0.3). The hours on either side get half that nudge. Weights stay between 0.5 and 2.0,
so research and your data both keep a say.

The weights live in `state/weights.json` and the history in `state/history.json`. Both are plain JSON,
so you can read, edit or reset them. Every slot in `social queue` and in the approval issue shows its
prior and learned weight.
