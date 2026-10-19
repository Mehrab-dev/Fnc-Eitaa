"""Local SQLite inbox/outbox. No network, credentials, pickle or blind retries."""
from contextlib import contextmanager
from dataclasses import dataclass, field, fields
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import time
import uuid

from .account_files import AccountMediaInfo
from .account_messages import AccountMessage
from .account_peers import AccountPeer
from .account_updates import AccountEvent
from .enums import MediaKind, PeerKind
from .errors import QueueError, QueueConflictError

OUTBOX_STATES = ('pending', 'in_flight', 'confirmed', 'uncertain', 'failed', 'cancelled')
OPERATIONS = ('send_message', 'edit_message', 'delete_messages', 'forward_messages', 'copy_message', 'send_file', 'send_album')
MODELS = {model.__name__: model for model in (AccountPeer, AccountMediaInfo, AccountMessage, AccountEvent)}
MAX_PAYLOAD = 16 * 1024 * 1024


def _pack(value):
    if type(value) in MODELS.values():
        return {'model': type(value).__name__, 'fields': {f.name: _pack(getattr(value, f.name)) for f in fields(value)}}
    if isinstance(value, bytes):
        return {'binary': base64.b64encode(value).decode('ascii')}
    if isinstance(value, dict):
        return {'map': [[key, _pack(value[key])] for key in sorted(value)]}
    if isinstance(value, (tuple, list)):
        return [_pack(item) for item in value]
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise QueueError('Unsupported queue payload type')


def _unpack(value):
    if isinstance(value, list):
        return [_unpack(v) for v in value]
    if not isinstance(value, dict):
        return value
    if set(value) == {'binary'}:
        return base64.b64decode(value['binary'], validate=True)
    if set(value) == {'map'}:
        return {key: _unpack(item) for key, item in value['map']}
    if set(value) == {'model', 'fields'} and value['model'] in MODELS:
        data = {key: _unpack(item) for key, item in value['fields'].items()}
        if value['model'] == 'AccountPeer':
            data['kind'] = PeerKind(data['kind'])
        elif value['model'] == 'AccountMediaInfo':
            data['kind'] = MediaKind(data['kind'])
        elif value['model'] == 'AccountEvent':
            data['message_ids'] = tuple(data['message_ids'])
        return MODELS[value['model']](**data)
    raise QueueError('Invalid queue payload structure')


def encode_payload(value):
    try:
        result = json.dumps(_pack(value), separators=(',', ':'), ensure_ascii=True)
        if len(result) > MAX_PAYLOAD:
            raise QueueError('Queue payload exceeds size limit')
        return result
    except (TypeError, ValueError, RecursionError):
        raise QueueError('Cannot serialize queue payload') from None


def decode_payload(value):
    try:
        if not isinstance(value, str) or len(value) > MAX_PAYLOAD:
            raise ValueError
        return _unpack(json.loads(value))
    except (KeyError, TypeError, ValueError, RecursionError):
        raise QueueError('Malformed queue payload') from None


@dataclass(frozen=True)
class OutboxJob:
    id: str
    operation: str
    state: str
    request: dict = field(repr=False)
    attempts: int = 0
    receipt: dict | None = field(default=None, repr=False)
    error: str | None = None
    not_before: float = 0
    claim_token: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class InboxItem:
    event: AccountEvent = field(repr=False)
    state: str
    attempts: int
    claim_token: str | None = field(default=None, repr=False)


