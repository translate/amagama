
.. _pages/amagama/api#amagama_api:

Amagama API
***********

.. _pages/amagama/api#tm_suggestion_request:

TM suggestion request
=====================

Request possible TM matches from the server.

Example:

  http://amagama.locamotion.org/tmserver/en/af/unit/Computer

Construction:

  $(server)/tmserver/$(source_language)/$(target_language)/unit/$(query)

Where:

| server                  | The URL of the Amagama server  |
| source_language  | Source language in Gettext format i.e. ISO639_ISO3166 e.g. en, en_GB, fr  |
| target_language   | Target language e.g. ar, hi  |
| query                   | The URL escaped string to be queried  |

.. _pages/amagama/api#tm_suggestion_results:

TM suggestion results
---------------------

The results from a TM suggestion request are provided in JSON format.  It is a list containing zero, 1 or multiple results. The results contain the following fields:

| source  | Matching unit's source language text  |
| target   | Matching unit's target language test   |
| quality  | A Levenshtein distance measure of quality as percent  |
| rank     | ?  |

An example:

::

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
         "source": 
         "My Computer", 
         "quality": 72.727272727272734, 
         "target": "My Rekenaar", 
         "rank": 100.0
       }
     ]

