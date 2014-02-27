.. _installation:

Installing amaGama
******************

Want to try amaGama? This guide will guide you through installing amaGama and
its requirements.


.. _installation#dependencies:

Dependencies
============

amaGama requires the following dependencies:

- **Python 2**: 2.6 or later.
- **PostgreSQL**: Tested on 8.3 and 8.4.
- **Psycopg2**
- **Flask**: 0.7 or later.
- **blinker**
- **WSGI webserver**: amaGama will try *cherrypy*, *werkzeug* or python's *wsgiref*
  in that order, unless running under *apache*, *nginx*, etc.
- **Translate Toolkit**: 1.8.0 or later.


.. _installation#optional-dependencies:

Optional dependencies
---------------------

It also has some optional dependencies:

- For management commands:

  - **Flask-Script**

- For the web UI:

  - **Flask-WTF**

- For better performance:

  - **python-Levenshtein**
  - **psyco** (if available for your platform)


.. _installation#installation:

Installation
============

After installing the amaGama dependencies, you can safely start amaGama
installation.


.. _installation#creating-database:

Creating the database
---------------------

amaGama requires a PostgreSQL database to store translations. So create an empty
database, for example doing the following:

.. code-block:: bash

    $ su root
    # su postgres
    $ createdb -E UTF-8 amagama

.. note:: You might see an error like:

   .. code-block:: bash

      createdb: database creation failed: ERROR: new encoding (UTF8) is
      incompatible with the encoding of the template database (SQL_ASCII)

   This could happen because the database was installed in the *"C"* locale. This
   might be fixed by doing the following:

   .. code-block:: bash

      $ createdb -E UTF-8 -T template0 amagama


.. _installation#adjust-settings:

Adjusting the settings
----------------------

The next step is to adjust amaGama settings to include the right database
connection configuration, and perhaps change any other setting. Check the
:ref:`amaGama settings documentation <settings>` in order to know how to do it.


.. _installation#preparing-database:

Preparing the database
----------------------

The first step after editing the settings is to prepare database tables for each
source language you will use (you can add more languages later):

.. code-block:: bash

    $ amagama-manage initdb -s en -s fr


.. _installation#next-steps:

Next steps
----------

Now that you have managed to install amaGama you will probably want to know how
to:

- :ref:`Manage amaGama <managing>`
- :ref:`Import translations <importing>` to amaGama
- :ref:`Run amaGama <running>`
