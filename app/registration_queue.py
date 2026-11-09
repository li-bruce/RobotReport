"""One queue policy for rosters, calendars and competition participant selection."""


def ranked_entries(rows, session):
    entries = sorted((dict(row) for row in rows), key=lambda row: (row['queue_seq'], row['id']))
    active = [row for row in entries if row['status'] == 'active']
    formal = [row for row in active if not row.get('substitute_only')][:session['capacity']]
    formal_ids = {row['id'] for row in formal}
    reserve = [row for row in active if row['id'] not in formal_ids]
    result = []
    for index, row in enumerate(formal + reserve, 1):
        category = '正式' if row['id'] in formal_ids else ('替补' if index-len(formal) <= session['substitute_capacity'] else '候补')
        result.append(row | {'rank': index, 'category': category})
    return result + [row | {'rank': None, 'category': '已取消'} for row in entries if row['status'] != 'active']


def queue_counts(rows, session):
    entries = ranked_entries(rows, session)
    return {key: sum(row['category'] == label for row in entries)
            for key, label in [('formal_count','正式'), ('substitute_count','替补'), ('waitlist_count','候补')]}
