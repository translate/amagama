.. _api:

amaGama API
***********

amaGama answers HTTP requests with JSON. The API lives under ``/api/v1/``;
``/tmserver/`` is the older path to the same read API and still works.


.. _api#tm-suggestion-request:

TM suggestion request
=====================

Request TM suggestions with a ``GET`` of
``<SERVER>/api/v1/<SOURCE_LANGUAGE>/<TARGET_LANGUAGE>/unit/<QUERY>``, where:

+-------------------+---------------------------------------+
| Placeholder       | Description                           |
+===================+=======================================+
| <SERVER>          | The URL of the amaGama server         |
+-------------------+---------------------------------------+
| <SOURCE_LANGUAGE> | Source language code: de, en, en_GB   |
+-------------------+---------------------------------------+
| <TARGET_LANGUAGE> | Target language: ar, es_AR, fr, hi    |
+-------------------+---------------------------------------+
| <QUERY>           | The URL escaped string to be queried  |
+-------------------+---------------------------------------+

.. note:: ``<SOURCE_LANGUAGE>`` and ``<TARGET_LANGUAGE>`` should be language
   codes in the form of **LANG** OR **LANG_COUNTRY**. **LANG**
   should be a language code from `ISO 639
   <https://en.wikipedia.org/wiki/List_of_ISO_639-1_codes>`_ and **COUNTRY** a
   country code from `ISO 3166 <https://en.wikipedia.org/wiki/ISO_3166-1>`_. The
   following are valid examples: ar, de, en, en_GB, es_AR, fr, gl, hi, tlh,...
   For the public server the code en is used for the source language for most
   of the FOSS localization files, not en_US.


For example::

    https://amagama-live.translatehouse.org/api/v1/en/af/unit/Computer

The string can also be passed as ``source`` in the query string, which avoids
problems with ``/`` in the string on some web servers::

    https://amagama-live.translatehouse.org/api/v1/en/af/unit/?source=Open%2FSave


.. _api#providing-options:

Providing options
+++++++++++++++++

It is possible to provide some options in the request URL by using a `query
string <https://en.wikipedia.org/wiki/Query_string>`_ with one or more of the
following fields.

+-------------------+-------------------------------------------+
| Option            | Description                               |
+===================+===========================================+
| min_similarity    | The minimum similarity between the string |
|                   | to be searched and the strings to match.  |
|                   | See :ref:`Levenshtein distance            |
|                   | <toolkit:levenshtein_distance>`. Values   |
|                   | below 70 are raised to 70. Default:       |
|                   | :setting:`MIN_SIMILARITY` (70).           |
+-------------------+-------------------------------------------+
| max_candidates    | The maximum number of results, at most    |
|                   | 30. Default: :setting:`MAX_CANDIDATES`    |
|                   | (5).                                      |
+-------------------+-------------------------------------------+
| style             | The project's checker style, so that      |
|                   | accelerators and similar markup are       |
|                   | matched as that project writes them (``&``|
|                   | for KDE, ``_`` for GNOME). One of:        |
|                   | creativecommons, drupal, gnome, ios, kde, |
|                   | libreoffice, minimal, mozilla, openoffice,|
|                   | reduced, standard, terminology, wx.       |
+-------------------+-------------------------------------------+
| jsoncallback      | Wrap the reply in a call to this          |
|                   | JavaScript function (JSONP).              |
+-------------------+-------------------------------------------+

For example::

    https://amagama-live.translatehouse.org/api/v1/en/af/unit/File?style=gnome&max_candidates=10

A request returns 404 if the source language isn't in the database, if the
source and target languages are the same, or if no string is given. Replies
may be cached for an hour (``Cache-Control: max-age=3600, public``).


.. _api#tm-suggestion-results:

TM suggestion results
=====================

The results from a TM suggestion request are provided in JSON format. It is a
list containing zero or more results. The results contain the following fields:

+-----------+---------------------------------------+
| Field     | Description                           |
+===========+=======================================+
| source    | Matching unit's source language text  |
+-----------+---------------------------------------+
| target    | Matching unit's target language text  |
+-----------+---------------------------------------+
| quality   | A Levenshtein distance measure of     |
|           | quality as percent                    |
+-----------+---------------------------------------+
| rank      | An internal quantity that might       |
|           | disappear. Ignore.                    |
+-----------+---------------------------------------+
| sources   | The :ref:`blocks <sources>` whose     |
|           | imports supplied this translation, in |
|           | alphabetical order. Empty if none was |
|           | recorded.                             |
+-----------+---------------------------------------+

An example:

.. code-block:: json

    [
      {
        "source": "Computer",
        "quality": 100.0,
        "target": "Rekenaar",
        "rank": 100.0,
        "sources": ["gnome-51", "kde"]
      },
      {
        "source": "Computers",
         "quality": 88.888888888888886,
         "target": "Rekenaars",
         "rank": 100.0,
         "sources": ["kde"]
       },
       {
         "source": "&Computer",
         "quality": 88.888888888888886,
         "target": "Rekenaar",
         "rank": 100.0,
         "sources": ["libreoffice-26.8"]
       },
       {
         "source": "_Computer",
         "quality": 88.888888888888886,
         "target": "_Rekenaar",
         "rank": 100.0,
         "sources": ["gnome-51"]
       },
       {
         "source": "My Computer",
         "quality": 72.727272727272734,
         "target": "My Rekenaar",
         "rank": 100.0,
         "sources": ["legacy-2019"]
       }
     ]


.. _api#languages:

Available languages
===================

``GET <SERVER>/api/v1/languages/`` lists the languages in the database:

.. code-block:: json

    {
      "sourceLanguages": ["en"],
      "targetLanguages": ["af", "ar", "de"]
    }


.. _api#write:

Adding translations
===================

When :setting:`ENABLE_DATA_ALTERING_API` is on (it is off by default), the
API also accepts translations. ``style`` can be passed as for lookups.

+--------+-------------------------------------+---------------------------------+
| Method | Path                                | Body                            |
+========+=====================================+=================================+
| PUT or | ``/api/v1/<SRC>/<TGT>/unit/<QUERY>``| JSON object with ``source`` and |
| POST   |                                     | ``target``                      |
+--------+-------------------------------------+---------------------------------+
| POST   | ``/api/v1/<SRC>/<TGT>/store/<NAME>``| JSON list of objects with       |
|        |                                     | ``source`` and ``target``       |
+--------+-------------------------------------+---------------------------------+
| PUT    | ``/api/v1/<SRC>/<TGT>/store/<NAME>``| A translation file; its format  |
|        |                                     | is taken from ``<NAME>``        |
+--------+-------------------------------------+---------------------------------+

The store requests reply ``added <N> units from <NAME>``.
A request with a language code that isn't valid returns 400.
Strings too long for the database's index are skipped.
