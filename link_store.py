"""
:mod:`link_store` -- generic local "local id <-> central UUID" mapping.

Factored out of ``song_links.py`` so the same (already exercised)
identity-mapping logic can be reused for other entity types this plugin
syncs -- currently songs and custom slides -- without duplicating it.
See ``song_links.py``'s module docstring for the full rationale behind
why this mapping exists at all.

Each entity type gets its OWN sqlite file and table, not a shared one --
so a schema hiccup or corruption in one never affects another, and each
remains a simple, isolated file to delete and rebuild (forcing a clean
re-link) if something ever needs it.
"""

import logging
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path

log = logging.getLogger(__name__)


class LinkStore(object):
    """
    Owns a local ``local_id <-> central_uuid`` mapping table for one
    entity type, backed by its own sqlite file.

    :param db_path: Path to this entity type's own sqlite file.
    :param table_name: Table name within that file.
    :param local_id_column: Name of the local-id column (kept
        configurable so existing schemas -- e.g. ``song_links``'s
        ``local_song_id`` -- can be preserved exactly when adopting this
        shared implementation).
    :param entity_label: Human-readable label used only in log messages
        (e.g. ``'song'``, ``'custom slide'``).
    """

    def __init__(self, db_path, table_name='links', local_id_column='local_id',
                 entity_label='record'):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.table_name = table_name
        self.local_id_column = local_id_column
        self.entity_label = entity_label
        self._ensure_schema()

    def _connect(self):
        return sqlite3.connect(str(self.db_path))

    def _ensure_schema(self):
        with closing(self._connect()) as conn:
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.table_name} (
                    {self.local_id_column} INTEGER PRIMARY KEY,
                    central_uuid TEXT NOT NULL UNIQUE
                )
                """
            )
            conn.commit()

    def get_or_create_uuid(self, local_id):
        """
        Return the central UUID linked to a local record, minting and
        persisting a new one the first time this local record is seen
        (i.e. the first time it's pushed).
        """
        with closing(self._connect()) as conn:
            row = conn.execute(
                f'SELECT central_uuid FROM {self.table_name} WHERE {self.local_id_column} = ?',
                (local_id,)
            ).fetchone()
            if row:
                return row[0]
            new_uuid = str(uuid.uuid4())
            conn.execute(
                f'INSERT INTO {self.table_name} ({self.local_id_column}, central_uuid) VALUES (?, ?)',
                (local_id, new_uuid)
            )
            conn.commit()
            return new_uuid

    def get_local_id(self, central_uuid):
        """
        Return the local id linked to a central UUID, or ``None`` if
        this install has never seen that record before.
        """
        with closing(self._connect()) as conn:
            row = conn.execute(
                f'SELECT {self.local_id_column} FROM {self.table_name} WHERE central_uuid = ?',
                (central_uuid,)
            ).fetchone()
            return row[0] if row else None

    def has_link(self, central_uuid):
        return self.get_local_id(central_uuid) is not None

    def set_link(self, local_id, central_uuid):
        """
        Create the link for a central UUID, or repoint it to a different
        local id if one already exists for that UUID (e.g. the
        previously-linked local record was deleted and pull is now
        recreating it under a new local id).

        ``local_id`` is the table's primary key, with its own uniqueness
        constraint independent of ``central_uuid``'s. If this local
        record is already linked to a *different* central UUID, we don't
        relink it: silently repointing it here would mean whichever
        caller happens to run last "wins" and steals the record away
        from its existing link -- most likely during a reconciliation
        pass when two central rows share the same identifying text. We
        keep the first, already-established link and log a warning
        instead of crashing or guessing.
        """
        with closing(self._connect()) as conn:
            existing_uuid_for_local = conn.execute(
                f'SELECT central_uuid FROM {self.table_name} WHERE {self.local_id_column} = ?',
                (local_id,)
            ).fetchone()
            if existing_uuid_for_local is not None and existing_uuid_for_local[0] != central_uuid:
                log.warning(
                    'CentralDB: local %s %s is already linked to central '
                    'record %s; not relinking it to %s. This usually means '
                    'two central rows share the same identifying text.',
                    self.entity_label, local_id, existing_uuid_for_local[0], central_uuid)
                return
            conn.execute(
                f"""
                INSERT INTO {self.table_name} ({self.local_id_column}, central_uuid)
                VALUES (?, ?)
                ON CONFLICT(central_uuid)
                DO UPDATE SET {self.local_id_column} = excluded.{self.local_id_column}
                """,
                (local_id, central_uuid)
            )
            conn.commit()