import sys
from pathlib import Path
from datetime import datetime, timedelta
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from usage_series import LOCAL_ZONE, today_hour_buckets, fixed_usage_series, local_now

now = datetime(2026, 10, 24, 9, 30, tzinfo=LOCAL_ZONE)
start = now.replace(hour=0, minute=0)
records = {
    'yesterday': (start.timestamp()-1, 9999),
    'midnight': (start.timestamp(), 10),
    'hour3': ((start+timedelta(hours=3, minutes=20)).timestamp(), 20),
    'hour9': (now.timestamp(), 30),
    'future-hour': ((start+timedelta(hours=10)).timestamp(), 9999),
    'tomorrow': ((start+timedelta(days=1)).timestamp(), 9999),
}
today = today_hour_buckets(records, now)
assert len(today['buckets']) == 24
assert today['buckets'][0]['tokens'] == 10 and today['buckets'][3]['tokens'] == 20 and today['buckets'][9]['tokens'] == 30
assert sum(x['tokens'] for x in today['buckets']) == 60
assert all(x['tokens'] == 0 for x in today['buckets'][10:])
daily = [{'startDate': '2026-09-24', 'tokens': 9999}, {'startDate': '2026-09-25', 'tokens': 100},
         {'startDate': '2026-10-23', 'tokens': 200}, {'startDate': '2026-10-25', 'tokens': 9999}]
month = fixed_usage_series('30天', daily, today, now)
week = fixed_usage_series('7天', daily, today, now)
assert len(month) == 30 and month[0]['startDate'] == '2026-09-25' and month[-1]['startDate'] == '2026-10-24'
assert len(week) == 7 and week[0]['startDate'] == '2026-10-18'
assert sum(x['tokens'] for x in month) == 360 and sum(x['tokens'] for x in week) == 260
assert len(fixed_usage_series('今日', None, today, now)) == 24
assert fixed_usage_series('30天', None, today, now) is None
override = fixed_usage_series('7天', daily+[{'startDate': '2026-10-24', 'tokens': 777}], today, now)
assert override[-1]['tokens'] == 777
assert fixed_usage_series('今日', daily, today, now+timedelta(days=1)) is None
leap = fixed_usage_series('7天', [], None, datetime(2024, 3, 1, 12, tzinfo=LOCAL_ZONE))
assert leap[0]['startDate'] == '2024-02-24' and leap[-2]['startDate'] == '2024-02-29'
print('PASS: fixed 30/7 daily bins and 24 local-calendar hourly bins; midnight/timezone boundaries; missing bins; future/yesterday exclusion; account-today precedence; stale-day rejection; leap dates')

