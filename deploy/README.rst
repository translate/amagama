Deployment at OSUOSL
====================

Configuration of the amaGama server at fosstranslation.osuosl.org
(amagama-live.translatehouse.org).

======================== =============================================
File                     Installed as
======================== =============================================
nginx-amagama.conf       /etc/nginx/sites-enabled/amagama
uwsgi.ini                /srv/amagama/uwsgi.ini
amagama.uwsgi.service    /etc/systemd/system/amagama.uwsgi.service
amagama-fetch.service    /etc/systemd/system/amagama-fetch.service
amagama-fetch.timer      /etc/systemd/system/amagama-fetch.timer
amagama-update.service   /etc/systemd/system/amagama-update.service
amagama-update.timer     /etc/systemd/system/amagama-update.timer
requirements.txt         /srv/amagama/venv (pip install -r)
======================== =============================================

Everything else is under /srv/amagama:

======================== =============================================
amagama/                 the checkout (root)
venv/                    its virtualenv (root)
uwsgi.ini                the web app's uWSGI settings
local_settings.py        the web app's settings, with the read-only
                         database user (root:www-data, 640)
updater_settings.py      the updater's settings, with the read-write
                         database user (amagama, 600)
cache/                   the fetch cache (amagama)
logs/                    nginx and uWSGI logs (root)
======================== =============================================

The settings files aren't kept here.


First deployment
----------------

The checkout and its virtualenv::

    sudo apt install git subversion nginx uwsgi uwsgi-plugin-python3
    sudo install -d /srv/amagama
    sudo git clone https://github.com/translate/amagama.git /srv/amagama/amagama
    sudo python3 -m venv /srv/amagama/venv
    sudo /srv/amagama/venv/bin/pip install -r /srv/amagama/amagama/deploy/requirements.txt
    sudo /srv/amagama/venv/bin/pip install --no-deps -e /srv/amagama/amagama
    sudo /srv/amagama/venv/bin/python -c "from phply import phpparse; phpparse.make_parser()"

The last line writes the PHP reader's parser tables into the virtualenv, which
the updater can't write to; without them it builds them on every run and warns
"Couldn't create 'phply.parsetab'".

The web app's files::

    sudo install -d /srv/amagama/logs
    sudo install -m 644 /srv/amagama/amagama/deploy/uwsgi.ini /srv/amagama/
    sudo install -m 640 -g www-data /dev/null /srv/amagama/local_settings.py

The updater's user, cache and settings::

    sudo useradd --system --home-dir /srv/amagama/cache amagama
    sudo install -d -o amagama -g amagama /srv/amagama/cache
    sudo install -m 600 -o amagama -g amagama /dev/null /srv/amagama/updater_settings.py

``local_settings.py`` holds the database settings with the read-only user, and
``SECRET_KEY``. ``updater_settings.py`` holds them with the read-write user,
and the updater's own::

    DB_HOST = "..."
    DB_NAME = "fosstranslation2"
    DB_USER = "fosstranslation"
    DB_PASSWORD = "..."
    UPDATER_CACHE_DIR = "/srv/amagama/cache"
    # One import worker: the box has 2 GB of memory.
    UPDATER_WORKERS = 1

Install the units and the nginx site from the table above, then::

    sudo systemctl daemon-reload
    sudo systemctl enable --now amagama.uwsgi amagama-fetch.timer
    sudo nginx -t && sudo systemctl reload nginx

The cache starts empty and fills a little each day: a run fetches at most 100
blocks for the first time (``UPDATER_FIRST_FETCHES``), so the shipped sources
file takes about three weeks. Each run picks its blocks at random, so it
takes a little from every upstream, not all of one.

Before the first update, keep the current data as the ``legacy`` snapshot
(``amagama-manage snapshot_db -s en --name legacy``, as ``amagama``); an
update refuses to replace data it can't rebuild. Then::

    sudo systemctl enable --now amagama-update.timer

Each update imports what has been fetched so far.


Scheduled updates
-----------------

``amagama-fetch.timer`` refreshes the fetch cache every day
(``amagama-manage fetch_sources``): each source's languages come due in turn,
so a day's fetch is small. ``amagama-update.timer`` rebuilds and swaps in the
database every Sunday (``amagama-manage update -s en --no-fetch``), from the
cache as the fetch timer left it, without fetching itself; a
rebuild takes hours, so it isn't run daily. They share a lock on the cache,
and whichever finds the other running skips that run. A run missed while the
box was down happens when it's back.

Both run as the ``amagama`` user, with settings the web app can't read, since
they need the database user that can write.

The output goes to the journal (``journalctl -u amagama-update``). A source
that fails to fetch is listed there, and the unit is marked failed
(``systemctl --failed``) though the rest was rebuilt and swapped in.


Upgrading
---------

::

    sudo git -C /srv/amagama/amagama pull --ff-only
    sudo /srv/amagama/venv/bin/pip install -r /srv/amagama/amagama/deploy/requirements.txt
    sudo /srv/amagama/venv/bin/python -c "from phply import phpparse; phpparse.make_parser()"
    sudo systemctl restart amagama.uwsgi
