.. _managing:

Managing amaGama
****************

.. note:: The :command:`amagama-manage` command comes with the
   :ref:`installation <installation#requirements>`.

amaGama is managed through the :command:`amagama-manage` command. Try running
it with no arguments for usage help:

.. code-block:: bash

    $ amagama-manage


The :command:`amagama-manage` command exposes several management subcommands,
each having it's own :option:`--help` option that displays its usage
information:

.. code-block:: bash

    $ amagama-manage SUBCOMMAND --help


See below for the available subcommands.


.. _managing#available-subcommands:

Available subcommands
=====================

These are the available management subcommands for amaGama:


.. _managing#benchmark-tmdb:

benchmark_tmdb
--------------

This subcommand benchmarks the application by querying for all strings in the
given file.

.. note:: For more information please check the help of this subcommand.


.. _managing#build-tmdb:

build_tmdb
----------

This subcommand is used to import translations into amaGama from bilingual
translation files. Please refer to the :ref:`importing translations
<importing>` section for a complete usage example. With ``--build`` it imports
into the build schema (see :ref:`swap_db <managing#swap-db>`).


.. _managing#deploy-db:

deploy_db
---------

This subcommand is used to optimize the database for deployment, or with
``--build`` the build schemas:

.. code-block:: bash

    $ amagama-manage deploy_db
    This will permanently alter the database. Continue? [n] y
    Successfully altered the database for deployment.

.. note:: This is an irreversible step that alters the database permanently.

.. _managing#dropdb:

dropdb
------

This subcommand is used to drop the tables for one or more source languages
from the amaGama database:

.. code-block:: bash

    $ amagama-manage dropdb -s fr -s de
    This will permanently destroy all data in the configured database. Continue? [n] y
    Successfully dropped the database for 'fr', 'de'.

With ``--build`` it drops the build schemas instead, and with ``--old`` the
copies that :ref:`swap_db <managing#swap-db>` replaced.


.. _managing#initdb:

initdb
------

This subcommand is used to create the tables in the database for one or several
source languages. It can be run several times to specify additional source
languages. The following example creates the tables for English and French:

.. code-block:: bash

    $ amagama-manage initdb -s en -s fr
    Successfully initialized the database for 'en', 'fr'.

With ``--build`` it creates the build schemas instead.


.. _managing#migrate-db:

migrate_db
----------

This subcommand copies a database made by older amaGama releases, which keep
each source language in ``public.sources_<lang>`` and ``public.targets_<lang>``
tables, into the per-language schemas current amaGama uses. It has no options:

.. code-block:: bash

    $ amagama-manage migrate_db
    Copied 'en': 782372 sources, 21101176 targets.
    The old tables are unchanged. Run deploy_db to build the search indexes.

Each source language is copied in one transaction, and a language that is
already migrated is skipped, so the command can be run again. The old tables
are left in place, so the old code keeps working until you switch over; drop
them once the new version is in use. Run :ref:`deploy_db <managing#deploy-db>`
afterwards.


.. _managing#repair-mojibake:

repair_mojibake
---------------

This subcommand finds text that was UTF-8 decoded as Latin-1 or Windows-1252
before import (``LÃªer`` for ``Lêer``) and converts it back. Without options
it only reports what it would fix:

.. code-block:: bash

    $ amagama-manage repair_mojibake
    Would fix 'en': 4 sources, 1420 targets.
    Nothing was changed. Run with --apply to write the fixes.

With ``--apply`` it writes the fixes, one transaction per source
language: a broken target beside a correct copy is removed, other broken
targets and sources are replaced, and a source whose fixed text already exists
is merged into it. Text is only changed when all of it converts, and text that
looks mangled but doesn't convert is listed and left alone.


.. _managing#swap-db:

swap_db
-------

This subcommand replaces a source language's data with a rebuilt copy. Build
the copy with ``--build`` on :ref:`initdb <managing#initdb>`,
:ref:`build_tmdb <managing#build-tmdb>` and :ref:`deploy_db
<managing#deploy-db>`: it goes into the ``_build_<lang>`` schema, which isn't
served. Then swap it in:

.. code-block:: bash

    $ amagama-manage initdb -s en --build
    $ amagama-manage build_tmdb -s en -t af -i af.po --build
    $ amagama-manage deploy_db --build
    $ amagama-manage swap_db -s en
    Swapped in 'en'; the replaced copy is _old_en.

The live and build schemas are renamed in one transaction, so lookups switch
to the new data at once, without a restart. The replaced data stays in
``_old_<lang>``: ``swap_db --rollback`` puts it back, and ``dropdb --old``
removes it. The language list served by the API refreshes when amaGama is
restarted.


.. _managing#tmdb-stats:

tmdb_stats
----------

This subcommand is used to print out some figures about the amaGama database,
or with ``--build`` the build schemas:

.. code-block:: bash

    $ amagama-manage tmdb_stats
    Complete database (amagama):	400 MB
    Complete size of sources_en:	234 MB
    Complete size of targets_en:	160 MB
    sources_en (table only):	85 MB
    targets_en (table only):	66 MB
    sources_en	sources_en_text_idx	83 MB
    targets_en	targets_en_unique_idx	79 MB
    sources_en	sources_en_text_unique_idx	53 MB
    targets_en	targets_en_pkey	16 MB
    sources_en	sources_en_pkey	13 MB
