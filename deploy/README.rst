Deployment at OSUOSL
====================

Configuration of the amaGama server at fosstranslation.osuosl.org
(amagama-live.translatehouse.org).

======================== =============================================
File                     Installed as
======================== =============================================
nginx-amagama.conf       /etc/nginx/sites-enabled/amagama
logrotate-amagama        /etc/logrotate.d/amagama-srv
uwsgi.ini                /srv/amagama/uwsgi.ini
amagama.uwsgi.service    /etc/systemd/system/amagama.uwsgi.service
amagama-fetch.service    /etc/systemd/system/amagama-fetch.service
amagama-fetch.timer      /etc/systemd/system/amagama-fetch.timer
amagama-update.service   /etc/systemd/system/amagama-update.service
amagama-update.timer     /etc/systemd/system/amagama-update.timer
amagama-drop-old.service /etc/systemd/system/amagama-drop-old.service
amagama-drop-old.timer   /etc/systemd/system/amagama-drop-old.timer
amagama-stats.service    /etc/systemd/system/amagama-stats.service
amagama-stats.timer      /etc/systemd/system/amagama-stats.timer
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
cache/                   the fetch cache (amagama), and stats.json,
                         the statistics page's figures
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

The web app reads the statistics page's figures and ISO 639-3's retired
codes from files the updater writes, so ``local_settings.py`` also has::

    STATS_FILE = "/srv/amagama/cache/stats.json"
    ISO639_RETIREMENTS_FILE = "/srv/amagama/cache/iso-639-3_Retirements.tab"

Install the units and the nginx site from the table above, then::

    sudo systemctl daemon-reload
    sudo systemctl enable --now amagama.uwsgi amagama-fetch.timer amagama-stats.timer
    sudo nginx -t && sudo systemctl reload nginx

The cache starts empty and fills a little each day: a run fetches at most 100
blocks for the first time (``UPDATER_FIRST_FETCHES``), so the shipped sources
file takes about three weeks. Each run takes ``core`` blocks first, then
``platform``, ``discovered`` and untiered ones, at random within each tier,
so a tier's blocks come from many upstreams, not all from one.

Before the first update, keep the current data as the ``legacy`` snapshot
(``amagama-manage snapshot_db -s en --name legacy``, as ``amagama``); an
update refuses to replace data it can't rebuild. Then::

    sudo systemctl enable --now amagama-update.timer amagama-drop-old.timer
    systemctl list-timers 'amagama-*'

Each update imports what has been fetched so far. The last line should list
the four timers.


Switching from the old deployment
---------------------------------

The old web app runs from /var/www/sites/amagama_fw, with its own checkout,
virtualenv and settings, and reads the database's old tables
(``public.sources_en`` and ``public.targets_en``). Once ``migrate_db``,
``deploy_db``, the ``legacy`` snapshot and a first update have built ``en``,
switching replaces only the uWSGI unit and the nginx site; the old checkout and
tables stay until the new app has run for a while.

Try the new app first without serving it, on a local port::

    sudo systemd-run --unit=amagama-trial -p User=www-data -p Group=www-data \
        -p KillSignal=SIGQUIT /usr/bin/uwsgi --plugins python3 \
        --chdir /srv/amagama/amagama --home /srv/amagama/venv \
        --env AMAGAMA_CONFIG=/srv/amagama/local_settings.py \
        --module wsgi:application --master --lazy-apps --processes 1 --threads 4 \
        --http-socket 127.0.0.1:8081 \
        --static-map /static=/srv/amagama/amagama/amagama/static
    curl -s http://127.0.0.1:8081/api/v1/en/af/unit/file
    sudo systemctl stop amagama-trial

``local_settings.py`` needs ``ENABLE_WEB_UI = True`` for the page at ``/``.

Switch, keeping the old unit and site outside the directories that load them::

    sudo install -d -m 700 /root/amagama-switch
    sudo cp /etc/systemd/system/amagama.uwsgi.service /etc/nginx/sites-enabled/amagama \
        /root/amagama-switch/
    sudo install -d /srv/amagama/logs
    sudo install -m 644 /srv/amagama/amagama/deploy/uwsgi.ini /srv/amagama/
    sudo cp /srv/amagama/amagama/deploy/amagama.uwsgi.service \
        /srv/amagama/amagama/deploy/amagama-update.service /etc/systemd/system/
    sudo cp /srv/amagama/amagama/deploy/nginx-amagama.conf /etc/nginx/sites-enabled/amagama
    sudo systemctl daemon-reload && sudo systemctl restart amagama.uwsgi
    sudo nginx -t && sudo systemctl reload nginx

Check it from outside: each line should start with 200, and the first should
show the ``Strict-Transport-Security`` header::

    curl -sI https://amagama-live.translatehouse.org/ | grep -iE '^HTTP|^strict'
    for p in /static/css/amagama.css /tmserver/languages/ /tmserver/en/af/unit/file \
            /api/v1/en/af/unit/file /api/v1/sources/; do
        curl -s -o /dev/null -w "%{http_code} $p\n" https://amagama-live.translatehouse.org$p
    done

To go back, put the old unit and site back::

    sudo cp /root/amagama-switch/amagama.uwsgi.service /etc/systemd/system/
    sudo cp /root/amagama-switch/amagama /etc/nginx/sites-enabled/amagama
    sudo systemctl daemon-reload && sudo systemctl restart amagama.uwsgi
    sudo nginx -t && sudo systemctl reload nginx

After a quiet week the old tables (``DROP TABLE public.sources_en,
public.targets_en`` as the read-write user) and /var/www/sites/amagama_fw can
go.


Request limits
--------------

