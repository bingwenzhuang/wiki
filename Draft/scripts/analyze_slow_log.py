#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os,json,sys
import hashlib
import configparser
import click
import psutil
import pymysql
import sql_metadata
from sql_metadata import generalize_sql

DEFAULT_DATE = '2020-06-11 08:00:00.000000'
#record max slow log start_time
#need to lock
SLOW_SQL='''
SELECT
	a.start_time as start_time,
	SUBSTRING_INDEX(SUBSTRING_INDEX(substring_index(a.user_host,'@',1), '[', -1), ']', 1) as  db_user,
	SUBSTRING_INDEX(SUBSTRING_INDEX(substring_index(a.user_host,'@',-1), '[', -1), ']', 1) as client_ip,
	a.query_time,
	a.lock_time,
	a.rows_sent,
	a.rows_examined,
	a.db as db_name,
	a.sql_text,
	a.thread_id 
FROM
	mysql.slow_log_backup a
'''

# GET_SLOW_DATE='''
# SELECT max(a.start_time) as max_time, min(a.start_time) as min_time
# FROM mysql.slow_log_backup a
# WHERE a.start_time >='%s'
# '''

ROTATE_SLOW_LOG = '''
CALL mysql.rds_rotate_slow_log
'''

class Config():
    def __init__(self, ini_file_path):
        """
        :param ini_file_path: ini 文件的路径
        """
        self.config = configparser.ConfigParser()  # 实例化
        self.config.read(ini_file_path,encoding='utf-8')

    def get_section(self):
        """
        文件中 [baseconf] 这个就是节点，该方法是获取文件中所有节点，并生成列表
        :return: 返回内容-->> ['baseconf', 'concurrent']
        """
        sections = self.config.sections()
        return sections

    def get_option(self, section_name):
        """
        文件中 host,port... 这个就是选项，该方法是获取文件中某个节点中所有的选项，并生成列表
        :param section_name: 节点名称
        :return: section_name="baseconf" 返回内容-->> ['host', 'port', 'user', 'password', 'db_name']
        """
        option = self.config.options(section_name)
        return option

    def get_items(self, section_name):
        """
        该方法是获取文件中某个节点中的所有选项及对应的值
        :param section_name: 节点名称
        :return: section_name="baseconf" 返回内容-->> [('host', '127.0.0.1'), ('port', '11223')........]
        """
        option_items = self.config.items(section_name)
        return option_items

    def get_value(self, section_name, option_name):
        """
        该方法是获取文件中对应节点中对应选项的值
        :param section_name: 节点名称
        :param option_name: 选项名称
        :return: section_name="baseconf"，option_name='host' 返回内容-->> '127.0.0.1'
        """
        data_msg = self.config.get(section_name, option_name)
        return data_msg

    def set_value(self, section_name, option_name, value):
        """
        设置相关的值
        :param section_name: 节点名称
        :param option_name: 选项名称
        :param value: 选项对应的值
        :return:
        """
        self.config.set(section_name, option_name, value)
        # 举例： config.set("baseconf", 'host', 192.168.1.1)


# def parse_connection(connection_values):
#     conn_format = connection_values.rsplit('@', 1)
#     user, passwd = conn_format[0].split(":")
#     host, port = conn_format[1].split(":")
#     connection = {
#         "user": user,
#         "host": host,
#         "port": int(port),
#         "passwd": passwd
#     }
#     return connection

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

def write_checkpoint(filename,info):
    '''
        info = {
       'hostname':
       'checkpoint':
    }
    '''

    with open("%s.checkpoint" % filename, 'w') as f:
        f.write(json.dumps(info, indent=2).strip())

def get_checkpoint(filename):
    '''
    info = {
       'hostname':
       'checkpoint':
    }
    '''
    filename = "%s.checkpoint" % filename
    if not os.path.exists(filename):
        return DEFAULT_DATE
    try:
        with open(filename) as f:
            info = f.read()
        info = json.loads(info)
    except:
        return DEFAULT_DATE
    return info['checkpoint']


