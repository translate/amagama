#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2010-2011 Zuza Software Foundation
#
# This file is part of translate.
#
# translate is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# translate is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with translate; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA

"""PostgreSQL access and helpers."""

from psycopg2.pool import PersistentConnectionPool
from psycopg2.extras import DictCursor
from psycopg2 import IntegrityError


# setup unicode
import psycopg2.extensions
psycopg2.extensions.register_type(psycopg2.extensions.UNICODE)
psycopg2.extensions.register_type(psycopg2.extensions.UNICODEARRAY)

from flask import g, got_request_exception

class PostGres(object):
    INIT_SQL = None

    def __init__(self, app=None):
        self.app = None
        self.pool = None

        if app:
            self.init_app(app)

    def cleanup(self, response):
        """Return connection to pool on request end."""
        #FIXME: we should have better dirty detection, maybe wrap up insert queries?
        if getattr(g, 'transaction_dirty', False):
            if response.status_code < 400:
                self.connection.commit()
            else:
                self.connection.rollback()
            self.pool.putconn()
        return response

    def bailout(self, app, exception):
        """Return connection to pool on request ended by unhandled exception."""
        if app.debug and getattr(g, 'transaction_dirty', False):
            self.connection.rollback()
            self.pool.putconn()

    def init_app(self, app):
        self.app = app
        # read config
        db_args = {'minconn': app.config.get('DB_MIN_CONNECTIONS', 2),
                   'maxconn': app.config.get('DB_MAX_CONNECTIONS', 20),
                   'database': app.config.get('DB_NAME'),
                   'user': app.config.get('DB_USER'),
                   'password': app.config.get('DB_PASSWORD', ''),
                   }
        if 'DB_HOST' in app.config:
            db_args['host'] = app.config.get('DB_HOST')
        if 'DB_PORT' in app.config:
            db_args['port'] = app.config.get('DB_PORT')

        self.pool = PersistentConnectionPool(**db_args)

        app.after_request(self.cleanup)

        got_request_exception.connect(self.bailout, app)

    def get_connection(self):
        """Get a thread local database connection object."""
        #FIXME: this is dirty can we detect when in request context?
        try:
            g.transaction_dirty = True
        except:
            # using connection outside request context
            pass

        return self.pool.getconn()
    connection = property(get_connection)

    def get_cursor(self):
        """Get a database cursor object to be used for making queries."""
        #FIXME: maybe use server side cursors?
        return self.connection.cursor(cursor_factory=DictCursor)

    def init_db(self, *args, **kwargs):
        """Initialize the database."""
        if not self.INIT_SQL:
            return
        cursor = self.get_cursor()
        cursor.execute(self.INIT_SQL)
        cursor.connection.commit()

    def function_exists(self, function):
        """Check if the SQL function already exists in the database."""
        query = """SELECT EXISTS(SELECT proname FROM pg_proc WHERE proname = %(function)s)"""
        cursor = self.get_cursor()
        cursor.execute(query, {'function': function})
        return cursor.fetchone()[0]

    def table_exists(self, table):
        """Check if table already exists in the database."""
        query = """SELECT EXISTS(SELECT relname FROM pg_class WHERE relname = %(table)s and relkind='r')"""
        cursor = self.get_cursor()
        cursor.execute(query, {'table': table})
        return cursor.fetchone()[0]

    def drop_table(self, table):
        """Drop the table if it exists."""
        query = """DROP TABLE IF EXISTS %s CASCADE;""" % table
        cursor = self.get_cursor()
        cursor.execute(query)
        cursor.connection.commit()
