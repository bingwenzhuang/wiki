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
SELECT 
	'%s' as instance_name, ## 实例
	time as start_time, ## 记录时间
	txn_start_ts as tx_start_time, ## 事务ID
	user as db_user, ## 用户
	host as client_ip, ## 客户端
	conn_id, ## 会话ID
	CONVERT(query_time,char) as query_time, ## 执行耗时  
	CONVERT(parse_time,char) as parse_time, ## 解析耗时
	CONVERT(compile_time,char) as compile_time, ## 优化耗时
	CONVERT(rewrite_time,char) as rewrite_time, ## 重写耗时
	preproc_subqueries as sub_cnt, ## 子查询个数 
	CONVERT(preproc_subqueries_time,char) as sub_query_time, ## 子查询耗时
	CONVERT(prewrite_time,char) as prewrite_time, ## 第一阶段耗时
	CONVERT(wait_prewrite_binlog_time,char) as wait_prewrite_binlog_time, ## binlog耗时
	CONVERT(commit_time,char) as commit_time, ## 第二阶段耗时
	CONVERT(get_commit_ts_time,char) as get_commit_ts_time, ## 获取commit tso耗时
	CONVERT(commit_backoff_time,char) as commit_backoff_time, ## 提交冲突重试等待时间 
	backoff_types, ## 重试等待原因类型
	CONVERT(resolve_lock_time,char) as resolve_lock_time, ## 在tikv上解决锁冲突耗时
	CONVERT(local_latch_wait_time,char) as local_latch_wait_time, ## 在tidb上解决锁冲突耗时
	write_keys, ## 成功提交的行数 rows
	write_size, ## 成功提交的大小 rows size
	prewrite_region, ## 事务关联的region数  
	txn_retry, ## 事务重试次数
	CONVERT(cop_time,char) as cop_time, ## 在tikv cop执行耗时(cop是并发的,该值为累计值)
	CONVERT(process_time,char) as process_time, ## 在tikv 语句执行耗时(tikv是并发的,该值为累计值)
	CONVERT(wait_time,char) as wait_time, ## 在tikv 等待cop执行耗时(cop是并发的,该值为累计值)
	CONVERT(backoff_time,char) as backoff_time, ## 重试等待耗时
	CONVERT(lockkeys_time,char) as lockkeys_time, ## 锁key时长 
	request_count, ## 向cop 请求数 
	total_keys, ## cop 扫描的key数量 rows_examined(tikv层面)
	process_keys, ## cop 处理的key数量 rows_sent(tikv层面)
	total_keys-process_keys as hist_version_keys, ## 历史版本key数量
	db as db_name, ## 数据库名称
	index_names, ## 涉及使用索引
	is_internal, ## 是否为内部语句
	digest as sql_fingerprint_md5, ## 指纹md5
	stats, ## 状态
	cop_proc_avg,
	cop_proc_p90,
	cop_proc_max,
	cop_proc_addr,
	cop_wait_avg,
	cop_wait_p90,
	cop_wait_max,
	cop_wait_addr,
	mem_max, ## 内存
	disk_max, ## 存储
	succ, ## 是否成功
	plan_from_cache, ## 执行计划是否使用游标
	plan,  ## 执行计划 
	plan_digest, ## 执行计划md5
    query as sql_text ## 文本
FROM INFORMATION_SCHEMA.SLOW_QUERY
WHERE time >'%s' and time <= '%s'
and user<>'%s'
'''

GET_SLOW_DATE = '''
SELECT max(time) as max_time,count(*) cnt
FROM INFORMATION_SCHEMA.SLOW_QUERY
WHERE time >'%s' and time <= '%s'
and user<>'%s'
'''

GET_CHECKPOINT = '''
select IFNULL(max(ck_time),'1987-11-27 06:00:00') as min_time from DBA_META.SLOW_CHECKPOINT where instance_name = '%s';
'''

SET_CHECKPOINT = '''
REPLACE INTO DBA_META.SLOW_CHECKPOINT (instance_name,ck_time,cnt) VALUES('%s', '%s', %s)
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


def write_checkpoint(conn, instance_name, checkpoint_time, slow_cnt):
    get_row(conn, SET_CHECKPOINT % (instance_name, checkpoint_time, slow_cnt))


def get_checkpoint(conn, instance_name, default_max_time, db_user):
    min_time = get_row(conn, query=GET_CHECKPOINT % instance_name)[0]
    # print(GET_SLOW_DATE % (min_time , default_max_time , db_user))
    row = get_row(conn, query=GET_SLOW_DATE % (min_time, default_max_time, db_user))
    # return max_time , cnt
    return row[0], min_time, row[1]


