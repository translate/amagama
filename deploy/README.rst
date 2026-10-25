Deployment at OSUOSL
====================

Configuration of the amaGama server at fosstranslation.osuosl.org
(amagama-live.translatehouse.org).

======================== =============================================
File                     Installed as
======================== =============================================
nginx-amagama.conf       /etc/nginx/sites-enabled/amagama
uwsgi.ini                /var/www/sites/amagama_fw/uwsgi.ini
amagama.uwsgi.service    /etc/systemd/system/amagama.uwsgi.service
amagama-fetch.service    /etc/systemd/system/amagama-fetch.service
amagama-fetch.timer      /etc/systemd/system/amagama-fetch.timer
amagama-update.service   /etc/systemd/system/amagama-update.service
amagama-update.timer     /etc/systemd/system/amagama-update.timer
requirements.txt         /var/www/sites/amagama_fw/env3 (pip install -r)
======================== =============================================

The checkout is /var/www/sites/amagama_fw/amagama.  Settings, including the
database credentials, are in /var/www/sites/amagama_fw/local_settings.py and
are not kept here.


Scheduled updates
-----------------

``amagama-fetch.timer`` refreshes the fetch cache every day
(``amagama-manage fetch_sources``): each source's languages come due in turn,
so a day's fetch is small. ``amagama-update.timer`` rebuilds and swaps in the
database every Sunday (``amagama-manage update -s en``), from the cache; a
rebuild takes hours, so it isn't run daily. They share a lock on the cache,
and whichever finds the other running skips that run. A run missed while the
box was down happens when it's back.

Both run as a system user of their own, ``amagama``, with settings the web app
can't read, since they need the database user that can write::

    sudo apt install git subversion
    sudo useradd --system --home-dir /var/www/sites/amagama_fw/cache amagama
    sudo install -d -o amagama -g amagama /var/www/sites/amagama_fw/cache
    sudo install -m 600 -o amagama -g amagama /dev/null \
        /var/www/sites/amagama_fw/updater_settings.py

``updater_settings.py`` holds the database settings with the read-write user,
and the updater's own::

    DB_HOST = "..."
    DB_NAME = "fosstranslation2"
    DB_USER = "fosstranslation"
    DB_PASSWORD = "..."
    UPDATER_CACHE_DIR = "/var/www/sites/amagama_fw/cache"
    # One import worker: the box has 2 GB of memory.
    UPDATER_WORKERS = 1

Before the first update, keep the current data as the ``legacy`` snapshot
(``amagama-manage snapshot_db -s en --name legacy``, as ``amagama``); an
update refuses to replace data it can't rebuild. Then::

    sudo systemctl daemon-reload
    sudo systemctl enable --now amagama-fetch.timer amagama-update.timer

The output goes to the journal (``journalctl -u amagama-update``). A source
that fails to fetch is listed there, and the unit is marked failed
(``systemctl --failed``) though the rest was rebuilt and swapped in.
