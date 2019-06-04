.. _api:

amaGama API
***********

.. _api#tm-suggestion-request:

TM suggestion request
=====================

The URL structure for requesting TM suggestions is
``<SERVER>/tmserver/<SOURCE_LANGUAGE>/<TARGET_LANGUAGE>/unit/<QUERY>`` where:

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
   <http://en.wikipedia.org/wiki/List_of_ISO_639-1_codes>`_ and **COUNTRY** a
   country code from `ISO 3166 <http://en.wikipedia.org/wiki/ISO_3166-1>`_. The
   following are valid examples: ar, de, en, en_GB, es_AR, fr, gl, hi, tlh,...
   For the public server the code en is used for the source language for most
   of the FOSS localization files, not en_US.


For example::

    http://amagama.locamotion.org/tmserver/en/af/unit/Computer


.. _api#providing-options:

Providing options
+++++++++++++++++

It is possible to provide some options in the request URL by using a `query
string <http://en.wikipedia.org/wiki/Query_string>`_ with one or more or the
following fields.


+-------------------+-------------------------------------------+
| Option            | Description                               |
+===================+===========================================+
| min_similarity    | The minimum similarity between the string |
|                   | to be searched and the strings to match.  |
|                   | See :ref:`Levenshtein distance            |
|                   | <toolkit:levenshtein_distance>`. Minimum  |
|                   | possible value is 30. Default value is 70.|
+-------------------+-------------------------------------------+
| max_candidates    | The maximum number of results. Default    |
|                   | value is 5.                               |
+-------------------+-------------------------------------------+


For example::

    http://amagama.locamotion.org/tmserver/en/gl/unit/window?min_similarity=31&max_candidates=500


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
| target    | Matching unit's target language test  |
+-----------+---------------------------------------+
| quality   | A Levenshtein distance measure of     |
|           | quality as percent                    |
+-----------+---------------------------------------+
| rank      | ?                                     |
+-----------+---------------------------------------+

An example:

.. code-block:: json

    [
      {
        "source": "Computer",
        "quality": 100.0,
        "target": "Rekenaar",
        "rank": 100.0
      },
      {
        "source": "Computers",
         "quality": 88.888888888888886,
         "target": "Rekenaars",
         "rank": 100.0
       },
       {
         "source": "&Computer",
         "quality": 88.888888888888886,
         "target": "Rekenaar",
         "rank": 100.0
       },
       {
         "source": "_Computer",
         "quality": 88.888888888888886,
         "target": "_Rekenaar",
         "rank": 100.0
       },
       {
         "source": "My Computer",
         "quality": 72.727272727272734,
         "target": "My Rekenaar",
         "rank": 100.0
       }
     ]

