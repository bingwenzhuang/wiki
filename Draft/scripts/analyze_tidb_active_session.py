#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import base64
import argparse
import pymysql
import time
import sql_metadata
from datetime import datetime, timezone
from sql_metadata import generalize_sql

GET_ACTIVE_SESSION='''
SELECT
     now() as start_time,  ## 采集时间
     '%s'  as instance_name, ## 实例
	 id as conn_id,  ## 会话ID 
	 user as db_user, ## 用户
	 host as client_ip, ## 客户端
	 db as db_name, ## 数据库
	 command as query_type, ## 执行类别 
	 time as query_time, ## 执行耗时
	 state , ## 状态(暂时无用)
	 info as sql_text, ## 语句
	 digest as sql_fingerprint_md5, ## 语句指纹MD5
	 mem as memory, ## 会话使用内存
     txnstart ## 事务开始时间和事务ID
FROM
	INFORMATION_SCHEMA.PROCESSLIST
WHERE COMMAND <> 'Sleep' 
and USER<>'%s'
'''


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


def get_tidb_active_session_info(conn,instance_name,db_user):
    datas = get_rows(conn=conn,query=GET_ACTIVE_SESSION % (instance_name,db_user))
    for data in datas:
        if isinstance(data['sql_text'],bytes):
            data['sql_text'] = str(data['sql_text'], encoding='utf-8')
        try:
            tbl_names = ",".join(sql_metadata.get_query_tables(data["sql_text"]))
            sql_fingerprint = generalize_sql(data["sql_text"])
            if data['sql_text'].strip().lower() == 'commit':
                tbl_names = 'commit'
                sql_fingerprint = 'commit'
        except:
            continue
        if tbl_names == '':
            tbl_names = 'dual'
        data['start_time'] = data["start_time"].timestamp()
        data['instance_name'] = data["instance_name"]
        data['conn_id'] = data["conn_id"]
        data['db_user'] = data["db_user"]
        data['client_ip'] = data["client_ip"]
        data['db_name'] = data["db_name"]
        data['query_type'] = data["query_type"]
        data['query_time'] = data["query_time"]
        data['state'] = data["state"]
        data['sql_text'] = data["sql_text"]
        data['sql_fingerprint_md5'] = data["sql_fingerprint_md5"]
        data['memory'] = data["memory"]
        data['txnstart'] = data["txnstart"]
        if (data["txnstart"] == '' or data["txnstart"] is None):
            data['txn_id'] = 'nil'
            data['txn_start_time'] = 'nil'
        else:
            data['txn_id'] = datetime.now(timezone.utc).strftime("%Y") + '-' + data["txnstart"].split('(')[0]
            data['txn_start_time'] = data["txnstart"].split('(')[1].replace(')', '')

        data["tbl_names"] = tbl_names.lower()
        data["sql_fingerprint"] = sql_fingerprint
        data['simplifysqlfinger'] = sql_fingerprint[:30]
        if (data["db_name"] == '' or data["db_name"] is None):
            data["db_name"] = 'nil'
        print(json.dumps(data))

def main():
    parser = argparse.ArgumentParser(description='analyze tidb-server active session information')
    parser.add_argument('--host', '-n', help='tidb-server hostname,default=127.0.0.1', default='127.0.0.1')
    parser.add_argument('--port', '-P', help='tidb-server port,default=4000', type=int, default=4000)
    parser.add_argument('--user', '-u', help='tidb-server user name,default=slow_query', default='slow_query')
    parser.add_argument('--password', '-p', help='tidb-server password,need base64 encode', default='U2xvd3F1ZXJ5MTEwNQ==')
    parser.add_argument('--period','-c', help='tidb-server get peroid, unit second,default=1', type= int, default=1)
    args = parser.parse_args()
    try:
        instance_name = os.uname()[1]
    except:
        instance_name=args.host

    conn_setting = {
        "user": args.user,
        "host": args.host,
        "port": args.port,
        "passwd": str(base64.b64decode(args.password),'utf-8'),
        "autocommit": True
    }
    try:
        conn = pymysql.connect(**conn_setting)
    except Exception as e:
        print('ERROR:', e)
    while True:
        try:
            reconn(conn=conn)
        except Exception as e:
            print('ERROR:', e)
            try:
                conn = pymysql.connect(**conn_setting)
            except Exception as e:
                print('ERROR:', e)
                # time.sleep(args.period * 5)
                continue
        try:
            get_tidb_active_session_info(conn=conn,instance_name=instance_name,db_user=args.user)
        except Exception as e:
            print('ERROR:', e)
            time.sleep(args.period*5)
            continue
        time.sleep(args.period)

'''
usage: analyze_tidb_active_session.py [-h] [--host HOST] [--port PORT]
                                      [--user USER] [--password PASSWORD]
                                      [--period PERIOD]

analyze tidb-server active session information

optional arguments:
  -h, --help            show this help message and exit
  --host HOST, -n HOST  tidb-server hostname,default=127.0.0.1
  --port PORT, -P PORT  tidb-server port,default=4000
  --user USER, -u USER  tidb-server user name,default=slow_query
  --password PASSWORD, -p PASSWORD
                        tidb-server password,need base64 encode
  --period PERIOD, -c PERIOD
                        tidb-server get peroid, unit second,default=1
'''
main()