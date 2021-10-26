#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import configparser
import pymysql
import threading
import queue
import time
import datetime
import json
import hashlib
import sql_metadata
from sql_metadata import generalize_sql
import click
from warnings import filterwarnings
import psutil

filterwarnings("ignore",category=pymysql.Warning)
'''

获取MySQL 会话信息
线程信息
事务信息
锁信息
执行语句信息

按照一定频率获取，有消费者对以上信息进行分析，打印到文件里
'''

q = queue.Queue()

GET_INFO_SQL = '''

SELECT
    now() as time,
	a.id AS mysqlthreadid,
	e.trx_mysql_thread_id AS blockmysqlthreadid,
	b.trx_id AS trxid,
	d.blocking_trx_id AS blocktrxid,
	a.USER AS USER,
	substring_index( a.HOST, ':', 1 ) AS HOST,
	substring_index( a.HOST, ':', - 1 ) AS PORT,
	a.db AS dbname,
	a.command AS querytype,
	a.time AS executetime,
	a.state AS state,
	b.trx_state AS trxstate,
	b.trx_operation_state AS trxoperstate,
	b.trx_concurrency_tickets AS trxtickets,
	c.lock_table AS locktable,
	c.lock_index AS lockindex,
	c.lock_mode AS lockmode,
	a.info AS sqltext,
CASE

		WHEN b.trx_concurrency_tickets > 0 THEN
		1 ELSE 0
	END isinnodbticket,
CASE

		WHEN c.lock_table IS NOT NULL THEN
		1 ELSE 0
	END islock,
CASE

		WHEN b.trx_id IS NOT NULL THEN
		1 ELSE 0
	END istrx
FROM
	information_schema.PROCESSLIST a
	LEFT JOIN information_schema.INNODB_TRX b ON a.id = b.trx_mysql_thread_id
	LEFT JOIN information_schema.INNODB_LOCKS c ON b.trx_id = c.lock_trx_id
	LEFT JOIN information_schema.INNODB_LOCK_WAITS d ON c.lock_trx_id = d.requesting_trx_id
	AND c.lock_id = d.requested_lock_id
	LEFT JOIN information_schema.INNODB_TRX e ON d.blocking_trx_id = e.trx_id
WHERE (a.command <> 'Sleep' or (a.command = 'Sleep' and b.trx_state <>''))
and a.command not like 'Binlog Dump%'
and a.id <> connection_id()
'''


class Config():
    def __init__(self, ini_file_path):
        self.config = configparser.ConfigParser()
        self.config.read(filenames=ini_file_path, encoding='utf-8')

    def get_section(self):
        sections = self.config.sections()
        return sections

    def get_options(self, section_name):
        options = self.config.options(section_name)
        return options

    def get_items(self, section_name):
        itmes = self.config.items(section_name)
        return itmes

    def get_value(self, section_name, option_name):
        value = self.config.get(section_name, option_name)
        return value

    def set_value(self, section_name, option_name, value):
        self.config.set(section_name, option_name, value)


def get_md5(query):
    if isinstance(query, str):
        query = query.encode('utf-8')
        md = hashlib.md5()
        md.update(query)
        return md.hexdigest()


def reconn(conn):
    try:
        conn.ping()
    except:
        conn.ping(True)
    return conn


def get_row(conn, query):
    conncetion = reconn(conn)
    cursor = conncetion.cursor()
    cursor.execute(query)
    row = cursor.fetchone()
    cursor.close()
    return row


def get_rows(conn, query):
    connection = reconn(conn)
    cursor = connection.cursor(cursor=pymysql.cursors.SSDictCursor)
    cursor.execute(query)
    rows = cursor.fetchall()
    return rows

def pid_filename(instance_name):
    return "/tmp/%s_%s.pid" % (instance_name, 'get_session_info')

def delete_pid(instance_name):
    if os.path.exists(pid_filename(instance_name)):
        os.remove(pid_filename(instance_name))

