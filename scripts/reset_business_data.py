"""Offline reset of one complete v2 state directory. Never import from an API.

Stop this robot service, make/verify a full state backup, then run, for example:
  python -m scripts.reset_business_data --state /absolute/active-state \
    --archive /absolute/private-backups/legacy-reset-UNIQUE \
    --confirm RESET_BUSINESS_DATA --service-stopped --backup-confirmed \
    --service-unit kingsoft-registration.service

--dry-run performs read-only preflight and requires no confirmation flags.
The archive destination must be new, outside state, with an existing parent.
Database locks supplement, but cannot replace, stopping every service process.
Changes across databases/files are NOT atomic. On any execution failure keep
the service stopped and restore the entire verified backup, not selected files.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess
import sys
from app.registry_schema import GROUP_BINDING_GUARDS, normalized_trigger


CONFIRMATION = 'RESET_BUSINESS_DATA'
BUSINESS_PRESERVE = frozenset({
    'schema_version', 'config', 'venues', 'slots', 'special_dates', 'member_groups', 'holiday_schedules',
    'holiday_calendars', 'holiday_plans',
})
BUSINESS_DELETE = frozenset({
    'venue_reservations', 'availability_notices',
    'profiles', 'member_records', 'membership_applications', 'member_profile_requests', 'identity_observations',
    'membership_terms', 'membership_term_audit',
    'identity_notification_events', 'identity_notification_deliveries',
    'user_roles', 'member_group_links', 'sessions', 'registrations', 'imports',
    'member_imports', 'pre_registered_members', 'member_import_previews',
    'audit', 'legacy_rows', 'legacy_delivery', 'workspace_migration_issues',
    'tour_events', 'tour_participants', 'tour_matches', 'tour_audit',
    'tour_notifications', 'tour_organization', 'tour_event_channels',
    'notification_jobs', 'notification_deliveries', 'media', 'media_links',
    'generated_tables', 'inbox', 'outbox', 'ai_pending', 'ai_requests', 'ai_plan_steps', 'ai_plans',
    'notification_marks', 'registration_forms', 'daily_planning',
})
TRANSPORT_PRESERVE = frozenset({'transport_meta'})
TRANSPORT_DELETE = frozenset({
    'inbox', 'outbox', 'ai_pending', 'ai_requests', 'ai_plan_steps', 'ai_plans', 'notification_marks',
    'registration_forms', 'daily_planning',
})
REGISTRY_PRESERVE = frozenset({
    'bots', 'bot_channels', 'chat_groups', 'bot_connection_checks', 'bot_connection_attempts', 'grants', 'workspace_migrations',
    'password_credentials', 'password_sessions', 'password_failures',
})
INTERNAL_TABLES = frozenset({'sqlite_sequence', 'sqlite_stat1', 'sqlite_stat4'})
PERSON_CONFIG_KEYS = ('admin_user_ids', 'leader_user_ids')


class ResetError(RuntimeError):
    """Safe operational error; never include database row values or secrets."""


def _quoted(name):
    return '"' + name.replace('"', '""') + '"'


def _is_link(path):
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0)
    )


def _absolute(value, label, *, existing=True):
    path = Path(value)
    if not path.is_absolute() or '..' in path.parts:
        raise ResetError(f'{label} must be a full absolute path without parent traversal')
    for item in [path, *path.parents]:
        if item.exists() or item.is_symlink():
            if _is_link(item):
                raise ResetError(f'{label} must not contain symlinks or reparse points')
    if existing and not path.exists():
        raise ResetError(f'{label} does not exist')
    # Every existing component has already been checked with lstat. Non-strict
    # resolution also supports a new archive destination and Windows sandboxes
    # that permit lstat but do not permit opening directory handles for resolve.
    path = path.resolve()
    if len(path.parts) < 3:
        raise ResetError(f'{label} is too broad')
    return path


def _inside(path, root):
    try:
        path.relative_to(root)
    except ValueError:
        raise ResetError('Path escaped the explicitly selected directory') from None
    _absolute(path, 'State file')
    return path


def _walk(root):
    """Do not follow a symlink/junction, including one encountered below root."""
    files = []
    def unreadable(_error):
        raise ResetError('Cannot enumerate every state directory; reset was refused')
    for directory, dirs, names in os.walk(root, followlinks=False, onerror=unreadable):
        for name in [*dirs, *names]:
            child = Path(directory) / name
            _inside(child, root)
            if child.is_file():
                files.append(child)
            elif not child.is_dir():
                raise ResetError('Unexpected non-regular state file')
    return files


def _open(path, *, readonly=False):
    if path.stat().st_nlink != 1:
        raise ResetError('Database hard links are not supported')
    mode = 'ro' if readonly else 'rw'
    conn = sqlite3.connect(path.as_uri() + '?mode=' + mode, uri=True, timeout=0, isolation_level=None)
    conn.execute('PRAGMA busy_timeout=0')
    return conn


def _schema(conn, kind):
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    preserve, delete = {
        'registry': (REGISTRY_PRESERVE, frozenset()),
        'business': (BUSINESS_PRESERVE, BUSINESS_DELETE),
        'legacy': (BUSINESS_PRESERVE, BUSINESS_DELETE),
        'transport': (TRANSPORT_PRESERVE, TRANSPORT_DELETE),
    }[kind]
    unknown = tables - preserve - delete - INTERNAL_TABLES
    if unknown:
        raise ResetError(f'Unclassified {kind} tables: ' + ', '.join(sorted(unknown)))
    for type_, name, sql in conn.execute("SELECT type,name,sql FROM sqlite_master WHERE type IN ('view','trigger')"):
        expected = GROUP_BINDING_GUARDS.get(name) if kind == 'registry' else None
        if type_ != 'trigger' or expected is None or normalized_trigger(sql) != normalized_trigger(expected):
            raise ResetError(f'Unexpected {kind} views or triggers; review before resetting')
    required = {'registry': {'bots', 'bot_channels', 'grants', 'workspace_migrations'},
                'business': {'config', 'schema_version', 'venues', 'slots', 'special_dates', 'member_groups'},
                'transport': {'transport_meta'}, 'legacy': set()}[kind]
    if not required <= tables:
        raise ResetError(f'{kind} database is missing required v2 schema')
    if conn.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
        raise ResetError(f'{kind} database integrity check failed')
    if kind == 'registry' and not conn.execute('SELECT 1 FROM workspace_migrations WHERE version=2').fetchone():
        raise ResetError('Complete the v2 migration before running a business reset')
    return tables, preserve & tables, delete & tables


def _counts(conn, tables):
    return {name: conn.execute('SELECT COUNT(*) FROM ' + _quoted(name)).fetchone()[0]
            for name in sorted(tables)}


def _fingerprint(conn, tables, *, omit_person_config=False):
    digest = hashlib.sha256()
    for table in sorted(tables):
        digest.update(table.encode('utf-8') + b'\0')
        rows = conn.execute('SELECT * FROM ' + _quoted(table)).fetchall()
        if table == 'config' and omit_person_config:
            rows = [row for row in rows if row[0] not in PERSON_CONFIG_KEYS]
        # repr preserves value types without emitting row data in the report.
        for row in sorted(repr(tuple(row)) for row in rows):
            digest.update(row.encode('utf-8') + b'\0')
    return digest.hexdigest()


def _service_check(unit):
    if unit is None:
        return
    # Deliberately only the project's service; never stop/start services here.
    if unit != 'kingsoft-registration.service':
        raise ResetError('Unsupported service unit')
    try:
        result = subprocess.run(['systemctl', 'show', unit, '--property=LoadState,ActiveState', '--no-pager'],
                                check=True, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        raise ResetError('Cannot verify the stopped systemd service') from None
    fields = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    if fields.get('LoadState') != 'loaded' or fields.get('ActiveState') not in ('inactive', 'failed'):
        raise ResetError('The robot service must be loaded and stopped before reset')


def _layout(state, archive):
    state = _absolute(state, 'State')
    archive = _absolute(archive, 'Archive', existing=False)
    if not state.is_dir() or not archive.parent.is_dir() or archive.exists():
        raise ResetError('State must exist; archive must be new with an existing parent')
    if archive == state or state in archive.parents or archive in state.parents:
        raise ResetError('Archive and active state must be separate, non-overlapping directories')
    all_files = _walk(state)
    registry = state / 'registry.sqlite3'
    business = state / 'workspace' / 'business.sqlite3'
    if not registry.is_file() or not business.is_file():
        raise ResetError('Select the complete v2 state directory, not a database or bot subdirectory')
    stores = [(registry, 'registry'), (business, 'business')]
    bot_root = state / 'bots'
    attachment_roots = [state / 'workspace' / name for name in ('uploads', 'tables')]
    if bot_root.exists():
        for bot in sorted(bot_root.iterdir()):
            if not bot.is_dir():
                raise ResetError('Unexpected file in bots directory')
            for name, kind in [('transport.sqlite3', 'transport'), ('business.sqlite3', 'legacy')]:
                if (bot / name).is_file():
                    stores.append((bot / name, kind))
            attachment_roots.extend(bot / name for name in ('uploads', 'tables'))
    known = {p for p, _ in stores}
    for file in all_files:
        if file.suffix.lower() in ('.sqlite3', '.sqlite', '.db') and file not in known:
            raise ResetError('Unclassified database location inside state; archive/review it first')
        for suffix in ('-wal', '-shm', '-journal'):
            if file.name.endswith(suffix) and Path(str(file)[:-len(suffix)]) not in known:
                raise ResetError('Orphan database sidecar inside state; review it first')
    attachments = []
    for folder in attachment_roots:
        if folder.exists():
            if not folder.is_dir():
                raise ResetError('Business attachment root is not a directory')
            attachments.extend(_walk(folder))
    return state, archive, stores, attachment_roots, attachments


def _preflight(state, stores):
    inventory = []
    for path, kind in stores:
        with ExitStack() as stack:
            conn = _open(path, readonly=True)
            stack.callback(conn.close)
            tables, preserve, delete = _schema(conn, kind)
            inventory.append({
                'database': path.relative_to(state).as_posix(), 'kind': kind,
                'before': _counts(conn, tables),
                'preserve': sorted(preserve), 'delete': sorted(delete),
                'internal_policy': {name: ('preserve' if kind == 'registry' else 'archive'
                    if kind == 'legacy' else 'remove_cleared_table_sequences'
                    if name == 'sqlite_sequence' else 'clear_statistics')
                    for name in sorted(tables & INTERNAL_TABLES)},
                'preserved_fingerprint_before': _fingerprint(conn, preserve, omit_person_config=True),
                'config_fingerprint_before': _fingerprint(conn, {'config'}, omit_person_config=True)
                if 'config' in tables else None,
            })
    return inventory


def _delete_rows(conn, tables, delete):
    conn.execute('PRAGMA defer_foreign_keys=ON')
    for table in sorted(delete):
        conn.execute('DELETE FROM ' + _quoted(table))
    if 'sqlite_sequence' in tables:
        for name in delete:
            conn.execute('DELETE FROM sqlite_sequence WHERE name=?', (name,))
    for name in ('sqlite_stat1', 'sqlite_stat4'):
        if name in tables:
            conn.execute('DELETE FROM ' + name)
    if 'config' in tables:
        conn.executemany("UPDATE config SET value='[]' WHERE key=?", [(key,) for key in PERSON_CONFIG_KEYS])
    if conn.execute('PRAGMA foreign_key_check').fetchone():
        raise ResetError('Foreign-key validation failed after reset')


def reset_business_data(state, archive, *, confirm='', service_stopped=False,
                        backup_confirmed=False, service_unit=None, dry_run=False):
    """Only invoke locally after every process using this state has been stopped."""
    if not dry_run and (confirm != CONFIRMATION or not service_stopped or not backup_confirmed):
        raise ResetError('Explicit confirmation, stopped-service declaration and verified-backup declaration are required')
    state, archive, stores, attachment_roots, attachments = _layout(state, archive)
    _service_check(service_unit)
    inventory = _preflight(state, stores)  # All databases checked before any writes.
    report = {'version': 1, 'status': 'preflight' if dry_run else 'complete',
              'created_at': datetime.now(timezone.utc).isoformat(),
              'databases': inventory, 'attachments_before': len(attachments),
              'person_config_keys_cleared': list(PERSON_CONFIG_KEYS),
              'service_verified': service_unit or 'operator-declaration',
              'recovery': 'On execution failure keep service stopped and restore the complete pre-reset backup.'}
    if dry_run:
        return report
    phase = 'acquiring offline database locks'
    try:
        with ExitStack() as stack:
            connections = []
            for (path, kind), item in zip(stores, inventory):
                conn = _open(_inside(path, state))
                stack.callback(conn.close)
                conn.execute('PRAGMA locking_mode=EXCLUSIVE')
                conn.execute('PRAGMA foreign_keys=ON')
                conn.execute('BEGIN EXCLUSIVE')
                tables, preserve, delete = _schema(conn, kind)
                if _counts(conn, tables) != item['before']:
                    raise ResetError('State changed during preflight; stop every service process and retry')
                if _fingerprint(conn, preserve, omit_person_config=True) != item['preserved_fingerprint_before']:
                    raise ResetError('Configuration changed during preflight')
                connections.append((conn, path, kind, tables, preserve, delete, item))
            _service_check(service_unit)
            phase = 'archiving legacy databases'
            archive.mkdir(mode=0o700)  # New only; never overwrite/delete an external backup.
            for conn, path, kind, tables, preserve, delete, item in connections:
                if kind != 'legacy':
                    continue
                destination = archive / path.relative_to(state)
                destination.parent.mkdir(parents=True, mode=0o700)
                # No modifications have been made to old databases; the exclusive
                # transaction freezes database + WAL bytes for this offline copy.
                for suffix in ('', '-wal', '-shm', '-journal'):
                    source = Path(str(path) + suffix)
                    if source.is_file():
                        shutil.copyfile(_inside(source, state), Path(str(destination) + suffix))
                        Path(str(destination) + suffix).chmod(0o600)
                archived = _open(destination, readonly=True)
                try:
                    archived_tables, _, _ = _schema(archived, 'legacy')
                    if archived_tables != tables or _fingerprint(archived, tables) != _fingerprint(conn, tables):
                        raise ResetError('Legacy archive verification failed')
                finally:
                    archived.close()
                item['action'] = 'archived_outside_active_state'
            phase = 'clearing database rows'
            for conn, path, kind, tables, preserve, delete, item in connections:
                if kind in ('business', 'transport'):
                    conn.execute('PRAGMA secure_delete=ON')
                    _delete_rows(conn, tables, delete)
                    item['action'] = 'reset'
                elif kind == 'registry':
                    item['action'] = 'preserved'
                item['after'] = _counts(conn, tables) if kind != 'legacy' else {name: 0 for name in tables}
                item['preserved_fingerprint_after'] = _fingerprint(conn, preserve, omit_person_config=True)
                item['config_fingerprint_after'] = _fingerprint(conn, {'config'}, omit_person_config=True) if 'config' in tables else None
                if item['preserved_fingerprint_after'] != item['preserved_fingerprint_before']:
                    raise ResetError('Preserved configuration fingerprint changed')
                if kind in ('business', 'transport') and any(item['after'][name] for name in delete):
                    raise ResetError('A business table is not empty after reset')
            # The coordinated SQL writes are validated before any commit, but
            # SQLite cannot make the following multi-database commits atomic.
            phase = 'committing database reset'
            for conn, path, kind, tables, preserve, delete, item in connections:
                conn.commit()
                if kind in ('business', 'transport'):
                    if conn.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0] != 0:
                        raise ResetError('WAL cleanup failed; keep the service stopped')
                    conn.execute('VACUUM')
                    if conn.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0] != 0:
                        raise ResetError('WAL cleanup failed; keep the service stopped')
                    if conn.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                        raise ResetError('Post-reset database integrity check failed')
            # Close handles before removing legacy database files on Windows.
        phase = 'removing archived legacy stores and business attachments'
        for path, kind in stores:
            if kind == 'legacy':
                for suffix in ('', '-wal', '-shm', '-journal'):
                    source = Path(str(path) + suffix)
                    if source.exists():
                        _inside(source, state).unlink()
        for folder in attachment_roots:
            if folder.exists():
                _inside(folder, state)
                for file in _walk(folder):
                    _inside(file, folder).unlink()
                for directory, dirs, names in os.walk(folder, topdown=False):
                    for name in dirs:
                        _inside(Path(directory) / name, folder).rmdir()
        report['attachments_after'] = sum(len(_walk(folder)) for folder in attachment_roots if folder.exists())
        report['legacy_databases_removed'] = sum(kind == 'legacy' for _, kind in stores)
        (archive / 'reset-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        (archive / 'reset-report.json').chmod(0o600)
        return report
    except Exception as exc:
        # Do not print arbitrary SQLite values, filesystem filenames, or subprocess output.
        raise ResetError(f'Reset failed while {phase}. Keep service stopped and restore the complete verified backup. Error type: {type(exc).__name__}') from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--state', required=True)
    parser.add_argument('--archive', required=True)
    parser.add_argument('--confirm', default='')
    parser.add_argument('--service-stopped', action='store_true')
    parser.add_argument('--backup-confirmed', action='store_true')
    parser.add_argument('--service-unit')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    try:
        report = reset_business_data(args.state, args.archive, confirm=args.confirm,
            service_stopped=args.service_stopped, backup_confirmed=args.backup_confirmed,
            service_unit=args.service_unit, dry_run=args.dry_run)
    except (ResetError, OSError, sqlite3.Error) as exc:
        message = str(exc) if isinstance(exc, ResetError) else 'Preflight failed; no reset was performed. Check state paths and database access.'
        print(json.dumps({'status': 'failed', 'error': message}), file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
