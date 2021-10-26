#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# import sys
import time
import sqlite3
# from faker import Faker


class Elapse_time(object):
    '''耗时统计工具'''

    def __init__(self, prompt=''):
        self.prompt = prompt
        self.start = time.time()

    def __del__(self):
        print('%s耗时: %.3f' % (self.prompt, time.time() - self.start))


CElapseTime = Elapse_time

filename = 'without-char.db'


def prepare(isolation_level=''):
    connection = sqlite3.connect(filename, isolation_level=isolation_level)
    connection.execute("PRAGMA page_size = 4096")
    connection.execute("PRAGMA cache_size = 2000")
    connection.execute("PRAGMA journal_mode = wal")
    connection.execute("PRAGMA wal_autocheckpoint = 3000")
    connection.execute("PRAGMA cache_spill = false")
    connection.execute("PRAGMA automatic_index = false")
    connection.execute("PRAGMA synchronous = normal")
    connection.execute("DROP TABLE IF EXISTS people")
    connection.execute(
        "create table IF NOT EXISTS people(seq text PRIMARY KEY, srvid text, sid text, borntime text) WITHOUT ROWID")
    # connection.execute('CREATE UNIQUE INDEX idx_srvid ON people(srvid)')
    # connection.execute('CREATE INDEX idx_col ON people(sid,borntime)')
    # connection.execute('CREATE INDEX idx_col2 ON people(borntime)')
    # connection.execute('CREATE INDEX idx_col ON people(sid)')
    connection.execute('CREATE INDEX idx_col ON people(sid,borntime)')
    connection.commit()
    return connection, connection.cursor()


def db_insert_values(cursor, count):
    num = 1
    # age = 2 * num

    while num <= count:
        cursor.execute("insert into people(seq,srvid,sid,borntime) values (?,?,?,?)", (num, num, num, num))
        num += 1
        # age = 2 * num


def study_case4_autocommit_transaction(count):
    connection, cursor = prepare(isolation_level=None)

    elapse_time = Elapse_time('  自动commit')
    # connection.execute("PRAGMA synchronous = OFF")
    # connection.execute("PRAGMA journal_mode = MEMORY ")
    connection.execute("BEGIN TRANSACTION;")
    db_insert_values(cursor, count)
    # connection.execute('CREATE UNIQUE INDEX idx_srvid ON people(srvid)')
    # connection.execute('CREATE INDEX idx_col ON people(sid,borntime)')
    connection.execute("COMMIT;")

    cursor.execute("select count(*) from people;")
    print(cursor.fetchone())


def query_cnt():
    connection, cursor = prepare(isolation_level=None)
    cursor.execute("select count(*) from people;")
    print(cursor.fetchone())


if __name__ == '__main__':
    print(sqlite3.sqlite_version)
    count = 10000000
    # prepare()
    study_case4_autocommit_transaction(count)
    # query_cnt()