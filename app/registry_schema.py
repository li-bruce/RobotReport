"""Registry guard definitions shared with offline schema validation."""

GROUP_BINDING_GUARDS = {
    'single_bot_group_insert': '''CREATE TRIGGER single_bot_group_insert
        BEFORE INSERT ON bot_channels WHEN NEW.archived=0 AND EXISTS(
            SELECT 1 FROM bot_channels WHERE bot_id=NEW.bot_id AND archived=0)
        BEGIN SELECT RAISE(ABORT,'机器人只能绑定一个群，请编辑现有绑定或先移除'); END''',
    'single_bot_group_restore': '''CREATE TRIGGER single_bot_group_restore
        BEFORE UPDATE OF bot_id,archived ON bot_channels
        WHEN NEW.archived=0 AND (OLD.archived<>0 OR OLD.bot_id<>NEW.bot_id)
        AND EXISTS(SELECT 1 FROM bot_channels
            WHERE bot_id=NEW.bot_id AND archived=0 AND id<>OLD.id)
        BEGIN SELECT RAISE(ABORT,'机器人只能绑定一个群，请编辑现有绑定或先移除'); END''',
}


def normalized_trigger(sql):
    return ' '.join((sql or '').split()).replace(
        'CREATE TRIGGER IF NOT EXISTS ', 'CREATE TRIGGER ', 1).rstrip(';')
