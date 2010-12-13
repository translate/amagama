#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2010 Zuza Software Foundation
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

"""PostgreSQL integreation"""

from psycopg2.pool import PersistentConnectionPool
from psycopg2.extras import DictCursor

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
        """return connection to pool on request end"""
        #FIXME: we should have better dirty detection, maybe wrap up insert queries?
        if getattr(g, 'transaction_dirty', False):
            if response.status_code < 400:
                self.connection.commit()
            else:
                self.connection.rollback()
            self.pool.putconn()
        return response

    def bailout(self, app, exception):
        """return connection to pool on request end by unhandled exception"""
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
        """get a thread local database connection object"""
        g.transaction_dirty = True
        return self.pool.getconn()
    connection = property(get_connection)

    def get_cursor(self):
        """get a database cursor object to be used for making queries"""
        #FIXME: maybe use server side cursors?
        return self.connection.cursor(cursor_factory=DictCursor)

    def init_db(self, *args, **kwargs):
        """initialize the database"""
        if not self.INIT_SQL:
            return
        cursor = self.get_cursor()
        cursor.execute(self.INIT_SQL)
        cursor.connection.commit()

    def function_exists(self, function):
        """checks if sql function already exists in database"""
        query = """SELECT EXISTS(SELECT proname FROM pg_proc WHERE proname = %(function)s)"""
        cursor = self.get_cursor()
        cursor.execute(query, {'function': function})
        return cursor.fetchone()[0]

    def table_exists(self, table):
        """checks if table already exists in database"""
        query = """select exists(select relname from pg_class where relname = %(table)s and relkind='r')"""
        cursor = self.get_cursor()
        cursor.execute(query, {'table': table})
        return cursor.fetchone()[0]
