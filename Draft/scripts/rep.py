#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import queue
import threading
import time
import random
import datetime
import copy
import os

import pymysql
import click
from pymysqlreplication import BinLogStreamReader
from pymysqlreplication.row_event import DeleteRowsEvent, UpdateRowsEvent, WriteRowsEvent
from pymysqlreplication.event import RotateEvent, QueryEvent, XidEvent

'''
pre-check 
1. 检验表和复制表是否存在	
2. 检验表和复制表是否有主键
3. 检验表和复制表是否有外键
4. 检验是否存在checkpoint表 -- 没有创建
doing 
1. 获取binlog file和position
2. 开启流复制
3. 复制

功能模块
1. 开连接conn
2. 检验连接是否有效，重试
3. 执行查询模块的
4. 事务执行模块(多条SQL语句)
5. 获取logfile和logpos点
6. 主函数

'''

'''
Queue
'''
q = queue.Queue()

'''
Common util
'''


def parse_connection(connection_values):
    conn_format = connection_values.rsplit('@', 1)
    user, passwd = conn_format[0].split(":")
    host, port = conn_format[1].split(":")
    connection = {
        "user": user,
        "host": host,
        "port": int(port),
        "passwd": passwd
    }
    return connection


def reconn(conn):
    try:
        conn.ping()
    except:
        conn.ping(True)
    return conn


'''
Producer
'''


class Producer(threading.Thread):
    def __init__(self, mysql_settings, position_file, position_lag_file,databases, tables):
        super().__init__()
        self.mysql_settings = parse_connection(mysql_settings)
        self.conn = pymysql.connect(**self.mysql_settings)
        self.position_file = position_file
        self.position_lag_file = position_lag_file
        self.databases = databases
        self.tables = tables
        self.log_file, self.log_pos = self.get_binlog_file_pos()
        print("ProducerInfo: start file : {0} start pos : {1}".format(self.log_file, self.log_pos))

    def write_binlog_lag_file(self, info):
        with open(self.position_lag_file, 'w') as f:
            f.write(json.dumps(info, indent=2).strip())

    def get_binlog_file_pos(self):
        if not os.path.exists(self.position_file):
            conn = reconn(self.conn)
            cursor = conn.cursor()
            cursor.execute('show master status')
            row = cursor.fetchone()
            cursor.close()
            return row[0], row[1]
        with open(self.position_file) as f:
            info = f.read()
        info = json.loads(info)
        return info['file'], info['pos_end']

    def get_tbls_change_info(self):
        if not os.path.exists(self.position_file):
            return dict()
        with open(self.position_file) as f:
            info = f.read()
        info = json.loads(info)
        return eval(info['producerchangeinfo'])

    def compare_items(self, items):
        (k, v) = items
        if v is None:
            return '`%s` IS %%s' % k
        else:
            return '`%s`=%%s' % k

    def parse_connection(self, connection_values):
        conn_format = connection_values.rsplit('@', 1)
        user, passwd = conn_format[0].split(":")
        host, port = conn_format[1].split(":")
        connection = {
            "user": user,
            "host": host,
            "port": int(port),
            "passwd": passwd
        }
        return connection

    def run(self):
        server_id = random.randint(99999, 999999999)
        stream = BinLogStreamReader(connection_settings=self.mysql_settings, server_id=server_id, blocking=True,
                                    only_events=[DeleteRowsEvent, WriteRowsEvent, UpdateRowsEvent, RotateEvent,QueryEvent,XidEvent],
                                    resume_stream=True,
                                    log_file=self.log_file,
                                    log_pos=self.log_pos,
                                    only_schemas=self.databases, only_tables=self.tables)

        conn = reconn(self.conn)
        cursor = conn.cursor()

        transaction_sql = []
        table_change_cnt = self.get_tbls_change_info()
        for binlog_event in stream:
            if isinstance(binlog_event, RotateEvent):
                log_file = binlog_event.next_binlog
            elif isinstance(binlog_event, QueryEvent) and binlog_event.query == 'BEGIN':
                transaction_start_pos = binlog_event.packet.log_pos
            elif isinstance(binlog_event, (DeleteRowsEvent, UpdateRowsEvent, WriteRowsEvent)):
                schema, table = binlog_event.schema, "%s_bak" % binlog_event.table
                for row in binlog_event.rows:
                    if isinstance(binlog_event, DeleteRowsEvent):
                        delete_sql_template = 'DELETE FROM `{0}`.`{1}` WHERE {2} LIMIT 1'.format(
                            schema, table, ' AND '.join(map(self.compare_items, row['values'].items())))
                        delete_sql = cursor.mogrify(delete_sql_template, list(row['values'].values()))
                        transaction_sql.append(delete_sql)
                        table_change_cnt['%s_del' % table] = table_change_cnt.get('%s_del' % table, 0) + 1
                    elif isinstance(binlog_event, UpdateRowsEvent):
                        update_sql_template = 'REPLACE `{0}`.`{1}`({2}) VALUES({3})'.format(schema, table,
                                                                                            ', '.join(map(
                                                                                                lambda
                                                                                                    key: '`%s`' % key,
                                                                                                row[
                                                                                                    'after_values'].keys())),
                                                                                            ', '.join(['%s'] * len(
                                                                                                row['after_values'])))
                        update_sql = cursor.mogrify(update_sql_template, list(row['after_values'].values()))
                        transaction_sql.append(update_sql)
                        table_change_cnt['%s_upd' % table] = table_change_cnt.get('%s_upd' % table, 0) + 1
                    elif isinstance(binlog_event, WriteRowsEvent):
                        insert_sql_template = 'INSERT INTO `{0}`.`{1}`({2}) VALUES ({3})'.format(
                            schema, table,
                            ', '.join(map(lambda key: '`%s`' % key, row['values'].keys())),
                            ', '.join(['%s'] * len(row['values']))
                        )
                        insert_sql = cursor.mogrify(insert_sql_template, list(row['values'].values()))
                        transaction_sql.append(insert_sql)
                        table_change_cnt['%s_ins' % table] = table_change_cnt.get('%s_ins' % table, 0) + 1

            elif isinstance(binlog_event, XidEvent):
                if transaction_sql:
                    transaction_sql.append(position_info)
                    q.put(copy.deepcopy(transaction_sql))
                    print('ProducerInfo: change data count:' + str(table_change_cnt))
                transaction_sql = []

                transaction_end_pos = binlog_event.packet.log_pos
                transaction_end_timestamp = binlog_event.timestamp
                position_info = {
                    'file': log_file,
                    'pos_start': transaction_start_pos,
                    'pos_end': transaction_end_pos,
                    'timestamp_end': transaction_end_timestamp,
                    'producerchangeinfo': str(table_change_cnt),
                    'queuesize': q.qsize()
                }
                self.write_binlog_lag_file(position_info)


        cursor.close()
        stream.close()


