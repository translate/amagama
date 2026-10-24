.. _importing:

Importing translations
**********************

.. note:: Please make sure that amaGama :ref:`is installed
   <installation#requirements>` and that the source language is already
   initialized in the database with the :command:`initdb` command.

To populate the amaGama database the :command:`amagama-manage` command
:command:`build_tmdb` subcommand should be used:

.. code-block:: bash

    $ amagama-manage build_tmdb --verbose -s en -t ar -i foo.po
    Importing foo.po
    Successfully imported foo.po


This will parse :file:`foo.po`, assuming that source language is *English (en)* and
target language is *Arabic (ar)*, and will populate the database accordingly.

The source and target language options only need to be specified if the file
does not provide this information. But if source and target language options
are specified they will override the languages metadata in the translation
file.

All bilingual formats supported by the Translate Toolkit are supported,
including **PO**, **TMX** and **XLIFF**. An XLIFF unit counts as translated
when it is ``approved="yes"`` or its target's state is ``translated``,
``final`` or ``signed-off``.

If a directory is passed to the :option:`-i` option, then its content will be
read recursively:

.. code-block:: bash

    $ amagama-manage build_tmdb --verbose -s en -t gl -i translations/
    Importing translations/foo.po
    Importing translations/bar.po
    Successfully imported translations/


.. _importing#record:

Recording imports
=================

Every imported file is recorded in the ``imports`` table of its source
language: the file name, its SHA-256 checksum (none for a :ref:`snapshot
<managing#load-snapshot>`), the target language, how many
units were loaded and how many were skipped (untranslated or fuzzy), and when.
Name the set of files with ``--block`` and give their licence as an `SPDX
licence expression <https://spdx.org/licenses/>`_ with ``--licence``, such as
``MPL-2.0 OR LGPL-3.0-or-later``. Identifiers must be current ones from the
SPDX License List (``GPL-2.0-or-later``, not the deprecated ``GPL-2.0+``) and
are stored in their normal case; anything else stops the import before
anything is loaded:

.. code-block:: bash

    $ amagama-manage build_tmdb -s en -i gnome-51/ --block gnome-51 \
        --licence GPL-2.0-or-later

:ref:`tmdb_stats <managing#tmdb-stats>` summarises the imports by block.
