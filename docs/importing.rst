.. _importing:

Importing translations
**********************

To populate the amaGama database the :command:`amagama-manage` command
:command:`build_tmdb` subcommand should be used:

.. code-block:: bash

    $ amagama-manage build_tmdb -s en -t ar -i foo.po


This will parse ``foo.po``, assuming that source language is *English (en)* and
target language is *Arabic (ar)*, and will populate the database accordingly.

The source and target language options only need to be specified if the file
does not provide this information. But if source and target language options are
specified they will override the languages metadata in the translation file.

All bilingual formats supported by the Translate Toolkit are supported,
including PO, TMX and XLIFF.

If a directory is passed to the ``-i`` option, then its content will be read
recursively.
