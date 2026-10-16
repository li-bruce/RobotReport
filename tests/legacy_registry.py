"""Build pre-v2.14 multi-group fixtures for historical routing regression tests.

New production bindings must use Platform.save_channel and are single-group.
These raw rows represent data already present before the new invariant existed.
"""
import uuid

from app.db import connect, dumps, stamp


def seed_legacy_channel(platform, bot_id, data):
    record = platform._channel_data(data)
    channel_id = uuid.uuid4().hex
    keys = ('name', 'chat_id', 'webhook_url', 'robot_key', 'active',
            'default_venue_id', 'venue_ids', 'receive_commands',
            'daily_notifications', 'slot_keys')
    values = [dumps(record[k]) if k in ('venue_ids', 'slot_keys') else record[k] for k in keys]
    with connect(platform.path, True) as db:
        db.execute('DROP TRIGGER IF EXISTS single_bot_group_insert')
        db.execute('INSERT INTO bot_channels(id,bot_id,' + ','.join(keys) + ',created_at) VALUES('
                   + ','.join('?' for _ in range(len(keys) + 3)) + ')',
                   (channel_id, bot_id, *values, stamp()))
        platform._guard_single_group(db)
    return platform.visible_channel(platform.channel(channel_id))
