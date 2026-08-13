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

This is now a thin wrapper around the generic :class:`link_store.LinkStore`
(originally this module had its own standalone sqlite implementation;
that logic moved to ``link_store.py`` so it could be reused for other
entity types -- custom slides, currently -- without duplication). The
table name (``song_links``) and column name (``local_song_id``) are kept
exactly as they were before that refactor, so this remains a drop-in
read of any ``song_links.sqlite`` file created by earlier versions of
this plugin -- no migration needed.
"""

from pathlib import Path

from .link_store import LinkStore


def resolve_link_store_path():
    """
    Resolve the path to this plugin's song link-store database.
    """
    from openlp.core.common.applocation import AppLocation
    return Path(AppLocation.get_section_data_path('central_db')) / 'song_links.sqlite'


class SongLinkStore(LinkStore):
    """
    Owns the ``local_song_id <-> central_uuid`` mapping for this install.
    """

    def __init__(self, db_path):
        super(SongLinkStore, self).__init__(
            db_path, table_name='song_links', local_id_column='local_song_id',
            entity_label='song')