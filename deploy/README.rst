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
requirements.txt         /var/www/sites/amagama_fw/env3 (pip freeze)
======================== =============================================

The checkout is /var/www/sites/amagama_fw/amagama.  Settings, including the
database credentials, are in /var/www/sites/amagama_fw/local_settings.py and
are not kept here.