'''
consumer
'''


class Consumer(threading.Thread):
    def __init__(self, mysql_setting, position_file):
        super().__init__()
        self.conn = pymysql.connect(**parse_connection(mysql_setting))
        self.position_file = position_file
        self.commit_cnt = 0

    def write_binlog_file_pos(self, info):
        with open(self.position_file, 'w') as f:
            f.write(json.dumps(info, indent=2).strip())

    def run(self):
        while True:
            # print('ConsumerInfo: queue.size is ' + str(q.qsize()))
            begin = datetime.datetime.now()
            if not q.empty():
                sqls = q.get()
            else:
                time.sleep(1)
                continue
            conn = reconn(self.conn)
            cursor = conn.cursor()
            try:
                position_info = sqls.pop()
                for each_sql in sqls:
                    cursor.execute(each_sql)
                    # print('ConsumerInfo: ' + each_sql)
                self.commit_cnt += 1
                if self.commit_cnt >= 1000:
                    conn.commit()
                    print('ConsumerInfo: change data count:' + str(position_info['producerchangeinfo']))
                    self.write_binlog_file_pos(position_info)
                    self.commit_cnt = 0
            except Exception as e:
                print('ConsumerError: ' + str(e) + " " + ', '.join(sqls))
                conn.rollback()
            finally:
                # cursor.close()
                end = datetime.datetime.now()
                if (end - begin).seconds > 0:
                    print("ConsumerInfo: Time : %s" % str((end - begin).seconds))




@click.command()
@click.option('--source',help='user:password@hostname:port')
@click.option('--dest',help='user:password@hostname:port')
@click.option('--databases',help='db1,db2')
@click.option('--tables',help='table1,table2')
@click.option('--position',help='/home/ubuntu/pupu/bowenz/archive/job1.txt')
def main(source,dest,databases,tables,position):
    databases = databases.split(',')
    tables = tables.split(',')
    filename = os.path.basename(position).split('.')[0]
    position_file = position
    position_lag_file = os.path.join(os.path.dirname(position),filename+'lag.txt')

    producer = Producer(source, position_file, position_lag_file, databases, tables)
    producer.start()

    consumer = Consumer(dest, position_file)
    consumer.start()


if __name__ == '__main__':
    '''
    Setting
    parameters:
    source: user:password@hostname:port
    dest: user:password@hostname:port
    databases: pupu_log,pupu_main
    tables: messagepushtask,pickinglogs
    position: /home/ubuntu/pupu/bowenz/archive/job1.txt
    
    
    example: 
    python3 rep.py --source=root:qFCSaiEOInwuxyy9@pupu-qa.cq1hwn7a0n9c.rds.cn-north-1.amazonaws.com.cn:3306 --dest=root:qFCSaiEOInwuxyy9@pupu-qa.cq1hwn7a0n9c.rds.cn-north-1.amazonaws.com.cn:3306 --databases=pupu_log --tables=messagepushtask --position=/Users/bowenz/Downloads/py-mysql-binlogserver/position_messagepushtask.txt
    
    
    '''

    main()

    # source = 'root:qFCSaiEOInwuxyy9@pupu-pre.cq1hwn7a0n9c.rds.cn-north-1.amazonaws.com.cn:3306'
    # dest = 'root:qFCSaiEOInwuxyy9@pupu-pre.cq1hwn7a0n9c.rds.cn-north-1.amazonaws.com.cn:3306'
    # databases = ['fresh_log_test', 'tmp']
    # tables = ['messagepushtask', 'trace_log']
    # position_file = '/Users/bowenz/Downloads/py-mysql-binlogserver/position_file.txt'
    # position_lag_file = '/Users/bowenz/Downloads/py-mysql-binlogserver/position_lag_file.txt'
    #
    # producer = Producer(source, position_file,position_lag_file, databases, tables)
    # producer.start()
    #
    # consumer = Consumer(dest, position_file)
    # consumer.start()
