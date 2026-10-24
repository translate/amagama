.. _installation:

Installing amaGama
******************

Want to try amaGama? This will guide you through installing amaGama and its
requirements.


.. _installation#dependencies:

Dependencies
============

amaGama requires:

- **Python** 3.11 or later.
- **PostgreSQL**. amaGama is tested with PostgreSQL 16.
- **git**, to get amaGama.

Consult the specifics for your operating system in order to get each of these
installed.


.. _installation#getting-amagama:

Getting amaGama
===============

Get the source from git:

.. code-block:: bash

   $ git clone https://github.com/translate/amagama.git
   $ cd amagama


.. _installation#setup-virtualenv:

Setting up a virtual environment
================================

A virtual environment keeps amaGama's requirements apart from system-wide
packages. Create and activate one:

.. code-block:: bash

   $ python3 -m venv .venv
   $ source .venv/bin/activate


.. _installation#requirements:

Installing amaGama
==================

Install amaGama with its recommended extras (a standalone web server, faster
string comparison and the management commands):

.. code-block:: bash

   (.venv) $ pip install -e '.[recommended]'

This puts the :command:`amagama` and :command:`amagama-manage` commands on
your path. For development, install ``'.[dev]'`` instead, which adds the test
and documentation tools.


.. _installation#creating-database:

Creating the database
=====================

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
======================

The next step is to adjust amaGama settings to include the right database
connection configuration, and perhaps change any other setting. Check the
:ref:`amaGama settings documentation <settings>` in order to know how to do it.

.. note:: One simple change that you should most likely make on a toy
   installation is to set:

   .. code-block:: python

      DB_HOST = "localhost"

   This is a side effect of how PostgreSQL is installed on Ubuntu and other
   systems.


.. _installation#preparing-database:

Preparing the database
======================

The first step after editing the settings is to prepare database tables for
each source language you will use (you can add more languages later):

.. code-block:: bash

    $ amagama-manage initdb -s en -s fr


.. _installation#docker:

Running under Docker
====================

As an alternative to the steps above, ``docker-compose.yml`` runs amaGama and
PostgreSQL together (``docker-compose`` works the same as ``docker compose``):

.. code-block:: bash

    $ docker compose up -d --build
    $ docker compose exec app amagama-manage initdb -s en
    $ docker compose restart app

amaGama is then at http://localhost:8888/. The image reads its settings from
environment variables (see ``docker/settings.py``): ``DB_HOST``, ``DB_PORT``,
``DB_NAME``, ``DB_USER``, ``DB_PASSWORD``, ``AMAGAMA_SECRET_KEY``,
``AMAGAMA_ENABLE_WEB_UI`` and ``AMAGAMA_ENABLE_DATA_ALTERING_API``.

.. note:: amaGama reads the list of source languages when it starts, so
   restart the ``app`` container after ``initdb`` adds one.

Run other management commands the same way, for example:

.. code-block:: bash

    $ docker compose exec app amagama-manage tmdb_stats


.. _installation#next-steps:

Next steps
==========

Now that you have managed to install amaGama you will probably want to know how
to:

- :ref:`Manage amaGama <managing>`
- :ref:`Import translations <importing>` into amaGama
- :ref:`Run amaGama <running>`
