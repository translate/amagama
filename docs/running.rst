.. _running:

Running amaGama
***************

The command :command:`amagama` will try to use the best pure Python WSGI server
to launch amaGama server listening on port ``8888``. Try
:command:`amagama --help` for more options.

After running you can test that amaGama is working by visiting
http://localhost:8888/tmserver/en/ar/unit/file which should display a JSON
representation of the Arabic translations for the English *file* word.
