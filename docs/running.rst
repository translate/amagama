.. _running:

Running amaGama
***************

.. note:: The :command:`amagama` command comes with the :ref:`installation
   <installation#requirements>`.

The :command:`amagama` command will try to use the best pure Python WSGI server
to launch amaGama server listening on port ``8888``.

.. code-block:: bash

    $ amagama


After launching the server you can test that amaGama is working by visiting
http://localhost:8888/tmserver/en/ar/unit/file which should display a JSON
representation of the *Arabic* translations for the *English* word **file**.

.. note:: For more options check:

   .. code-block:: bash

        $ amagama --help
