amaGama: a web translation memory server
========================================

Status
------
At this stage amaGama is just a prototype, it started as a port of
Translate Toolkit's tmserver to PostgreSQL.


Dependencies
------------
* PostgreSQL (in theory works with 8.3, tested on 8.4)
* Psycopg2
* Flask (0.6.1 or below), Flask-Script, Flask-WTF
* blinker
* WSGI webserver (amagama will try cherrypy, werkzeug or python's
  wsgiref in that order)
* Translate Toolkit (1.8.0+)
* python-Levenshtein (for better performance)


Installation
------------
AmaGama requires a postgresql database to store translations. Create
an empty database, then edit the database connection configuration in
``amagama/settings.py``.

A way to create the amagama database::

    $ su root
    # su postgres
    $ createdb -E UTF-8 amagama

You might see an error such as this one::

    createdb: database creation failed: ERROR: new encoding (UTF8) is
    incompatible with the encoding of the template database (SQL_ASCII)
    
This could happen because the database was installed in the "C" locale. This
might be fixed by doing the following::

    $ createdb -E UTF-8 -T template0 amagama

AmaGama is managed through the ``amagama-manage`` command. Try running it
with no arguments for usage help. Each command has it's own ``--help``
usage information. Try ::

    $ amagama-manage initdb --help

The first step after editing ``settings.py`` is to prepare database tables for
each source language you will use (you can add more languages later)::

    $ amagama-manage initdb -s en -s fr

Importing Translations
----------------------
To populate the amaGama database we use the ``amagama-manage``
command ``build_tmdb``::

    $ amagama-manage build_tmdb -s en -t ar -i foo.po

This will parse ``foo.po``, assume source language is English (en) and target
language is Arabic (ar) and populate the database accordingly.

The source and target language options only need to be specified if the
file does not provide this information. They override file metadata if
specified.

All bilingual formats supported by the Translate Toolkit are supported. If a
directory is passed to ``-i`` its content will be read recursively.


Running
-------
The command ``amagama`` will try to use the best pure python wsgi server
to launch amagama server listening on port 8888. Try ``amagama --help``
for more options.

After running you can test operation by visiting
<http://localhost/tmserver/en/ar/unit/file>.

This should display a JSON representation of the english->arabic
translations for "file".


Integrating with Virtaal
------------------------
AmaGama implements the same protocol as tmserver, and can be used with
Virtaal's remotetm plugin.

In Virtaal go to *edit->preferences->plugins->Translation Memory->configure*
to make sure the remote server plugin is enabled and then close Virtaal.

Edit ``~/.virtaal/tm.ini`` and make sure there is a remotetm section that
looks like this::

    [remotetm]
    host = localhost
    port = 8888

Run Virtaal again. You should start seeing results from amaGama (they
will be marked as coming from remotetm).


TODO
----
* simple web interface
* custom index config for source languages not supported by default postgres install
* keep track of file's mtime to avoid expensive reparses
* use memcached to cache results
* use more permanent caching of levenshtein distances?
* use postgres built in levenshtein functions?
* integrate with Pootle
* document API
