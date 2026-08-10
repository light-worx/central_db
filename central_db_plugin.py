"""
:mod:`central_db_plugin` -- OpenLP plugin that keeps the local songs
database in sync with a central MySQL database.

Verified against the real OpenLP 3.1.6 source (openlp/core/lib/plugin.py,
openlp/core/state.py, openlp/plugins/songs/songsplugin.py) rather than
assumption, after two rounds of import errors surfaced incorrect guesses.

Two bootstrap issues are specifically guarded against here:

* **Bootstrap KeyError** -- ``Plugin.__init__`` does
  ``self.name_strings = self.text_strings[StringContent.Name]`` right
  after calling ``self.set_plugin_text_strings()`` (note: *not*
  ``set_text_strings`` -- that was the actual bug in the previous
  version of this file). If that method doesn't populate
  ``self.text_strings[StringContent.Name]``, the lookup raises
  ``KeyError: 'name'`` because ``StringContent.Name`` is literally the
  string ``'name'``, not an Enum member.

* **Missing from Tools -> Plugin Manager** -- OpenLP does not use any
  ``settings_section`` / ``programmatic_name`` / ``status_default``
  attributes (those don't exist in the real API). Visibility comes from
  registering with ``State().add_service(name, weight, is_plugin=True)``
  and ``State().update_pre_conditions(name, check_pre_conditions())``,
  exactly as ``SongsPlugin`` does in ``songsplugin.py``. Skipping this
  step means the plugin loads but ``State().list_plugins()`` never
  returns it, so it's invisible everywhere in the UI.
"""

import logging

from PyQt5 import QtCore, QtWidgets

from openlp.core.common.i18n import translate
from openlp.core.lib import build_icon
from openlp.core.lib.plugin import Plugin, PluginStatus, StringContent
from openlp.core.lib.ui import create_action
from openlp.core.state import State
from openlp.core.ui.icons import UiIcons

from openlp.plugins.central_db.central_db_tab import CentralDBTab
from openlp.plugins.central_db.sync_engine import CentralSyncEngine, resolve_songs_db_path

log = logging.getLogger(__name__)

# Registered via self.settings.extend_default_settings() in __init__.
__default_settings__ = {
    'central_db/status': PluginStatus.Active,
    'central_db/mysql_host': '',
    'central_db/mysql_port': 3306,
    'central_db/mysql_user': '',
    'central_db/mysql_password': '',
    'central_db/mysql_database': '',
    'central_db/editor_name': '',
}


