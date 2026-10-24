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
codespell, whitespace and file checks, the GitHub workflow files (zizmor and
their schema) and the commit message (gitlint). Install them once per checkout
with `prek <https://github.com/j178/prek>`_ (installed by
``requirements/dev.txt``; `pre-commit <https://pre-commit.com>`_ reads the same
config); CI runs the same hooks:

.. code-block:: bash

    $ prek install

To check the whole tree:

.. code-block:: bash

    $ prek run --all-files

Running the tests
-----------------

The tests start their own PostgreSQL server with pytest-postgresql, which needs
PostgreSQL's server binaries installed. Without them, run the tests in Docker
against your checkout:

.. code-block:: bash

    $ docker compose run --rm test


TODO
----

An incomplete list of possible TODO items:

- Improve web interface
- Custom index config for source languages not supported by default PostgreSQL
  install
- Keep track of file's mtime to avoid expensive reparses
- Use memcached to cache results
- Use more permanent caching of Levenshtein distances?
- Use PostgreSQL built-in Levenshtein functions?
- Full text search
- Other search methods and options
- Further documenting of API
- Document the commands
- Document how to deploy amaGama using Apache or other web server
