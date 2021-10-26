#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sshtunnel
from sshtunnel import SSHTunnelForwarder
import pymysql

sshtunnel.SSH_TIMEOUT = 5.0
sshtunnel.TUNNEL_TIMEOUT = 5.0

server = SSHTunnelForwarder(
    ('47.117.37.248',22),
    ssh_username='root',
    ssh_password='123456',
    remote_bind_address=('10.132.183.191', 4000)
)

server.start()

conn_setting = {
    "user": 'root',
    "host": '127.0.0.1',
    "port": server.local_bind_port,
    "passwd": 'Yealink1105',
    "autocommit": True
}


def reconn(conn):
    try:
        conn.ping()
    except:
        conn.ping(True)
    return conn

def get_row(conn ,query):
    connection = reconn(conn)
    cursor = connection.cursor()
    cursor.execute(query)
    row = cursor.fetchone()
    cursor.close()

    return row

def get_rows(conn,query):
    connection = reconn(conn)
    cursor = connection.cursor(cursor=pymysql.cursors.SSDictCursor)
    cursor.execute(query)
    rows = cursor.fetchall()
    cursor.close()

    return rows


conn = pymysql.connect(**conn_setting)

rows = get_rows(conn=conn,query="show databases")
for row in rows:
    print(row)

server.stop()