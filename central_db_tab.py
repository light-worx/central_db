"""
:mod:`central_db_tab` -- the Settings dialog tab for the CentralDB plugin.

Lets the user configure the central MySQL connection (host/port/user/
password/database) and their editor name, and offers a "Test Connection"
button that exercises :meth:`CentralSyncEngine.test_connection` without
running a full sync.
"""

from PyQt5 import QtCore, QtWidgets

from openlp.core.common.i18n import translate
from openlp.core.lib.settingstab import SettingsTab

from .sync_engine import CentralSyncEngine, resolve_songs_db_path


class CentralDBTab(SettingsTab):
    """
    CentralDBTab is the CentralDB settings tab in the settings dialog.
    """

    def __init__(self, parent, title, visible_title=None, icon_path=None):
        """
        ``SettingsTab.__init__`` only sets ``self.icon_path`` when
        ``icon_path`` is truthy, so passing ``None`` (as the plugin
        currently does, pending a real icon) leaves the attribute
        missing entirely rather than ``None``. ``SettingsForm.insert_tab``
        reads ``tab_widget.icon_path`` unconditionally, so we guarantee
        the attribute exists here regardless of what's passed in.
        """
        self.icon_path = icon_path
        super(CentralDBTab, self).__init__(parent, title, visible_title, icon_path)

    def setup_ui(self):
        """
        Set up the configuration tab UI.
        """
        self.setObjectName('CentralDBTab')
        super(CentralDBTab, self).setup_ui()

        # --- Connection group box (left column) ---
        self.connection_group_box = QtWidgets.QGroupBox(self.left_column)
        self.connection_group_box.setObjectName('connection_group_box')
        self.connection_layout = QtWidgets.QFormLayout(self.connection_group_box)
        self.connection_layout.setObjectName('connection_layout')

        self.host_label = QtWidgets.QLabel(self.connection_group_box)
        self.host_edit = QtWidgets.QLineEdit(self.connection_group_box)
        self.host_edit.setObjectName('host_edit')
        self.connection_layout.addRow(self.host_label, self.host_edit)

        self.port_label = QtWidgets.QLabel(self.connection_group_box)
        self.port_spin_box = QtWidgets.QSpinBox(self.connection_group_box)
        self.port_spin_box.setObjectName('port_spin_box')
        self.port_spin_box.setRange(1, 65535)
        self.connection_layout.addRow(self.port_label, self.port_spin_box)

        self.user_label = QtWidgets.QLabel(self.connection_group_box)
        self.user_edit = QtWidgets.QLineEdit(self.connection_group_box)
        self.user_edit.setObjectName('user_edit')
        self.connection_layout.addRow(self.user_label, self.user_edit)

        self.password_label = QtWidgets.QLabel(self.connection_group_box)
        self.password_edit = QtWidgets.QLineEdit(self.connection_group_box)
        self.password_edit.setObjectName('password_edit')
        self.password_edit.setEchoMode(QtWidgets.QLineEdit.Password)
        self.connection_layout.addRow(self.password_label, self.password_edit)

        self.database_label = QtWidgets.QLabel(self.connection_group_box)
        self.database_edit = QtWidgets.QLineEdit(self.connection_group_box)
        self.database_edit.setObjectName('database_edit')
        self.connection_layout.addRow(self.database_label, self.database_edit)

        self.left_layout.addWidget(self.connection_group_box)

        # --- Sync options group box (left column) ---
        self.sync_group_box = QtWidgets.QGroupBox(self.left_column)
        self.sync_group_box.setObjectName('sync_group_box')
        self.sync_layout = QtWidgets.QFormLayout(self.sync_group_box)
        self.sync_layout.setObjectName('sync_layout')

        self.editor_label = QtWidgets.QLabel(self.sync_group_box)
        self.editor_edit = QtWidgets.QLineEdit(self.sync_group_box)
        self.editor_edit.setObjectName('editor_edit')
        self.sync_layout.addRow(self.editor_label, self.editor_edit)

        self.sync_on_startup_check_box = QtWidgets.QCheckBox(self.sync_group_box)
        self.sync_on_startup_check_box.setObjectName('sync_on_startup_check_box')
        self.sync_layout.addRow(self.sync_on_startup_check_box)

        self.sync_on_shutdown_check_box = QtWidgets.QCheckBox(self.sync_group_box)
        self.sync_on_shutdown_check_box.setObjectName('sync_on_shutdown_check_box')
        self.sync_layout.addRow(self.sync_on_shutdown_check_box)

        self.left_layout.addWidget(self.sync_group_box)
        self.left_layout.addStretch()

        # --- Test connection + status (right column) ---
        self.test_button = QtWidgets.QPushButton(self.right_column)
        self.test_button.setObjectName('test_button')
        self.test_button.clicked.connect(self.on_test_button_clicked)
        self.right_layout.addWidget(self.test_button)

        self.status_label = QtWidgets.QLabel(self.right_column)
        self.status_label.setObjectName('status_label')
        self.status_label.setWordWrap(True)
        self.right_layout.addWidget(self.status_label)

        self.sync_now_button = QtWidgets.QPushButton(self.right_column)
        self.sync_now_button.setObjectName('sync_now_button')
        self.sync_now_button.clicked.connect(self.on_sync_now_button_clicked)
        self.right_layout.addWidget(self.sync_now_button)

        self.sync_status_label = QtWidgets.QLabel(self.right_column)
        self.sync_status_label.setObjectName('sync_status_label')
        self.sync_status_label.setWordWrap(True)
        self.right_layout.addWidget(self.sync_status_label)
        self.right_layout.addStretch()

    def retranslate_ui(self):
        """
        Set up the interface translation strings.
        """
        self.connection_group_box.setTitle(
            translate('CentralDBPlugin.CentralDBTab', 'MySQL Connection'))
        self.host_label.setText(translate('CentralDBPlugin.CentralDBTab', 'Host:'))
        self.port_label.setText(translate('CentralDBPlugin.CentralDBTab', 'Port:'))
        self.user_label.setText(translate('CentralDBPlugin.CentralDBTab', 'Username:'))
        self.password_label.setText(translate('CentralDBPlugin.CentralDBTab', 'Password:'))
        self.database_label.setText(translate('CentralDBPlugin.CentralDBTab', 'Database:'))
        self.sync_group_box.setTitle(
            translate('CentralDBPlugin.CentralDBTab', 'Sync Options'))
        self.editor_label.setText(translate('CentralDBPlugin.CentralDBTab', 'Editor name:'))
        self.sync_on_startup_check_box.setText(
            translate('CentralDBPlugin.CentralDBTab', 'Sync automatically when OpenLP starts'))
        self.sync_on_shutdown_check_box.setText(
            translate('CentralDBPlugin.CentralDBTab', 'Sync automatically when OpenLP exits'))
        self.test_button.setText(
            translate('CentralDBPlugin.CentralDBTab', 'Test Connection'))
        self.sync_now_button.setText(
            translate('CentralDBPlugin.CentralDBTab', 'Sync Now'))

    def on_test_button_clicked(self):
        """
        Save the current field values (so the test reflects what's on
        screen, not stale settings) and try connecting to MySQL.
        """
        self.save()
        engine = CentralSyncEngine(settings=self.settings)
        success, message = engine.test_connection()
        self.status_label.setText(message)
        self.status_label.setStyleSheet(
            'color: green;' if success else 'color: red;')

    def on_sync_now_button_clicked(self):
        """
        Save the current field values and run a full bidirectional sync
        immediately, rather than waiting for the next OpenLP
        startup/shutdown. Runs on the UI thread (a wait cursor is shown
        while it's in progress) -- fine for occasional manual use, but
        a large library or a slow connection will visibly block the UI;
        that's a candidate for a background thread later if it becomes
        annoying in practice.
        """
        self.save()
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            self._wait_for_startup_sync()
            engine = CentralSyncEngine(
                settings=self.settings, songs_db_path=resolve_songs_db_path())
            success, message = engine.run()
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        self.sync_status_label.setText(message)
        self.sync_status_label.setStyleSheet(
            'color: green;' if success else 'color: red;')

    def _wait_for_startup_sync(self):
        """
        If the plugin's automatic background startup sync is still
        running, wait briefly (max 5s) for it to finish before starting
        this manual one -- otherwise both would write to the same local
        songs.sqlite and link store at once. Looks the plugin instance
        up via Registry() (every plugin self-registers under its
        de-humped class name -- 'central_db_plugin' here -- the same
        mechanism ``self.settings``/``self.main_window`` use elsewhere).
        Best-effort: does nothing if the plugin instance can't be found.
        """
        from openlp.core.common.registry import Registry
        plugin = Registry().get('central_db_plugin')
        thread = getattr(plugin, '_startup_sync_thread', None)
        if thread is not None and thread.isRunning():
            thread.wait(5000)

    def load(self):
        """
        Load the CentralDB settings.
        """
        self.host_edit.setText(self.settings.value('central_db/mysql_host'))
        self.port_spin_box.setValue(int(self.settings.value('central_db/mysql_port') or 3306))
        self.user_edit.setText(self.settings.value('central_db/mysql_user'))
        self.password_edit.setText(self.settings.value('central_db/mysql_password'))
        self.database_edit.setText(self.settings.value('central_db/mysql_database'))
        self.editor_edit.setText(self.settings.value('central_db/editor_name'))
        self.sync_on_startup_check_box.setChecked(
            bool(self.settings.value('central_db/sync_on_startup')))
        self.sync_on_shutdown_check_box.setChecked(
            bool(self.settings.value('central_db/sync_on_shutdown')))
        self.status_label.setText('')
        self.sync_status_label.setText('')

    def save(self):
        """
        Save the CentralDB settings.
        """
        self.settings.setValue('central_db/mysql_host', self.host_edit.text())
        self.settings.setValue('central_db/mysql_port', self.port_spin_box.value())
        self.settings.setValue('central_db/mysql_user', self.user_edit.text())
        self.settings.setValue('central_db/mysql_password', self.password_edit.text())
        self.settings.setValue('central_db/mysql_database', self.database_edit.text())
        self.settings.setValue('central_db/editor_name', self.editor_edit.text())
        self.settings.setValue(
            'central_db/sync_on_startup', self.sync_on_startup_check_box.isChecked())
        self.settings.setValue(
            'central_db/sync_on_shutdown', self.sync_on_shutdown_check_box.isChecked())
        if self.tab_visited:
            self.settings_form.register_post_process('central_db_config_updated')
        self.tab_visited = False