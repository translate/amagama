.. _managing:

Managing amaGama
****************

.. note:: Please make sure that the :command:`amagama-manage` command :ref:`is
   accessible <installation#commands>` in order to be able to use it.

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
<importing>` section for a complete usage example.


.. _managing#deploy-db:

deploy_db
---------

This subcommand is used to optimize the database for deployment. It has no
options:

.. code-block:: bash

    $ amagama-manage deploy_db
    This will permanently alter the database. Continue? [n] y
    Succesfully altered the database for deployment.


.. _managing#dropdb:

dropdb
------

This subcommand is used to drop the tables for one or more source languages
from the amaGama database:

.. code-block:: bash

    $ amagama-manage dropdb -s fr -s de
    This will permanently destroy all data in the configured database. Continue? [n] y
    Succesfully dropped the database for 'fr', 'de'.


.. _managing#initdb:

initdb
------

This subcommand is used to create the tables in the database for one or several
source languages. It can be run several times to specify additional source
languages. The following example creates the tables for english and french:

.. code-block:: bash

    $ amagama-manage initdb -s en -s fr
    Succesfully initialized the database for 'en', 'fr'.


.. _managing#tmdb-stats:

tmdb_stats
----------

This subcommand is used to print out some figures about the amaGama database.
It has no options:

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
