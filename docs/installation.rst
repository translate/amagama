.. _installation:

Installing amaGama
******************

Want to try amaGama? This will guide you through installing amaGama and its
requirements.


.. _installation#dependencies:

Dependencies
============

amaGama requires the following dependencies:

- **Python**: 2.7 or later. Python 3 is also supported.
- **PostgreSQL**: 8.4 or later.

There are also some dependencies that we strongly recommend to use, but are optional:

- **git**: Necessary to get amaGama.
- **virtualenv**: Provides an isolated environment or virtualenv.
- **virtualenvwrapper**: To ease handling virtualenvs.

Consult the specifics for your operating system in order to get each above
package installed successfully.


.. _installation#setup-virtualenv:

Setting up a virtualenv
=======================

The use of virtualenvs allows to install all the requirements at specific
versions without interfering with system-wide packages. To create a virtualenv
just run:

.. code-block:: bash

   $ mkvirtualenv amagama


.. _installation#getting-amagama:

Getting amaGama
===============

There is no package for amaGama, so you will need to run it from a git
checkout:

.. code-block:: bash

   (amagama) $ git clone https://github.com/translate/amagama.git
   (amagama) $ cd amagama


.. _installation#requirements:

Installing the requirements
===========================

Then install the requirements:

.. code-block:: bash

   (amagama) $ pip install -r requirements/recommended.txt


After installing the amaGama requirements, you can safely start amaGama
installation.


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


.. _installation#commands:

Making the commands accessible
==============================

Since amaGama is not installed we need to make its commands accessible:

.. code-block:: bash

   $ export PATH=$(pwd)/bin:$PATH
   $ export PYTHONPATH=$(pwd):$PYTHONPATH


.. _installation#preparing-database:

Preparing the database
======================

The first step after editing the settings is to prepare database tables for
each source language you will use (you can add more languages later):

.. code-block:: bash

    $ amagama-manage initdb -s en -s fr


.. _installation#next-steps:

Next steps
==========

Now that you have managed to install amaGama you will probably want to know how
to:

- :ref:`Manage amaGama <managing>`
- :ref:`Import translations <importing>` into amaGama
- :ref:`Run amaGama <running>`
