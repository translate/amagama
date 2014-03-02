.. _installation:

Installing amaGama
******************

Want to try amaGama? This will guide you through installing amaGama and its
requirements.


.. _installation#dependencies:

Dependencies
============

amaGama requires the following dependencies:

- **Python 2**: 2.6 or later.
- **PostgreSQL**: Tested on 8.3 and 8.4.
- **git**: Necessary to get amaGama.
- **virtualenv**: We strongly recommend its use.
- **virtualenvwrapper**: To ease handling virtualenvs.

Consult the specifics for your operating system in order to get each above
package installed successfully.


.. _installation#getting-amagama:

Getting amaGama
===============

There is no package for amaGama, so you will need to run it from a git
checkout:

.. code-block:: bash

   $ git clone https://github.com/translate/amagama.git


.. _installation#dependencies:

Dependencies
============

We recommend that you use a virtualenv and virtualenv-wrappers to create a
virtual environment:

.. code-block:: bash

   $ mkvirtualenv amagama


Then install the dependencies:

.. code-block:: bash

   (amagama) $ pip install -r requirements/recommended.txt


After installing the amaGama dependencies, you can safely start amaGama
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

   This is a side effect of how Postgres is installed on Ubuntu and other
   systems.


.. _installation#commands:

Making the commands accessible
==============================

Since amaGama is not installed we need to make accessible its commands:

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
- :ref:`Import translations <importing>` to amaGama
- :ref:`Run amaGama <running>`
