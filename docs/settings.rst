.. _settings:

amaGama settings
****************

amaGama has some settings that allow to tune how it behaves. Below you can see
a detailed description for each setting and its default values.

amaGama settings are stored in :file:`amagama/settings.py`.


.. _settings#global-settings:

Global settings
===============

Settings to define amaGama server behavior.


.. setting:: DEBUG

``DEBUG``
  Default: ``False``

  Indicates if the debug mode is enabled.


.. setting:: ENABLE_WEB_UI

``ENABLE_WEB_UI``
  Default: ``False``

  Indicates if the web interface is enabled.


.. _settings#database-settings:

Database settings
=================

Settings used for connecting to the amaGama database.


.. setting:: DB_HOST

``DB_HOST``
  Default: ``"localhost"``

  Hostname of the server where the amaGama database is located.


.. setting:: DB_NAME

``DB_NAME``
  Default: ``"amagama"``

  amaGama database name.


.. setting:: DB_PASSWORD

``DB_PASSWORD``
  Default: ``""``

  Password for the amaGama database user.


.. setting:: DB_PORT

``DB_PORT``
  Default: ``"5432"``

  Port number where the database server holding the amaGama database is
  listening.


.. setting:: DB_USER

``DB_USER``
  Default: ``"postgres"``

  User name for connecting to the amaGama database.


.. _settings#database-pool-settings:

Database pool settings
======================

Settings for the database pool.

.. setting:: DB_MAX_CONNECTIONS

``DB_MAX_CONNECTIONS``
  Default: ``20``

  Maximum number of connections that the pool database will handle.


.. setting:: DB_MIN_CONNECTIONS

``DB_MIN_CONNECTIONS``
  Default: ``2``

  Number of connections to the database server that are created automatically
  in the database pool.


.. _settings#levenshtein-settings:

Levenshtein settings
====================

Settings for Levenshtein algoritm. See :ref:`Levenshtein distance
<toolkit:levenshtein_distance>` for more information.


.. setting:: MAX_CANDIDATES

``MAX_CANDIDATES``
  Default: ``5``

  The maximum number of results returned. This can be overridden by providing
  another value using a :ref:`query string <api#providing-options>`.


.. setting:: MAX_LENGTH

``MAX_LENGTH``
  Default: ``1000``

  Maximum length of the string. If the string length is higher then it won't be
  matched neither returned in the results.


.. setting:: MIN_SIMILARITY

``MIN_SIMILARITY``
  Default: ``70``

  The minimum similarity between the string to be searched and the strings to
  match.

  This can be overridden by providing another value using a :ref:`query string
  <api#providing-options>`, but there is a hardcoded minimum possible value of
  ``30``. If a lower value is provided then ``30`` will be used.