class CentralDBPlugin(Plugin):
    """
    Synchronizes OpenLP's local ``songs.sqlite`` database with a central
    MySQL database on startup and shutdown.
    """

    log.info('CentralDB Plugin loaded')

    def __init__(self):
        """
        NOTE: ``self.name`` must be ``'central_db'``, matching the
        de-humped class name (``CentralDBPlugin`` -> ``central_db_plugin``).
        ``RegistryBase.__init__`` (a Plugin base class) registers this
        instance under that de-humped name, and ``State().list_plugins()``
        looks it up as ``f'{self.name}_plugin'``. A mismatch here (e.g.
        ``'centraldb'``) means the plugin loads but is never found.
        """
        super(CentralDBPlugin, self).__init__('central_db', settings_tab_class=CentralDBTab)
        self.weight = -5
        # Reuses OpenLP's built-in 'database' icon (qtawesome mdi.database)
        # -- same pattern SongsPlugin uses for its own icon. Shows up both
        # in Tools -> Plugin Manager (via self.icon) and on the settings
        # tab's list entry (via self.icon_path, passed through to
        # CentralDBTab by the base class's create_settings_tab()).
        self.icon_path = UiIcons().database
        self.icon = build_icon(self.icon_path)

        self.settings.extend_default_settings(__default_settings__)

        # Required for the plugin to appear in Tools -> Plugin Manager;
        # mirrors what SongsPlugin.__init__ does.
        State().add_service(self.name, self.weight, is_plugin=True)
        State().update_pre_conditions(self.name, self.check_pre_conditions())

    # ------------------------------------------------------------------ #
    # Text strings -- this is the method Plugin.__init__ actually calls
    # ------------------------------------------------------------------ #
    def set_plugin_text_strings(self):
        """
        Called from ``Plugin.__init__`` before
        ``self.name_strings = self.text_strings[StringContent.Name]`` is
        evaluated. Must populate ``self.text_strings[StringContent.Name]``
        or bootstrap raises ``KeyError: 'name'``.
        """
        # Name for the Plugin list
        self.text_strings[StringContent.Name] = {
            'singular': translate('CentralDBPlugin', 'Central DB', 'name singular'),
            'plural': translate('CentralDBPlugin', 'Central DBs', 'name plural')
        }
        # Name for MediaDockManager / SettingsManager (get_visible_name()
        # reads this key, so it must exist even though this plugin has no
        # media dock item)
        self.text_strings[StringContent.VisibleName] = {
            'title': translate('CentralDBPlugin', 'Central DB', 'container title')
        }

    # ------------------------------------------------------------------ #
    # Lifecycle hooks
    # ------------------------------------------------------------------ #
    def check_pre_conditions(self):
        """
        Note the real method name is ``check_pre_conditions`` (with an
        underscore between "check" and "pre"), not ``check_preconditions``.
        Must never raise and must never return a falsy value.
        """
        return True

    def initialise(self):
        super(CentralDBPlugin, self).initialise()
        log.info('CentralDB: initialising, running startup sync.')
        self._run_sync()

    def finalise(self):
        log.info('CentralDB: finalising, running shutdown sync.')
        self._run_sync()
        super(CentralDBPlugin, self).finalise()

    def _run_sync(self):
        """
        Silent sync used by initialise()/finalise() (app startup and
        shutdown). Logs the result rather than showing it -- for
        feedback while working, use the Tools menu item or the
        settings tab's "Sync Now" button instead.
        """
        try:
            engine = CentralSyncEngine(
                settings=self.settings, songs_db_path=resolve_songs_db_path()
            )
            success, message = engine.run()
            log.info('CentralDB: %s', message) if success else log.warning('CentralDB: %s', message)
        except Exception:
            # A sync failure must never block OpenLP startup/shutdown.
            log.exception('CentralDB: unexpected error running sync.')

    # ------------------------------------------------------------------ #
    # Tools menu -- manual "Sync Now" trigger
    # ------------------------------------------------------------------ #
    def add_tools_menu_item(self, tools_menu):
        """
        Give the CentralDB plugin the opportunity to add an item to the
        Tools menu, so a sync can be triggered on demand instead of only
        at OpenLP startup/shutdown.

        :param tools_menu: The actual **Tools** menu item.
        """
        self.tools_sync_now_item = create_action(
            tools_menu, 'toolsCentralDBSyncItem',
            text=translate('CentralDBPlugin', 'Sync Central DB Now'),
            statustip=translate(
                'CentralDBPlugin',
                'Manually sync the local songs database with the central MySQL database.'),
            triggers=self.on_tools_sync_now_triggered)
        tools_menu.addAction(self.tools_sync_now_item)

    def on_tools_sync_now_triggered(self):
        """
        Run a full bidirectional sync immediately and report the result
        in a message box. Runs on the UI thread with a wait cursor shown
        -- fine for occasional manual use; a slow connection or a large
        library would be a good reason to move this to a background
        thread later.
        """
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            engine = CentralSyncEngine(
                settings=self.settings, songs_db_path=resolve_songs_db_path())
            success, message = engine.run()
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        if success:
            QtWidgets.QMessageBox.information(
                self.main_window,
                translate('CentralDBPlugin', 'Sync Complete'), message)
        else:
            QtWidgets.QMessageBox.warning(
                self.main_window,
                translate('CentralDBPlugin', 'Sync Failed'), message)

    # ------------------------------------------------------------------ #
    # UI stubs -- CentralDB has no media manager panel (it has a
    # settings tab now, built by the base class from settings_tab_class)
    # ------------------------------------------------------------------ #
    def create_media_manager_item(self):
        return None

    def about(self):
        return translate(
            'CentralDBPlugin',
            'The <strong>Central DB</strong> plugin synchronizes the '
            'local songs database with a central MySQL database on '
            'startup and shutdown.'
        )