def get_tidb_slow_log(conn, instance_name, max_time, min_time, db_user):
    datas = get_rows(conn, query=GET_SLOW_SQL % (instance_name, min_time, max_time, db_user))
    slow_cnt = len(datas)
    for data in datas:
        if isinstance(data['sql_text'], bytes):
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
        data["instance_name"] = data["instance_name"]
        data["start_time"] = data["start_time"].timestamp()
        data["tx_start_time"] = data["tx_start_time"]
        data["db_user"] = data["db_user"]
        data["client_ip"] = data["client_ip"]
        data["conn_id"] = data["conn_id"]
        data["query_time"] = data["query_time"]
        data["parse_time"] = data["parse_time"]
        data["compile_time"] = data["compile_time"]
        data["rewrite_time"] = data["rewrite_time"]
        data["sub_cnt"] = data["sub_cnt"]
        data["sub_query_time"] = data["sub_query_time"]
        data["prewrite_time"] = data["prewrite_time"]
        data["wait_prewrite_binlog_time"] = data["wait_prewrite_binlog_time"]
        data["commit_time"] = data["commit_time"]
        data["get_commit_ts_time"] = data["get_commit_ts_time"]
        data["commit_backoff_time"] = data["commit_backoff_time"]
        data["backoff_types"] = data["backoff_types"]
        data["resolve_lock_time"] = data["resolve_lock_time"]
        data["local_latch_wait_time"] = data["local_latch_wait_time"]
        data["write_keys"] = data["write_keys"]
        data["write_size"] = data["write_size"]
        data["prewrite_region"] = data["prewrite_region"]
        data["txn_retry"] = data["txn_retry"]
        data["cop_time"] = data["cop_time"]
        data["process_time"] = data["process_time"]
        data["wait_time"] = data["wait_time"]
        data["backoff_time"] = data["backoff_time"]
        data["lockkeys_time"] = data["lockkeys_time"]
        data["request_count"] = data["request_count"]
        data["total_keys"] = data["total_keys"]
        data["process_keys"] = data["process_keys"]
        data["hist_version_keys"] = data["hist_version_keys"]
        data["db_name"] = data["db_name"]
        data["index_names"] = data["index_names"]
        data["is_internal"] = data["is_internal"]
        data["sql_fingerprint_md5"] = data["sql_fingerprint_md5"]
        data["stats"] = data["stats"]
        data["cop_proc_avg"] = data["cop_proc_avg"]
        data["cop_proc_max"] = data["cop_proc_max"]
        data["cop_proc_addr"] = data["cop_proc_addr"]
        data["cop_wait_avg"] = data["cop_wait_avg"]
        data["cop_wait_p90"] = data["cop_wait_p90"]
        data["cop_wait_max"] = data["cop_wait_max"]
        data["cop_wait_addr"] = data["cop_wait_addr"]
        data["mem_max"] = data["mem_max"]
        data["disk_max"] = data["disk_max"]
        data["succ"] = data["succ"]
        data["plan_from_cache"] = data["plan_from_cache"]
        data["plan"] = data["plan"]
        data["plan_digest"] = data["plan_digest"]
        data["sql_text"] = data["sql_text"]
        data["tbl_names"] = tbl_names.lower()
        data["sql_fingerprint"] = sql_fingerprint
        data['simplifysqlfinger'] = sql_fingerprint[:30]
        if (data["db_name"] == '' or data["db_name"] is None):
            data["db_name"] = 'nil'
        print(json.dumps(data))
    return slow_cnt


def main():
    parser = argparse.ArgumentParser(description='analyze tidb-server slow log')
    parser.add_argument('--host', '-n', help='tidb-server hostname,default=127.0.0.1', default='127.0.0.1')
    parser.add_argument('--instance', '-i', help='tidb-server hostname,default=None', default=None)
    parser.add_argument('--port', '-P', help='tidb-server port,default=4000', type=int, default=4000)
    parser.add_argument('--user', '-u', help='tidb-server user name,default=slow_query', default='slow_query')
    parser.add_argument('--password', '-p', help='tidb-server password,need base64 encode',
                        default='U2xvd3F1ZXJ5MTEwNQ==')
    parser.add_argument('--period', '-c', help='tidb-server get peroid, unit second,default=10', type=int, default=10)
    args = parser.parse_args()
    try:
        instance_name = os.uname()[1]
    except:
        instance_name = args.host

    if args.instance:
        instance_name = args.instance

    conn_setting = {
        "user": args.user,
        "host": args.host,
        "port": args.port,
        "passwd": str(base64.b64decode(args.password), 'utf-8'),
        "autocommit": True
    }

    global conn
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
                time.sleep(args.period * 5)
                continue
        try:
            default_max_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            max_time, min_time, cnt = get_checkpoint(conn=conn, instance_name=instance_name,default_max_time=default_max_time, db_user=args.user)
            if cnt == 0:
                time.sleep(args.period)
                continue
            slow_cnt = get_tidb_slow_log(conn=conn, instance_name=instance_name, max_time=max_time, min_time=min_time,db_user=args.user)
            write_checkpoint(conn, instance_name=instance_name, checkpoint_time=max_time, slow_cnt=slow_cnt)
        except Exception as e:
            print('ERROR:', e)
            time.sleep(args.period * 5)
            continue
        time.sleep(args.period)


'''
usage: analyze_tidb_slow_log.py [-h] [--host HOST] [--port PORT] [--user USER]
                                [--password PASSWORD] [--period PERIOD]

analyze tidb-server slow log

optional arguments:
  -h, --help            show this help message and exit
  --host HOST, -n HOST  tidb-server hostname,default=127.0.0.1
  --port PORT, -P PORT  tidb-server port,default=4000
  --user USER, -u USER  tidb-server user name,default=slow_query
  --password PASSWORD, -p PASSWORD
                        tidb-server password
  --period PERIOD, -c PERIOD
                        tidb-server get peroid, unit second,default=10
'''
main()