def generate_pid(instance_name):
    instance_name_pid = str(os.getpid())
    pidfile = pid_filename(instance_name)

    if os.path.exists(pidfile):
        if psutil.pid_exists(int(open(pidfile, 'r').readlines()[0])):
            # print("%s job already exists, exiting" % instance_name)
            sys.exit(-1)
        else:
            delete_pid(instance_name)
    with open(pidfile,'w') as f:
        f.write(instance_name_pid)

class Producer(object):
    def __init__(self, mysql_setting, second):
        # super(Producer, self).__init__()
        self.mysql_settings = mysql_setting
        self.second = second
        self.conn = pymysql.connect(**self.mysql_settings)

    def run(self):
        # 安排下一秒再次执行
        timer = threading.Timer(interval=self.second, function=self.run)
        timer.start()
        rows = get_rows(conn=self.conn, query=GET_INFO_SQL)
        q.put(rows)
        # 执行完放入queue即可退出


class Consumer(threading.Thread):
    def __init__(self, instance_name):
        super(Consumer, self).__init__()
        self.instance_name = instance_name

    def run(self):
        '''
        time
        instance_name
        mysqlthreadid,
	blockmysqlthreadid,
        trxid,
        blocktrxid,
        USER,
        HOST,
        PORT,
        dbname,
        querytype,
        executetime,
        state,
        trxstate,
        trxoperstate,
        trxtickets,
        locktable,
        lockindex,
        lockmode,
        sqltext,
        isinnodbticket,
        islock,
        istrx,
        sqlfinger
        sqlfingermd5
        tables

        simplifysqlfinger

        '''
        # 常驻不退出
        while True:
            if not q.empty():
                datas = q.get()
            else:
                time.sleep(1)
                continue
            for data in datas:
                if isinstance(data['sqltext'], bytes):
                    data['sqltext'] = str(data['sqltext'], encoding='utf-8')
                try:
                    tables = ",".join(sql_metadata.get_query_tables(data["sqltext"]))
                    sqlfinger = generalize_sql(data["sqltext"])
                    if data['sqltext'].strip().lower() == 'commit':
                        tables = 'commit'
                        sqlfinger = 'commit'
                except:
                    # raise
                    tables = ''
                    sqlfinger = ''

                data['time'] = data["time"].timestamp()
                data['instance'] = self.instance_name
                data['sqlfinger'] = sqlfinger
                data['simplifysqlfinger'] = sqlfinger[:30]
                data['sqlfingermd5'] = get_md5(sqlfinger)
                data['tables'] = tables.lower()
                print(json.dumps(data))


@click.command()
@click.option('--instance_name', help='mysql/tidb instance name')
@click.option('--conf', default='conf/db.conf', help='config file, need abpath')
def main(instance_name, conf):
    '''
    python3 analyze_session_info.py --instance_name=dev-master
    '''
    if conf == 'conf/db.conf':
        dirname, filename = os.path.split(os.path.abspath(__file__))
        conf = dirname+ os.sep+'conf'+os.sep+'db.conf'
    try:
        config = Config(ini_file_path=conf)
        host = config.get_value(section_name=instance_name, option_name='host')
        user = config.get_value(section_name=instance_name, option_name='user')
        port = int(config.get_value(section_name=instance_name, option_name='port'))
        password = config.get_value(section_name=instance_name, option_name='passwrod')
        second = config.get_value(section_name=instance_name, option_name='second')
        mysql_setting = {
            "user": user,
            "host": host,
            "port": port,
            "passwd": password
        }
    except:
        print('please check you config file')
        sys.exit(-1)

    # 实例检测和守护
    generate_pid(instance_name = instance_name)

    # 消息生产者，每秒执行一次，非阻塞
    producter = Producer(mysql_setting=mysql_setting, second=int(second))
    timer = threading.Timer(interval=int(second),function=producter.run)
    timer.start()

    # 消息消费者，常驻，不退出
    consumer = Consumer(instance_name=instance_name)
    consumer.start()

main()