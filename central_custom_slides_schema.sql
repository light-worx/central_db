-- Schema for the central MySQL side of custom-slide syncing.
-- Run this against the same database as central_songs (the one
-- configured in central_db/mysql_database).
--
-- Columns map directly to what CentralSyncEngine._push_custom_to_central()
-- and ._pull_custom_to_local() read/write in sync_engine.py, mirroring
-- openlp.plugins.custom.lib.db.CustomSlide (title, text, credits,
-- theme_name) -- a much simpler schema than songs (no author
-- relationship, no separate search-text columns).
--
-- This table didn't exist before custom-slide sync was added, so unlike
-- central_songs there's no existing data to migrate -- just run this
-- once.

CREATE TABLE IF NOT EXISTS central_custom_slides (
    id              INT AUTO_INCREMENT PRIMARY KEY,

    -- Stable, install-independent identity for this slide, shared
    -- between every install's local copy via custom_slide_links.py.
    -- This -- not local_id -- is what push/pull actually match rows on.
    central_uuid    CHAR(36) NOT NULL,

    -- The local sqlite `custom_slide.id` this row was most recently
    -- pushed from. Not unique across installs (two installs can
    -- legitimately share a local_id for unrelated slides) -- kept only
    -- for debugging.
    local_id        INT NOT NULL,

    title           VARCHAR(255) NOT NULL,
    credits         MEDIUMTEXT,
    theme_name      VARCHAR(128),
    text            MEDIUMTEXT NOT NULL,
    last_editor     VARCHAR(255),

    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    -- Required for the ON DUPLICATE KEY UPDATE in _push_custom_to_central
    -- to target the right row instead of inserting a duplicate every sync.
    UNIQUE KEY uq_central_custom_slides_uuid (central_uuid)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;