Clients mark lookups made ahead of need with a ``Sec-Purpose`` header
(``prefetch``, or ``prefetch;bulk`` for a whole-file fetch; see the API
docs). nginx limits bulk requests to 1 a second per client address with a
queue of 5, and 2 a second from all clients with a queue of 10. Other API
lookups (ordinary ones and plain prefetches) have their own limit: 5 a second
per client address, with a burst of 50 answered at once. One past a limit
gets a 429 with ``Retry-After: 30``. The rates are in the ``limit_req_zone``
lines, the bursts in the two ``limit_req`` copies (``location /tmserver`` and
``location @amagama``). The access logs end with the header (``-`` when
absent), and ``amagama-manage log_summary`` counts the three kinds::

    sudo /srv/amagama/venv/bin/amagama-manage log_summary /srv/amagama/logs/live-nginx.access.log

Before changing a limit, ``log_summary --rate R --burst B``, over the
rotated logs too, estimates the lookups it would have refused (the logs
time requests to the second, so the count is close, not exact).

After changing the site, ``sudo nginx -t && sudo systemctl reload nginx``.


Scheduled updates
-----------------

``amagama-fetch.timer`` refreshes the fetch cache every day
(``amagama-manage fetch_sources``): each source's languages come due in turn,
so a day's fetch is small. ``amagama-update.timer`` rebuilds and swaps in the
database every Sunday (``amagama-manage update -s en --no-fetch``), from the
cache as the fetch timer left it, without fetching itself; a
rebuild takes hours, so it isn't run daily. They share a lock on the cache:
a fetch that finds a rebuild running skips that day, and a rebuild waits up to
six hours for a fetch to finish, then fails. A run missed while the box was
down happens when it's back.

The update keeps the copy it replaced, so ``swap_db --rollback`` can bring it
back; ``amagama-drop-old.timer`` drops it on Thursday, after the longest
update the unit allows, to keep the database to one copy (and the ``legacy``
snapshot). After that, going back means fixing the cause and rebuilding.

These run as the ``amagama`` user, with settings the web app can't read, since
they need the database user that can write.

After the swap, an update computes the figures of the statistics page
(``/stats/``) into ``/srv/amagama/cache/stats.json``, which the web app reads
when it changes. ``amagama-stats.timer`` adds the lookups per day every night
(``amagama-manage update_stats --only-use``), from the current access log and
the last rotated one, so ``amagama`` must be able to read them (``ls -l
/srv/amagama/logs``). Only totals are kept. To fill the page without waiting
for a rebuild, and to count the older logs once::

    sudo -u amagama env AMAGAMA_CONFIG=/srv/amagama/updater_settings.py \
        /srv/amagama/venv/bin/amagama-manage update_stats \
        --logs '/srv/amagama/logs/live-nginx.access.log*'

That reads the whole database and can take a long while: over 10 minutes for
2,099,307 sources and 36,408,695 targets in 636 languages. With
``--only-use`` it counts only the lookups, without reading the database, and
the translation figures come with the next rebuild.

A box deployed before the statistics page needs its setting and units once,
after the upgrade's pull::

    echo 'STATS_FILE = "/srv/amagama/cache/stats.json"' | \
        sudo tee -a /srv/amagama/local_settings.py
    sudo cp /srv/amagama/amagama/deploy/amagama-stats.service \
        /srv/amagama/amagama/deploy/amagama-stats.timer /etc/systemd/system/
    sudo systemctl daemon-reload && sudo systemctl enable --now amagama-stats.timer
    sudo systemctl restart amagama.uwsgi

A box deployed before retired language codes were read needs their setting
once, after the upgrade's pull::

    echo 'ISO639_RETIREMENTS_FILE = "/srv/amagama/cache/iso-639-3_Retirements.tab"' | \
        sudo tee -a /srv/amagama/local_settings.py
    sudo systemctl restart amagama.uwsgi

The output goes to the journal. A source that fails to fetch is listed by the
fetch (``journalctl -u amagama-fetch``), and that unit is marked failed
(``systemctl --failed``) though the rest was fetched. The update
(``journalctl -u amagama-update``) doesn't fetch, so its unit fails only when
a rebuild stops or the uWSGI reload after it fails.


Upgrading
---------

The files from ``deploy/`` are copies, so a change to one takes effect only
once it is put in place, as the table at the top says, and the copy below
replaces them all. Before the pull, while ``deploy/`` still matches what was
installed, compare them: a difference is a change made on the box, which the
copy would undo, so move it into ``deploy/`` first::

    cd /srv/amagama/amagama/deploy
    for f in amagama*.service amagama*.timer; do diff -u $f /etc/systemd/system/$f; done
    diff -u uwsgi.ini /srv/amagama/uwsgi.ini
    diff -u nginx-amagama.conf /etc/nginx/sites-enabled/amagama
    diff -u logrotate-amagama /etc/logrotate.d/amagama-srv

Then::

    sudo git -C /srv/amagama/amagama pull --ff-only
    sudo /srv/amagama/venv/bin/pip install -r /srv/amagama/amagama/deploy/requirements.txt
    sudo /srv/amagama/venv/bin/python -c "from phply import phpparse; phpparse.make_parser()"
    cd /srv/amagama/amagama/deploy
    sudo install -m 644 uwsgi.ini /srv/amagama/
    sudo install -m 644 amagama*.service amagama*.timer /etc/systemd/system/
    sudo install -m 644 nginx-amagama.conf /etc/nginx/sites-enabled/amagama
    sudo install -m 644 logrotate-amagama /etc/logrotate.d/amagama-srv
    sudo systemctl daemon-reload
    sudo nginx -t && sudo systemctl reload nginx
    sudo systemctl restart amagama.uwsgi

A new timer also needs ``systemctl enable --now``, as the notes above say.
