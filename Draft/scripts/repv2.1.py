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
import sys

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
                    print('Product: ' + str(position_info['producerchangeinfo']))
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
    def __init__(self, mysql_setting, position_file, csize=0, ctime=0):
        super().__init__()
        self.conn = pymysql.connect(**parse_connection(mysql_setting))
        self.cursor = None
        self.position_file = position_file
        self.csize = csize
        self.ctime = ctime
    
    def create_cursor(self):
        if self.cursor is None:
            self.cursor = reconn(self.conn).cursor()

    def drop_cursor(self):
        if self.cursor:
            self.cursor.close()
            self.cursor = None

    def write_binlog_file_pos(self, info):
        with open(self.position_file, 'w') as f:
            f.write(json.dumps(info, indent=2).strip())
    
    def commit_flag(self, count, t):
        # 如果只有限制次数的
        if self.csize and not self.ctime:
            if self.csize <= count:
                return True
            return False
        # 如果限制次数和限制时间都有
        if self.csize and self.ctime:
            # 限制次数或者限制时间，哪个先达到都可以进行提交
            if self.csize <= count or self.ctime <= t:
                return True
            return False
        # 如果只有限制时间的
        if not self.csize and self.ctime:
            if self.ctime <= t:
                return True
            return False
        return True

    def execute_to_error_position(self, execute_queue):
        print('ConsumerError: Start find error position.')
        self.create_cursor()
        for sqls in execute_queue:
            position_info = sqls[1]
            for each_sql in sqls[0]:
                self.cursor.execute(each_sql)
            try:
                self.conn.commit()
                self.drop_cursor()
                self.write_binlog_file_pos(position_info)
            except Exception as e:
                print('ConsumerError: ' + str(e) + 'consumer stop position ' + str(position_info))
                print('ConsumerError: ' + str(e) + 'consumer stop sqls ' + str(sqls))
                self.conn.rollback()
                self.drop_cursor()
                self.conn.close()
                print('process exit.')
                sys.exit()
    
    def to_commit(self, execute_queue ,execute_time, position_info):
        if self.commit_flag(len(execute_queue), execute_time) and position_info:
            try:
                self.conn.commit()
                print('Consumer: ' + str(position_info['producerchangeinfo']) + " Time : %s" % str(execute_time))
                # 提交完成后记录binlog位置
                self.write_binlog_file_pos(position_info)
                # 提交完成后计数，时间清零，下次循环重新开始
                execute_queue, execute_time, position_info = [], 0, None
                # 关闭cursor
                # self.drop_cursor()
                return execute_queue, execute_time, position_info
            except Exception as e:
                print('ConsumerError: ' + str(e))
                self.conn.rollback()
                self.drop_cursor()
                self.execute_to_error_position(execute_queue)
        return execute_queue, execute_time, position_info

    def run(self):
        execute_queue =[]
        execute_time = 0
        position_info = None
        while True:
            # 一次循环的开始时间记录
            begin = time.time()
            # 如果队列是空，等待1s后重新进行循环
            if not q.empty():
                sqls = q.get()
            else:
                time.sleep(1)
                execute_time += (time.time() - begin)
                execute_queue, execute_time, position_info = self.to_commit(execute_queue, execute_time, position_info)
                continue
            # 确认和Mysql的连接正常
            self.create_cursor()
            position_info = sqls.pop()
            for each_sql in sqls:
                self.cursor.execute(each_sql)
            # cursor完成一次事务操作，操作事务进入列表
            execute_queue.append((sqls, position_info))
            # cursor完成一次事务操作，操作时间增加当前时间减去当前循环开始时间的差异秒数
            execute_time += (time.time() - begin)
            # 判断，当次数，时间都满足时，才进行提交
            execute_queue, execute_time, position_info = self.to_commit(execute_queue, execute_time, position_info)


@click.command()
@click.option('--source',help='user:password@hostname:port')
@click.option('--dest',help='user:password@hostname:port')
@click.option('--databases',help='db1,db2')
@click.option('--tables',help='table1,table2')
@click.option('--position',help='/home/ubuntu/pupu/bowenz/archive/job1.txt')
@click.option('--csize', default=0, type=int, help='<--csize 1000> \n默认值为0，就是不启用；\n每当执行了1000条sql后才commit，以提高效率降低延迟; \n建议和ctime参数配合使用，防止长时间没有commit的情况出现')
@click.option('--ctime', default=0, type=int ,help='<--ctime 10> \n默认值为0，就是不启用；\n单位为秒，每10秒中执行一次commit，以提高效率降低延迟; \n配合csize参数使用，如果10s时间仍然没有commit，将不考虑csize，直接commit')
def main(source,dest,databases,tables,position, csize, ctime):
    if not source or not dest or not databases or not tables or not position:
        print('--help 获取帮助')
        return
    databases = databases.split(',')
    tables = tables.split(',')
    filename = os.path.basename(position).split('.')[0]
    position_file = position
    position_lag_file = os.path.join(os.path.dirname(position),filename+'lag.txt')

    producer = Producer(source, position_file, position_lag_file, databases, tables)
    producer.start()

    consumer = Consumer(dest, position_file, csize, ctime)
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
    python3 mysql2mysql.py --source=root:qFCSaiEOInwuxyy9@pupu-pre.cq1hwn7a0n9c.rds.cn-north-1.amazonaws.com.cn:3306 --dest=root:qFCSaiEOInwuxyy9@pupu-pre.cq1hwn7a0n9c.rds.cn-north-1.amazonaws.com.cn:3306 --databases=fresh_log_test --tables=messagepushtask --position=/Users/bowenz/Downloads/py-mysql-binlogserver/position_file.txt
    python3 mysql2mysql.py --source=root:qFCSaiEOInwuxyy9@pupu-pre.cq1hwn7a0n9c.rds.cn-north-1.amazonaws.com.cn:3306 --dest=root:qFCSaiEOInwuxyy9@pupu-pre.cq1hwn7a0n9c.rds.cn-north-1.amazonaws.com.cn:3306 --databases=fresh_log_test --tables=messagepushtask --position=/home/ubuntu/pupu/bowenz/archive/qa_messagepushtask_file.txt
    
    
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
