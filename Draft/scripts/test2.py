#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import time
import argparse
import pymysql
from datetime import datetime, timezone
import sql_metadata
from sql_metadata import generalize_sql
import base64
import pymysql.converters


def borked_utf8_decode(data):
    """
    Work around input with unpaired surrogates or surrogate pairs,
    replacing by XML char refs: look for "&#\d+;" after.
    """
    # print(data)
    return data.encode("utf-8", "xmlcharrefreplace").decode("utf-8")


# use this in your connection
pymysql_use_unicode = False
conversions = pymysql.converters.conversions
conversions[pymysql.converters.FIELD_TYPE.STRING] = borked_utf8_decode
conversions[pymysql.converters.FIELD_TYPE.VAR_STRING] = borked_utf8_decode
conversions[pymysql.converters.FIELD_TYPE.VARCHAR] = borked_utf8_decode

GET_SLOW_SQL = '''
select
	target,
	SUM(CAST(parameter_value AS SIGNED)) total
from
	service_ticket_parameter stp
inner join service_ticket st on
	st.id = stp.service_ticket_id
where
	st.target in ('%s')
	and st.category in (%d)
	and stp.parameter_name in ('%s')
	and st.ticket_status in (%d)
group by
	target
'''



def reconn(conn):
    try:
        conn.ping()
    except:
        conn.ping(True)
    return conn


def get_row(conn, query):
    connection = reconn(conn)
    cursor = connection.cursor()
    cursor.execute(query)
    row = cursor.fetchone()
    cursor.close()

    return row


def get_rows(conn, query):
    connection = reconn(conn)
    cursor = connection.cursor(cursor=pymysql.cursors.SSDictCursor)
    cursor.execute(query)
    rows = cursor.fetchall()
    cursor.close()

    return rows



def main():

    conn_setting = {
        "user": 'root',
        "host": '10.120.25.142',
        "port": 9436,
        "autocommit": True,
        "db": 'user_manager'
    }

    conn = pymysql.connect(**conn_setting)
    datas = get_rows(conn, query=GET_SLOW_SQL % ('e55ee50ec3fc466b8fc818be9d2adcb5', 7, 'storage_size', 0))
    print(datas)

main()
