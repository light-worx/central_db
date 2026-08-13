"""
:mod:`custom_slide_links` -- maps local ``custom.sqlite`` custom-slide ids
to a stable, install-independent UUID used to identify that slide in the
central MySQL database.

Same rationale as ``song_links.py``, applied to OpenLP's Custom Slides
plugin instead of Songs. Deliberately kept as its own sqlite file (not
sharing ``song_links.sqlite``) so the two entity types' link data stay
fully independent -- a problem with one link store can never corrupt or
block sync of the other.
"""

from pathlib import Path

from .link_store import LinkStore


def resolve_custom_link_store_path():
    """
    Resolve the path to this plugin's custom-slide link-store database.
    """
    from openlp.core.common.applocation import AppLocation
    return Path(AppLocation.get_section_data_path('central_db')) / 'custom_slide_links.sqlite'


class CustomSlideLinkStore(LinkStore):
    """
    Owns the ``local_custom_id <-> central_uuid`` mapping for this install.
    """

    def __init__(self, db_path):
        super(CustomSlideLinkStore, self).__init__(
            db_path, table_name='custom_slide_links', local_id_column='local_custom_id',
            entity_label='custom slide')