.. _virtaal_integration:

Integrating amaGama with Virtaal
********************************

Virtaal has a plugin for the public amaGama server since version **0.7** and it
is enabled by default. No configuration is needed for the public server.

amaGama implements the same protocol as *tmserver*, and can be used with
Virtaal's *remotetm* plugin, or other software that supports this. This is
useful for using alternative amaGama servers.

In Virtaal go to :menuselection:`Edit --> Preferences --> Plugins -->
Translation Memory --> Configure` to make sure the remote server plugin is
enabled and then close Virtaal.

Edit :file:`~/.virtaal/tm.ini` and make sure there is a ``remotetm`` section
that looks like this:

.. code-block:: ini

    [remotetm]
    host = localhost
    port = 8888

.. note:: If you are going to use a remote amaGama server this setting needs to
   be changed accordingly.

Run Virtaal again. You should start seeing results from amaGama (they will be
marked as coming from *remotetm*).
