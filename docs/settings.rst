.. _settings:

amaGama settings
****************

amaGama has some settings to allow tuning its behaviour. Below you can see
a detailed description for each setting and its default values.

amaGama settings are stored in :file:`amagama/settings.py`.

Alternatively you can specify settings in a separate file to overeride the
built-in settings in :file:`amagama/settings.py`. This avoids having to edit a
file that is tracked in the version control system. To use these settings, set
the environment variable `AMAGAMA_CONFIG` to the full path of your file. This
should be used as overrides to the default settings, so whatever is specified
in this file will take precedence over the file :file:`settings.py`.


.. _settings#global-settings:

Global settings
===============

Settings to define amaGama server behavior.


.. setting:: DEBUG

``DEBUG``
  Default: ``False``

  Indicates if debug mode is enabled.


.. setting:: SECRET_KEY

``SECRET_KEY``
  Default: ``foobar``

  The secret key to use for keeping the sessions secure. Choose a long random
  string and keep this secret.


.. setting:: ENABLE_WEB_UI

``ENABLE_WEB_UI``
  Default: ``False``

  Enables the web interface.


.. setting:: ENABLE_DATA_ALTERING_API

``ENABLE_DATA_ALTERING_API``
  Default: ``False``

  Enables the operations of the amaGama API that alters data. If set to
  ``False``, the system can be considered read-only.

  This doesn't affect to the part of the API that is used to perform queries
  that don't alter the data. For example retrieving translations is always
  enabled.


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

  Maximum number of connections in the database pool.


.. setting:: DB_MIN_CONNECTIONS

``DB_MIN_CONNECTIONS``
  Default: ``2``

  Minimum number of connections in the database pool.


.. _settings#levenshtein-settings:

Levenshtein settings
====================

Settings for Levenshtein algorithm. See :ref:`Levenshtein distance
<toolkit:levenshtein_distance>` for more information.


.. setting:: MAX_CANDIDATES

``MAX_CANDIDATES``
  Default: ``5``

  The maximum number of results returned. This can be overridden by providing
  another value using a :ref:`query string <api#providing-options>`.


.. setting:: MAX_LENGTH

``MAX_LENGTH``
  Default: ``2000``

  Maximum length of source strings. If a string is longer, then it won't be
  matched or returned in the results. This setting is also used during import
  to filter out source strings. It is therefore not very meaningful to increase
  this after importing translations. Note that a value above 3000 characters
  (or even less for CJK languages) might give problems, and is less likely to
  be useful to users. It is after all about a whole page of text in one string!


.. setting:: MIN_SIMILARITY

``MIN_SIMILARITY``
  Default: ``70``

  The minimum similarity between the query string and the candidate strings.

  This can be overridden by providing another value using a :ref:`query string
  <api#providing-options>`, but there is a hardcoded minimum possible value of
  ``30``. If a lower value is provided then ``30`` will be used.