class SQLiteQueueStore:
    """One connection per owning thread; multiple processes use SQL CAS claims.

    Database contents include message bodies and access hashes, but no account
    token. Keep the database private. SQLite FULL sync assumes a local filesystem.
    """
    APPLICATION_ID = 0x464E4351

    def __init__(self, path, owner):
        self.path = Path(path).absolute()
        self.owner = encode_payload(owner)
        self._closed = False
        if not self.path.parent.is_dir():
            raise QueueError('Queue database parent must exist')
        for suffix in ('', '-wal', '-shm', '-journal'):
            candidate = Path(str(self.path) + suffix)
            if candidate.is_symlink() or (candidate.exists() and not candidate.is_file()):
                raise QueueError('Queue database and sidecars must be regular files')
        try:
            try:
                fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                pass
            else:
                os.close(fd)
            self._db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
            self._db.row_factory = sqlite3.Row
            with self._transaction():
                application = self._db.execute('PRAGMA application_id').fetchone()[0]
                version = self._db.execute('PRAGMA user_version').fetchone()[0]
                if application == 0:
                    if version or self._db.execute("SELECT 1 FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchone():
                        raise QueueError('Refusing to initialize a different database')
                    self._db.execute(f'PRAGMA application_id={self.APPLICATION_ID}')
                    self._db.execute('PRAGMA user_version=1')
                    for statement in (
                        'CREATE TABLE metadata (id INTEGER PRIMARY KEY CHECK(id=1), owner TEXT NOT NULL)',
                        'CREATE TABLE checkpoint (id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL, payload TEXT NOT NULL)',
                        "CREATE TABLE outbox (id TEXT PRIMARY KEY, operation TEXT NOT NULL, payload TEXT NOT NULL, dedupe_key TEXT UNIQUE, fingerprint TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('pending','in_flight','confirmed','uncertain','failed','cancelled')), attempts INTEGER NOT NULL DEFAULT 0, receipt TEXT, error TEXT, not_before REAL NOT NULL DEFAULT 0, claim_token TEXT, lease_until REAL, created REAL NOT NULL)",
                        "CREATE TABLE inbox (sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL, payload TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('pending','processing','done')), attempts INTEGER NOT NULL DEFAULT 0, claim_token TEXT, lease_until REAL, created REAL NOT NULL)",
                        'CREATE TABLE audit (sequence INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL, state TEXT NOT NULL, reason TEXT, created REAL NOT NULL)',
                        'CREATE INDEX outbox_pending ON outbox(state, not_before, created)',
                        'CREATE INDEX inbox_pending ON inbox(state, sequence)',
                    ):
                        self._db.execute(statement)
                    self._db.execute('INSERT INTO metadata VALUES (1,?)', (self.owner,))
                elif application != self.APPLICATION_ID or version != 1:
                    raise QueueError('Unsupported queue database version or application')
                record = self._db.execute('SELECT owner FROM metadata WHERE id=1').fetchone()
                if record is None or record['owner'] != self.owner:
                    raise QueueError('Queue belongs to a different account/device/application')
            self._db.execute('PRAGMA journal_mode=WAL')
            self._db.execute('PRAGMA synchronous=FULL')
        except BaseException as exc:
            if hasattr(self, '_db'):
                self._db.close()
            if isinstance(exc, (sqlite3.Error, OSError)):
                raise QueueError('Cannot open queue database') from None
            raise

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        if not self._closed:
            self._db.close()
            self._closed = True

    @contextmanager
    def _transaction(self):
        try:
            self._db.execute('BEGIN IMMEDIATE')
            try:
                yield
                self._db.execute('COMMIT')
            except BaseException:
                self._db.execute('ROLLBACK')
                raise
        except sqlite3.Error:
            raise QueueError('Queue transaction failed; no automatic retry') from None

    def _audit(self, identifier, state, reason=None):
        self._db.execute('INSERT INTO audit(job_id,state,reason,created) VALUES(?,?,?,?)',
                         (identifier, state, reason, time.time()))

    @staticmethod
    def _job(row):
        return OutboxJob(row['id'], row['operation'], row['state'], decode_payload(row['payload']),
                         row['attempts'], decode_payload(row['receipt']) if row['receipt'] else None,
                         row['error'], row['not_before'], row['claim_token'])

    def get_job(self, identifier):
        row = self._db.execute('SELECT * FROM outbox WHERE id=?', (identifier,)).fetchone()
        if row is None:
            raise QueueError('Unknown outbox job')
        return self._job(row)

    def list_jobs(self, *, limit=20):
        self._limit(limit)
        return tuple(self._job(row) for row in self._db.execute('SELECT * FROM outbox ORDER BY rowid LIMIT ?', (limit,)))

    @staticmethod
    def _limit(limit):
        if type(limit) is not int or not 1 <= limit <= 10000:
            raise ValueError('Queue limit must be 1..10000')

    def enqueue(self, operation, request, *, key=None):
        if operation not in OPERATIONS or not isinstance(request, dict):
            raise ValueError('Unsupported outbox operation')
        if key is not None and (not isinstance(key, str) or not 1 <= len(key) <= 256):
            raise ValueError('Idempotency key must contain 1..256 characters')
        payload = encode_payload(request)
        # Correlation IDs are generated once, but an identical keyed enqueue may
        # have just generated unused new IDs. Compare the semantic request only.
        semantic = {k: v for k, v in request.items() if k not in ('random_id', 'random_ids')}
        fingerprint = hashlib.sha256((operation + encode_payload(semantic)).encode()).hexdigest()
        with self._transaction():
            if key is not None:
                old = self._db.execute('SELECT * FROM outbox WHERE dedupe_key=?', (key,)).fetchone()
                if old is not None:
                    if old['operation'] != operation or old['fingerprint'] != fingerprint:
                        raise QueueConflictError('Idempotency key already belongs to a different request')
                    return self._job(old)
            identifier = uuid.uuid4().hex
            self._db.execute('INSERT INTO outbox(id,operation,payload,dedupe_key,fingerprint,state,created) VALUES(?,?,?,?,?,?,?)',
                             (identifier, operation, payload, key, fingerprint, 'pending', time.time()))
            self._audit(identifier, 'pending', 'enqueued')
        return self.get_job(identifier)

    def claim_job(self, identifier=None, *, lease_seconds=300):
        self._lease(lease_seconds)
        with self._transaction():
            sql, params = "SELECT * FROM outbox WHERE state='pending' AND not_before<=?", [time.time()]
            if identifier is not None:
                sql += ' AND id=?'
                params.append(identifier)
            row = self._db.execute(sql + ' ORDER BY rowid LIMIT 1', params).fetchone()
            if row is None:
                return None
            token = uuid.uuid4().hex
            self._db.execute("UPDATE outbox SET state='in_flight',attempts=attempts+1,claim_token=?,lease_until=?,error=NULL WHERE id=?",
                             (token, time.time()+lease_seconds, row['id']))
            self._audit(row['id'], 'in_flight', 'claimed')
        return self.get_job(row['id'])

    @staticmethod
    def _lease(value):
        if type(value) is not int or not 1 <= value <= 86400:
            raise ValueError('Lease must be an integer in 1..86400 seconds')

    def finish_job(self, job, state, *, receipt=None, error=None, retry_after=0):
        if state not in ('confirmed', 'uncertain', 'failed'):
            raise ValueError('Invalid final outbox state')
        if type(retry_after) is not int or not 0 <= retry_after < 10**129:
            raise ValueError('Invalid outbox retry delay')
        payload = encode_payload(receipt) if receipt is not None else None
        with self._transaction():
            changed = self._db.execute("UPDATE outbox SET state=?,receipt=?,error=?,not_before=?,claim_token=NULL,lease_until=NULL WHERE id=? AND state='in_flight' AND claim_token=?",
                (state, payload, error, time.time()+retry_after, job.id, job.claim_token)).rowcount
            if changed != 1:
                raise QueueConflictError('Outbox claim was recovered or belongs to another worker')
            self._audit(job.id, state, error)
        return self.get_job(job.id)

    def retry_failed(self, identifier):
        with self._transaction():
            changed = self._db.execute("UPDATE outbox SET state='pending',error=NULL WHERE id=? AND state='failed' AND not_before<=?", (identifier, time.time())).rowcount
            if changed != 1:
                raise QueueConflictError('Only a failed job whose wait has elapsed can be retried')
            self._audit(identifier, 'pending', 'explicit_retry')
        return self.get_job(identifier)

    def cancel_job(self, identifier):
        with self._transaction():
            changed = self._db.execute("UPDATE outbox SET state='cancelled' WHERE id=? AND state='pending'", (identifier,)).rowcount
            if changed != 1:
                raise QueueConflictError('Only a pending job can be cancelled')
            self._audit(identifier, 'cancelled', 'explicit_cancel')

    def resolve_uncertain(self, identifier, *, state, note, receipt=None):
        if state not in ('confirmed', 'failed') or not isinstance(note, str) or not 1 <= len(note) <= 256:
            raise ValueError('Explicit verification note and confirmed/failed state required')
        payload = encode_payload(receipt) if receipt is not None else None
        with self._transaction():
            changed = self._db.execute("UPDATE outbox SET state=?,receipt=?,error=NULL WHERE id=? AND state='uncertain'", (state, payload, identifier)).rowcount
            if changed != 1:
                raise QueueConflictError('Only an uncertain job can be manually resolved')
            self._audit(identifier, state, 'manual_verification: '+note)

    def recover_expired(self):
        """Never requeue possibly transmitted writes, even after process death."""
        now = time.time()
        with self._transaction():
            rows = self._db.execute("SELECT id FROM outbox WHERE state='in_flight' AND lease_until<=?", (now,)).fetchall()
            for row in rows:
                self._db.execute("UPDATE outbox SET state='uncertain',error='expired_claim',claim_token=NULL,lease_until=NULL WHERE id=?", (row['id'],))
                self._audit(row['id'], 'uncertain', 'expired_claim')
            count = self._db.execute("UPDATE inbox SET state='pending',claim_token=NULL,lease_until=NULL WHERE state='processing' AND lease_until<=?", (now,)).rowcount
        return {'outbox_uncertain': len(rows), 'inbox_pending': count}

    def stats(self):
        return {table: {row['state']: row['n'] for row in self._db.execute(f'SELECT state,COUNT(*) AS n FROM {table} GROUP BY state')}
                for table in ('outbox', 'inbox')}

    def list_inbox(self, *, limit=20):
        self._limit(limit)
        return tuple(InboxItem(decode_payload(row['payload']), row['state'], row['attempts'])
                     for row in self._db.execute('SELECT * FROM inbox ORDER BY sequence LIMIT ?', (limit,)))

    def claim_inbox(self, event_id=None, *, lease_seconds=300):
        self._lease(lease_seconds)
        with self._transaction():
            sql, params = "SELECT * FROM inbox WHERE state='pending'", []
            if event_id is not None:
                sql += ' AND event_id=?'
                params.append(event_id)
            row = self._db.execute(sql + ' ORDER BY sequence LIMIT 1', params).fetchone()
            if row is None:
                return None
            token = uuid.uuid4().hex
            self._db.execute("UPDATE inbox SET state='processing',attempts=attempts+1,claim_token=?,lease_until=? WHERE event_id=?",
                             (token, time.time()+lease_seconds, row['event_id']))
            return InboxItem(decode_payload(row['payload']), 'processing', row['attempts']+1, token)

    def finish_inbox(self, item, *, success=True):
        if type(success) is not bool:
            raise ValueError('success must be bool')
        with self._transaction():
            changed = self._db.execute("UPDATE inbox SET state=?,claim_token=NULL,lease_until=NULL WHERE event_id=? AND state='processing' AND claim_token=?",
                ('done' if success else 'pending', item.event.event_id, item.claim_token)).rowcount
            if changed != 1:
                raise QueueConflictError('Inbox item belongs to another claim')


class SQLiteCheckpointStore:
    """AccountUpdates adapter: the inbox and cursor share one SQL transaction."""
    def __init__(self, store):
        self.store, self.path = store, store.path
        self._revision = None
        self._loaded = False

    def load(self):
        row = self.store._db.execute('SELECT * FROM checkpoint WHERE id=1').fetchone()
        self._revision = row['revision'] if row is not None else None
        self._loaded = True
        return decode_payload(row['payload']) if row is not None else None

    def save(self, value):
        self.commit_batch(value, ())

    def commit_batch(self, value, events):
        if not self._loaded:
            raise QueueError('Load SQLite checkpoint before saving')
        payload = encode_payload(value)
        packed = []
        for event in events:
            if (not isinstance(event, AccountEvent) or len(event.event_id) != 64 or
                    any(c not in '0123456789abcdef' for c in event.event_id)):
                raise QueueError('Invalid durable event identity')
            packed.append((event.event_id, encode_payload(event)))
        with self.store._transaction():
            row = self.store._db.execute('SELECT revision FROM checkpoint WHERE id=1').fetchone()
            if (row['revision'] if row is not None else None) != self._revision:
                raise QueueConflictError('SQLite checkpoint changed in another consumer')
            for event_id, event in packed:
                self.store._db.execute("INSERT INTO inbox(event_id,payload,state,created) VALUES(?,?,'pending',?) ON CONFLICT(event_id) DO NOTHING",
                                       (event_id, event, time.time()))
            revision = (self._revision or 0) + 1
            self.store._db.execute('INSERT INTO checkpoint VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,payload=excluded.payload', (revision, payload))
        self._revision = revision
