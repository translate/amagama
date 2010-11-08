amaGama: a web translation memory server
========================================

Status
------

At this stage amaGama is just a prototype, it started as a port of
Translate Toolkit's tmserver to PostgreSQL


Dependencies
------------

* PostgreSQL (in theory works with 8.3, tested on 8.4)
* Psycopg2
* Flask, flask-sript, flask-wtf
* WSGI webserver (amagama will try cherrypy, werkzeug or python's
  wsgiref in that order)
* Translate Toolkit (1.8.0+)
* python-Levenshtien (for better performance)


Installation
------------

amaGama requires a postgresql database to store translations, create
an empty database then edit the database connection configuration in
amagama/settings.py

amaGama is managed through the amagama-manage command, try running it
with no arguments for usage help. each command has it's own --help
usage information. try

$ amagama-manage initdb --help

first step after editing settings.py is to prepare database tables for
each source language you will use (you can add more languages later)

$ amagama-manage initdb -s en -s fr

Importing Translations
----------------------

To populate the amaGama database we use the amagama-manage command build_tmdb

$ amagama-manage build_tmdb -s en -t ar -i foo.po

this will parse foo.po, assume source language is en and target
language is ar and populate the database accordingly.

the source and target language options are only used if the file does
not provide this information. they do not override file metadata.

all transalte toolkit bilingual formats are supported, if a directory
is passed to -i it's content will be read recursively.


Running
-------

The command amagama will try to use the best pure python wsgi server
to launch amagama server listening on port 8888. try amagama --help
for more options.

after running you can test operation by visiting

http://localhost/tmserver/en/ar/unit/file

this should display a json representation of the english->arabic
translations for "file"


Integrating with Virtaal
------------------------

amaGama implements the same protocol as tmserver, and can be used with
Virtaal's remotetm plugin.

in Virtaal under edit->preferences->plugins->Translation Memory->configure
make sure the remote server plugin is enabled then close virtaal.

edit ~/.virtaal/tm.ini and make sure there is a remotetm section that
looks like this

[remotetm]
host = localhost
port = 8888

run Virtaal again you should start seeing results from amaGama (they
will be marked as coming from remotetm)


TODO
----

* simple web interface
* custom index config for source languages not supported by default postgres install
* keep track of file's mtime to avoid expensive reparses
* use memcached to cache results
* use more permemnant caching of levenshtein distances?
* use postgres built in levenshtein functions?
* integrate with Pootle
* document API
