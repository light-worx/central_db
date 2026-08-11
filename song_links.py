"""
:mod:`song_links` -- maps local ``songs.sqlite`` song ids to a stable,
install-independent UUID used to identify that song in the central MySQL
database.

Why this exists: ``songs.sqlite``'s own primary key is only meaningful
*within a single OpenLP install*. Two different installs will both have a
song with local id ``5`` -- those are two unrelated songs. Without a
shared identifier, pull/push can only guess at identity (by title, which
is ambiguous for genuinely different songs that share a title -- multiple
arrangements of "Amazing Grace", for example).

This module owns a small sqlite database, entirely separate from
``songs.sqlite``, mapping ``local_song_id <-> central_uuid``. It's kept
deliberately separate from the real Songs-plugin schema rather than added
as a column there: we don't own that schema, and don't want a future
Songs-plugin schema upgrade to ever have to account for a column we
added to it.
"""

import logging
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path

log = logging.getLogger(__name__)


def resolve_link_store_path():
    """
    Resolve the path to this plugin's own link-store database. Kept
    alongside ``resolve_songs_db_path()`` in ``sync_engine.py`` as the
    equivalent helper for the link store.
    """
    from openlp.core.common.applocation import AppLocation
    return Path(AppLocation.get_section_data_path('central_db')) / 'song_links.sqlite'


class SongLinkStore(object):
    """
    Owns the ``local_song_id <-> central_uuid`` mapping for this install.
    """

    def __init__(self, db_path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self):
        return sqlite3.connect(str(self.db_path))

    def _ensure_schema(self):
        with closing(self._connect()) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS song_links (
                    local_song_id INTEGER PRIMARY KEY,
                    central_uuid  TEXT NOT NULL UNIQUE
                )
                """
            )
            conn.commit()

    def get_or_create_uuid(self, local_song_id):
        """
        Return the central UUID linked to a local song, minting and
        persisting a new one the first time this local song is seen
        (i.e. the first time it's pushed).
        """
        with closing(self._connect()) as conn:
            row = conn.execute(
                'SELECT central_uuid FROM song_links WHERE local_song_id = ?',
                (local_song_id,)
            ).fetchone()
            if row:
                return row[0]
            new_uuid = str(uuid.uuid4())
            conn.execute(
                'INSERT INTO song_links (local_song_id, central_uuid) VALUES (?, ?)',
                (local_song_id, new_uuid)
            )
            conn.commit()
            return new_uuid

    def get_local_id(self, central_uuid):
        """
        Return the local song id linked to a central UUID, or ``None``
        if this install has never seen that song before (a genuinely new
        song, as far as this install is concerned).
        """
        with closing(self._connect()) as conn:
            row = conn.execute(
                'SELECT local_song_id FROM song_links WHERE central_uuid = ?',
                (central_uuid,)
            ).fetchone()
            return row[0] if row else None

    def has_link(self, central_uuid):
        return self.get_local_id(central_uuid) is not None

    def set_link(self, local_song_id, central_uuid):
        """
        Create the link for a central UUID, or repoint it to a different
        local song id if one already exists for that UUID (e.g. the
        previously-linked local song was deleted and pull is now
        recreating it under a new local id).

        ``local_song_id`` is the table's primary key, so it has its own
        uniqueness constraint independent of ``central_uuid``'s. If this
        local song is already linked to a *different* central UUID, we
        don't relink it: silently repointing it here would mean whichever
        caller happens to run last "wins" and steals the local song away
        from its existing link -- most likely to happen during
        :meth:`CentralSyncEngine._reconcile_links` when two central rows
        share the same title. We keep the first, already-established
        link and log a warning instead of crashing or guessing.
        """
        with closing(self._connect()) as conn:
            existing_uuid_for_local = conn.execute(
                'SELECT central_uuid FROM song_links WHERE local_song_id = ?',
                (local_song_id,)
            ).fetchone()
            if existing_uuid_for_local is not None and existing_uuid_for_local[0] != central_uuid:
                log.warning(
                    'CentralDB: local song %s is already linked to central '
                    'song %s; not relinking it to %s. This usually means '
                    'two central rows share the same title.',
                    local_song_id, existing_uuid_for_local[0], central_uuid)
                return
            conn.execute(
                """
                INSERT INTO song_links (local_song_id, central_uuid)
                VALUES (?, ?)
                ON CONFLICT(central_uuid)
                DO UPDATE SET local_song_id = excluded.local_song_id
                """,
                (local_song_id, central_uuid)
            )
            conn.commit()