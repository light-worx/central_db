"""
The :mod:`central_db` module contains the CentralDB plugin for OpenLP.

This plugin synchronizes OpenLP's local SQLite songs database with a
central MySQL database via :class:`~central_db.sync_engine.CentralSyncEngine`.
"""

__all__ = ['central_db_plugin', 'sync_engine']