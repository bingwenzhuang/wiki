#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import hashlib
import re
import json
import sql_metadata
from sql_metadata import generalize_sql


def get_md5(query):
    if isinstance(query,str):
        query = query.encode('utf-8')
        md = hashlib.md5()
        md.update(query)
    return md.hexdigest()

def parse_slow_log(line):
    p1 = re.compile(r'[<](.*?)[>]', re.S)
    p2 = re.compile(r'time (.*?) msec', re.S)
    p3 = re.compile(r'max (.*?) msec', re.S)
    query_str = re.findall(p2, line)
    avg_min_max_str = re.findall(p3,line)
    sql_text = re.findall(p1, line)[0]
    if len(query_str) >0:
        query_time = int(query_str[0])
        avg_time = query_time
        min_time = query_time
        max_time = query_time

    if len(avg_min_max_str) >0:
        avg_time = avg_min_max_str[0].split("/")[0]
        min_time = avg_min_max_str[0].split("/")[1]
        max_time = avg_min_max_str[0].split("/")[2]
        query_time = avg_time


    data = {}
    try:
        tbl_names = ",".join(sql_metadata.get_query_tables(sql_text))
        sql_fingerprint = generalize_sql(sql_text)
        if sql_text.strip().lower() == 'commit':
            tbl_names = 'commit'
            sql_fingerprint = 'commit'
    except:
        pass
    if tbl_names == '':
        tbl_names = 'dual'
    try:
        db_name = tbl_names.split('.')[0]
        tbl_names = tbl_names.split('.')[1]
    except:
        db_name = 'None'
        tbl_names =  tbl_names
    data['db_name'] = db_name
    data['tbl_names'] = tbl_names
    data['sql_fingerprint_md5'] = get_md5(sql_fingerprint)
    data['sql_fingerprint'] = sql_fingerprint
    data['sql_text'] = sql_text
    data['is_crossnode'] = 1 if 'cross-node' in line else 0
    data['is_cnt'] = 1 if 'avg' in line else 0
    data['query_time'] = query_time
    data['avg_time'] = avg_time
    data['min_time'] = min_time
    data['max_time'] = max_time
    print(json.dumps(data))
    # sys.stdout.write(json.dumps(data))

for line in sys.stdin:
    p = re.compile(r'slow timeout', re.S)
    if re.findall(p, line):
        parse_slow_log(line)
    # sys.stdout.write(line)