def get_mysql_slow_infos(instance_name,conn_config):
    '''
    parameters:
    @conn_setttings: user:password@hostname:port

    steps:
    1. 执行获取信息语句
    2. 对数据进行加工打印
    3. 获取执行计划
    rows[
        start_time,
	    db_user,
        client_ip,
        query_time,
        lock_time,
        rows_sent,
        rows_examined,
        db_name,
        sql_text,
        thread_id]

	print data[
        start_time
        instance_name --new
        db_name
        client_ip
        db_user
        query_time
        lock_time
        rows_sent
        rows_examined
        sql_fingerprint -- new
        sql_text
        thread_id
        tbl_names -- new
        sql_plan -- new
        sql_fingerprint_md5 --new
    ]
    '''
    conn = pymysql.connect(**conn_config)
    datas = get_rows(conn,query=SLOW_SQL)
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
            continue
        data["start_time"] = data["start_time"].timestamp()
        data["tbl_names"] = tbl_names.lower()
        data["sql_fingerprint"] = sql_fingerprint
        data["instance_name"] = instance_name
        data["sql_fingerprint_md5"]  = get_md5(sql_fingerprint)
        data["query_time"] = data["query_time"].total_seconds()*1000
        data["lock_time"] = data["lock_time"].total_seconds()*1000
        if (data["db_name"] == '' or data["db_name"] is None):
            data["db_name"] = tbl_names.split('.')[0]
        print(json.dumps(data))

def generate_pid(instance_name):
    instance_name_pid = str(os.getpid())
    pidfile = "/tmp/%s.pid" % instance_name

    if os.path.exists(pidfile):
        if psutil.pid_exists(int(open(pidfile, 'r').readlines()[0])):
            # print("%s job already exists, exiting" % instance_name)
            sys.exit(-1)
        else:
            delete_pid(instance_name)
    with open(pidfile,'w') as f:
        f.write(instance_name_pid)

def delete_pid(instance_name):
    pidfile = "/tmp/%s.pid" % instance_name
    if os.path.exists(pidfile):
        os.remove(pidfile)

def rotate_slow_log(conn_config):
    conn = pymysql.connect(**conn_config)
    row = get_row(conn,query=ROTATE_SLOW_LOG)


def get_md5(query):
    if isinstance(query,str):
        query = query.encode('utf-8')
        md = hashlib.md5()
        md.update(query)
    return md.hexdigest()

@click.command()
@click.option('--instance_name',help='mysql/tidb instance name')
@click.option('--conf', default='conf/analyze_slow_log.conf' ,help='config file, need abpath')
def main(instance_name,conf):
    '''
    一个实例类型仅能同时跑一个进程
    1. 判断释放存在pid文件，有退出
    2. 生产pid 文件
    3. 执行轮换慢日志
    4. 获取mysql慢日志

    问题
    1. 密码加密
    2. sql_plan的获取方式

    python3 analyze_slow_log.py --instance_name=dev-master
    python3 analyze_slow_log.py --instance_name=dev-master --conf=/tmp/tmp.conf
    '''

    if conf == 'conf/analyze_slow_log.conf':
        dirname, filename = os.path.split(os.path.abspath(__file__))
        conf =  "%s%sconf%sanalyze_slow_log.conf" % (dirname,os.sep,os.sep)
        try:
            config = Config(ini_file_path=conf)
            host = config.get_value(section_name=instance_name, option_name='host')
            user = config.get_value(section_name=instance_name, option_name='user')
            port = int(config.get_value(section_name=instance_name, option_name='port'))
            password = config.get_value(section_name=instance_name, option_name='passwrod')
            sql_plan_host = config.get_value(section_name=instance_name, option_name='sql_plan_host')
            connection = {
                "user": user,
                "host": host,
                "port": port,
                "passwd": password
            }
            if sql_plan_host != '':
                plan_connection = {
                    "user": user,
                    "host": host,
                    "port": port,
                    "passwd": password
                }
        except:
            # print('please check you config file')
            sys.exit(-1)
    generate_pid(instance_name=instance_name)
    rotate_slow_log(conn_config=connection)
    get_mysql_slow_infos(instance_name=instance_name, conn_config=connection)


main()
