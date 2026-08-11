-- Schema for the central MySQL side of the CentralDB plugin.
-- Run this against the database configured in central_db/mysql_database
-- (the same one entered in the plugin's settings tab).
--
-- Columns map directly to what CentralSyncEngine._push_local_to_central()
-- and ._pull_central_to_local() read/write in sync_engine.py.
--
-- ALREADY HAVE A central_songs TABLE FROM BEFORE? Don't re-run this --
-- CREATE TABLE IF NOT EXISTS is a no-op against an existing table, so it
-- won't add the new search_lyrics column to a table that predates it.
-- Run central_songs_migration_add_search_lyrics.sql instead.

CREATE TABLE IF NOT EXISTS central_songs (
    -- Surrogate key for the central copy, distinct from local_id so
    -- multiple OpenLP installs (with their own independent local ids)
    -- can all push into the same central table without id collisions.
    id              INT AUTO_INCREMENT PRIMARY KEY,

    -- The local sqlite `songs.id` this row was pushed from. NOT unique
    -- across installs by itself -- see the composite unique key below.
    local_id        INT NOT NULL,

    title           VARCHAR(255) NOT NULL,
    alternate_title VARCHAR(255),
    search_title    VARCHAR(255) NOT NULL,
    search_lyrics   MEDIUMTEXT,
    lyrics          MEDIUMTEXT NOT NULL,
    last_modified   DATETIME,
    last_editor     VARCHAR(255),

    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    -- Required for the ON DUPLICATE KEY UPDATE in _push_local_to_central
    -- to target the right row instead of inserting a duplicate every sync.
    UNIQUE KEY uq_central_songs_local_id (local_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;