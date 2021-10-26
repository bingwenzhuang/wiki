#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import base64
import argparse
import pymysql
from datetime import datetime,timezone
import oss2
from boto3.session import Session


GEN_USER_SQL="select CONCAT('show grants for ''',user,'''@''',host,''';') as user_sql,CONCAT('CREATE USER IF NOT EXISTS ''',user,'''@''',host,'''') as gen_user_sql from mysql.user"

GEN_SEQ_SQL='''
select CONCAT('show create sequence ',s.SEQUENCE_SCHEMA,'.',s.SEQUENCE_NAME,';') as seq_sql,s.SEQUENCE_SCHEMA as seqschema from INFORMATION_SCHEMA.SEQUENCES s 
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

def write_sql_file(filename,info):
    with open(filename, 'a',encoding="utf-8") as f:
        f.write(info)

def print_user_sql(conn,filename):
    # start_time = (datetime.now(timezone.utc) + timedelta(hours=-1)).strftime("%Y-%m-%d %H:%M:%S")
    user_sqls = get_rows(conn=conn, query=GEN_USER_SQL)
    for index ,user_sql in enumerate(user_sqls,1):
        write_sql_file(filename, "%s;\n\r" % user_sql['gen_user_sql'])
        print("%s;" % user_sql['gen_user_sql'])
        rows = get_rows(conn=conn,query=user_sql['user_sql'])
        for row in rows:
            for v in row.values():
                write_sql_file(filename,"%s;\n\r" % v)
                print("%s;" % v)

def print_seq_sql(conn,filename):
    seq_sqls = get_rows(conn=conn, query=GEN_SEQ_SQL)
    for index ,seq_sql in enumerate(seq_sqls,1):
        rows = get_rows(conn=conn, query=seq_sql['seq_sql'])
        for row in rows:
            write_sql_file(filename,"use " + seq_sql['seqschema'] + ";" )
            write_sql_file(filename,"%s;\n\r" % row['Create Sequence'])
            print("use %s;" % seq_sql['seqschema'])
            print("%s;" % row['Create Sequence'])

def upload_cloud_storage(type,region,bucket,accessid,accesskey,filename,namespace,host):
    print("Start upload %s to %s cloud storage" % (('%s/%s/%s' % (namespace,host,filename)),type))
    keyname = '%s/%s/%s' % (namespace, host, filename)
    if type == "oss":
        auth = oss2.Auth(accessid, accesskey)
        bucket = oss2.Bucket(auth, 'http://oss-%s.aliyuncs.com' % region, bucket)
        bucket.put_object_from_file(keyname, filename)
    elif type == "s3":
        session = Session(aws_access_key_id=accessid, aws_secret_access_key=accesskey, region_name=region)
        s3 = session.client("s3")
        s3.upload_file(Filename=filename, Key=keyname, Bucket=bucket)
    else:
        print("Invalid cloud storage type")
        sys.exit(1)

def main():
    parser = argparse.ArgumentParser(description='analyze tidb-cluster tables and indexes information for better performance')
    parser.add_argument('--host', '-n', help='tidb-server hostname,default=None', default=None)
    parser.add_argument('--port', '-P', help='tidb-server port,default=9436', type=int, default=9436)
    parser.add_argument('--user', '-u', help='tidb-server user name,default=backupUser', default='backupUser')
    parser.add_argument('--password', '-p', help='tidb-server password,need base64 encode', default='WWVhbGluazExMDViYWNrdXB1c2Vy')
    parser.add_argument('--ns',help="k8s namespace name,default=ybdp-tidb",default='ybdp-tidb')
    parser.add_argument('--upload',help="whether to upload to the cloud,default=False",default=False,action='store_true')
    parser.add_argument('--type',help="cloud storage type,s3 or oss,default=oss",default='oss')
    parser.add_argument('--region', help="cloud storage region", default=None)
    parser.add_argument('--bucket', help="cloud storage bucket", default=None)
    parser.add_argument('--accessid', help="cloud storage access id,need base64 encode", default=None)
    parser.add_argument('--accesskey', help="cloud storage access key,need base64 encode", default=None)
    args = parser.parse_args()

    if args.host is None:
        print("Host arg must set value")
        sys.exit(1)

    if args.upload:
        if args.region is None or args.bucket is None or args.accessid is None or args.accesskey is None:
            print("[region-bucket-accessid-accesskey] args must set value")
            sys.exit(1)

    conn_setting = {
        "user": args.user,
        "host": args.host,
        "port": args.port,
        "passwd": str(base64.b64decode(args.password),'utf-8'),
        "autocommit": True,
        "connect_timeout": 100
    }
    try:
        conn = pymysql.connect(**conn_setting)
        filename = "backup_user_and_sequence_%s.sql" % datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        print_user_sql(conn=conn,filename=filename)
        print_seq_sql(conn=conn,filename=filename)
        print("Generate Database sql scripts: %s" % filename)
        if args.upload:
            accessid=str(base64.b64decode(args.accessid),'utf-8')
            accesskey = str(base64.b64decode(args.accesskey), 'utf-8')
            upload_cloud_storage(type=args.type,region=args.region,bucket=args.bucket,accessid=accessid,accesskey=accesskey,filename=filename,namespace=args.ns,host=args.host)
    except Exception as e:
        print('ERROR:', e)
        sys.exit(1)

'''
usage: bk_tidb_user_seq.py [-h] [--host HOST] [--port PORT] [--user USER]
                           [--password PASSWORD] [--ns NS] [--upload UPLOAD]
                           [--type TYPE] [--region REGION] [--bucket BUCKET]
                           [--accessid ACCESSID] [--accesskey ACCESSKEY]

analyze tidb-cluster tables and indexes information for better performance

optional arguments:
  -h, --help            show this help message and exit
  --host HOST, -n HOST  tidb-server hostname,default=None
  --port PORT, -P PORT  tidb-server port,default=9436
  --user USER, -u USER  tidb-server user name,default=backupUser
  --password PASSWORD, -p PASSWORD
                        tidb-server password,need base64 encode
  --ns NS               k8s namespace name,default=ybdp-tidb
  --upload UPLOAD       whether to upload to the cloud,default=True
  --type TYPE           cloud storage type，s3 or oss,default=oss
  --region REGION       cloud storage region
  --bucket BUCKET       cloud storage bucket
  --accessid ACCESSID   cloud storage access id,need base64 encode
  --accesskey ACCESSKEY
                        cloud storage access key,need base64 encode
'''
main()
