amaGama documentation
=====================

amaGama is a web service implementing a large-scale translation memory. A
translation memory is a database of previous translations which can be searched
to find good matches to new strings.

amaGama is implemented in Python on top of PostgreSQL. There are currently no
releases of the software, but the `source code is available at Github
<https://github.com/translate/amagama>`_.

A public deployment of amaGama is available, providing both a `public API
<https://amagama-live.translatehouse.org/api/v1/en/af/unit/Computer>`_ and a
`web search <https://amagama-live.translatehouse.org/>`_ interface on top of
the API, and is usable from `Virtaal <http://virtaal.org>`_ and `Pootle
<http://pootle.translatehouse.org>`_.

*amaGama* is the Zulu word for *words*.

.. toctree::
   :maxdepth: 1

   installation
   settings
   managing
   importing
   running
   virtaal_integration

.. include:: contents.rst.inc
