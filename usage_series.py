"""Calendar-based, fixed-length usage bins in the user's UTC+8 time zone."""
from datetime import datetime, timedelta, timezone

LOCAL_ZONE = timezone(timedelta(hours=8))


def local_now(now=None):
    if now is None:
        return datetime.now(LOCAL_ZONE)
    if isinstance(now, datetime):
        return now.replace(tzinfo=LOCAL_ZONE) if now.tzinfo is None else now.astimezone(LOCAL_ZONE)
    return datetime.fromtimestamp(now, LOCAL_ZONE)


def today_hour_buckets(records, now=None):
    moment = local_now(now)
    midnight = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    bins = [{'startDate': f'{hour:02}:00', 'tokens': 0, 'recorded': False} for hour in range(24)]
    for stamp, tokens in records.values():
        if midnight.timestamp() <= stamp <= moment.timestamp():
            hour = int((stamp-midnight.timestamp())//3600)
            if 0 <= hour < 24 and isinstance(tokens, int) and not isinstance(tokens, bool) and tokens >= 0:
                bins[hour]['tokens'] += tokens
                bins[hour]['recorded'] = True
    return {'date': moment.date().isoformat(), 'buckets': bins}


def fixed_usage_series(period, daily, today_usage, now=None):
    today = local_now(now).date()
    today_valid = today_usage is not None and today_usage.get('date') == today.isoformat()
    if period == '今日':
        return [dict(item) for item in today_usage['buckets']] if today_valid else None
    if daily is None:
        return None
    count = {'7天': 7, '30天': 30}[period]
    recorded = {}
    for item in daily:
        day, tokens = item.get('startDate'), item.get('tokens')
        if isinstance(day, str) and isinstance(tokens, int) and not isinstance(tokens, bool) and tokens >= 0:
            recorded[day] = recorded.get(day, 0)+tokens
    # The service may publish completed days only. Complete today's last daily
    # column with the local hourly sum until an account bucket is available.
    if today.isoformat() not in recorded and today_valid:
        recorded[today.isoformat()] = sum(item['tokens'] for item in today_usage['buckets'])
    dates = [(today-timedelta(days=i)).isoformat() for i in range(count-1, -1, -1)]
    return [{'startDate': day, 'tokens': recorded.get(day, 0), 'recorded': day in recorded} for day in dates]
