.. _contributing:

Contributing
************

We accept code contributions to amaGama, please use GitHub pull requests for
your changes.


Preparations
------------

You will need a local working copy of amaGama. The best way to achieve that is
to follow the :ref:`installation guidelines <installation>`.


Coding style
------------

Please follow the :ref:`Translate Toolkit Style Guide <toolkit:styleguide>`.

Every commit is checked by the hooks in ``.pre-commit-config.yaml``: ruff,
codespell, the copyright header, whitespace and file checks, the GitHub
workflow files (zizmor and their schema) and the commit message (gitlint). Install them once per checkout
with `prek <https://github.com/j178/prek>`_ (installed by
``requirements/dev.txt``; `pre-commit <https://pre-commit.com>`_ reads the same
config); CI runs the same hooks:

.. code-block:: bash

    $ prek install

To check the whole tree:

.. code-block:: bash

    $ prek run --all-files

Every Python file and ``bin/`` script starts with the copyright header in
``.license.header.txt``; ``AUTHORS.md`` lists who has contributed. Add
yourself there with your first change.

Running the tests
-----------------

The tests start their own PostgreSQL server with pytest-postgresql, which needs
PostgreSQL's server binaries installed. Without them, run the tests in Docker
against your checkout:

.. code-block:: bash

    $ docker compose run --rm test


Fuzzing
-------

The fuzz targets in ``fuzz/`` feed generated input to the code that reads
untrusted text: the file readers the updater uses, language codes, a lookup's
options and text, and the language negotiation. They run with `Atheris
<https://github.com/google/atheris>`_ under `ClusterFuzzLite
<https://google.github.io/clusterfuzzlite/>`_: for a few minutes on each pull
request, and for an hour every week on main. Each target's seed inputs are in
``fuzz/corpus/``; the tests run every target over them.

``amagama/tests/test_api_fuzz.py`` sends generated requests to the API and the
web pages. The tests run it with the same examples every time; to run many
more, new ones each time:

.. code-block:: bash

    $ AMAGAMA_FUZZ=1 pytest amagama/tests/test_api_fuzz.py

A problem fuzzing finds gets an issue. Until it is fixed, the target or test
lets that input through, with the issue number beside it.


Planned work
------------

Planned work and known problems are tracked in the `issue tracker
<https://github.com/translate/amagama/issues>`_.
