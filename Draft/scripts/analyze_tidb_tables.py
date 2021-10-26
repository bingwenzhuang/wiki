#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import base64
import argparse
import pymysql
import time
from datetime import datetime,timezone

GET_ANALYZE_TBS_SQL='''
select 
	CONCAT('analyze table ',t.TABLE_SCHEMA,'.',t.TABLE_NAME)  as analyze_sql,
	CONCAT(t.TABLE_SCHEMA,'.',t.TABLE_NAME) as tablename
from
	INFORMATION_SCHEMA.TABLES t
where
	t.TABLE_SCHEMA not in('INFORMATION_SCHEMA', 'METRICS_SCHEMA', 'PERFORMANCE_SCHEMA', 'dba_meta', 'mysql', 'tmp')
'''

GET_100_HEALTH_TBS_SQL='''
SHOW STATS_HEALTHY where healthy= 100;
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

def exec_analyze_tbs(conn):
    print("Start analyze tables at %s" % datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))
    # start_time = (datetime.now(timezone.utc) + timedelta(hours=-1)).strftime("%Y-%m-%d %H:%M:%S")
    analyze_sqls = get_rows(conn=conn, query=GET_ANALYZE_TBS_SQL)
    tbls_100 = get_rows(conn=conn,query=GET_100_HEALTH_TBS_SQL)
    not_analyze_tbs = []
    for table in tbls_100:
        not_analyze_tbs.append("%s.%s" % (table['Db_name'],table['Table_name']))
    for index ,analyze_sql in enumerate(analyze_sqls,1):
        if analyze_sql['tablename'] not in not_analyze_tbs:
            sql = analyze_sql['analyze_sql']
            tic = time.time()
            get_row(conn=conn,query=sql)
            toc = time.time()
            print("%s Analyze table %s (%s)" % (index,analyze_sql['tablename'],toc-tic))

    print("End analyze tables at %s" % datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))

def main():
    parser = argparse.ArgumentParser(description='analyze tidb-cluster tables and indexes information for better performance')
    parser.add_argument('--host', '-n', help='tidb-server hostname,default=None', default=None)
    parser.add_argument('--port', '-P', help='tidb-server port,default=9436', type=int, default=9436)
    parser.add_argument('--user', '-u', help='tidb-server user name,default=backupUser', default='backupUser')
    parser.add_argument('--password', '-p', help='tidb-server password,need base64 encode', default='WWVhbGluazExMDViYWNrdXB1c2Vy')
    args = parser.parse_args()

    if args.host is None:
        print("Host arg must set value")
        sys.exit(1)

    conn_setting = {
        "user": args.user,
        "host": args.host,
        "port": args.port,
        "passwd": str(base64.b64decode(args.password),'utf-8'),
        "autocommit": True
    }
    try:
        conn = pymysql.connect(**conn_setting)
        exec_analyze_tbs(conn=conn)
    except Exception as e:
        print('ERROR:', e)
        sys.exit(1)

'''
usage: analyze_tidb_tables.py [-h] [--host HOST] [--port PORT]
                                      [--user USER] [--password PASSWORD]

analyze tidb-cluster tables and indexes information for better performance

optional arguments:
  -h, --help            show this help message and exit
  --host HOST, -n HOST  tidb-server hostname,default=None
  --port PORT, -P PORT  tidb-server port,default=9436
  --user USER, -u USER  tidb-server user name,default=backupUser
  --password PASSWORD, -p PASSWORD
                        tidb-server password,need base64 encode
'''
main()