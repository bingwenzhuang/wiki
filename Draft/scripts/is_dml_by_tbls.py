#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import random
import queue
import time
import threading
import click
import sys
import configparser
from pymysqlreplication import BinLogStreamReader
from pymysqlreplication.row_event import DeleteRowsEvent, UpdateRowsEvent, WriteRowsEvent

'''
Queue
'''
q = queue.Queue()

'''
Common util
'''

DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

def     verbose(message):
    print("--%s: %s" % (time.strftime(DATE_FORMAT,time.localtime()),message))


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


'''
Config util
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


'''
Producer
'''


class Producer(threading.Thread):
    def __init__(self, conn_settings, databases, tables):
        super().__init__()
        self.conn_settings = conn_settings
        self.databases = databases
        self.tables = tables

    def run(self):
        server_id = random.randint(99999, 999999999)
        table_change_cnt = dict()
        stream = BinLogStreamReader(connection_settings=self.conn_settings \
                                    , server_id=server_id \
                                    , blocking=True \
                                    , only_events=[DeleteRowsEvent, WriteRowsEvent, UpdateRowsEvent] \
                                    , resume_stream=True \
                                    , only_schemas=self.databases \
                                    , only_tables=self.tables)

        for binlog_event in stream:
            if isinstance(binlog_event, (DeleteRowsEvent, WriteRowsEvent, UpdateRowsEvent)):
                table_name = "%s.%s" % (binlog_event.schema, binlog_event.table)
                table_change_cnt[table_name] = table_change_cnt.get(table_name,0) + 1

                q.put(table_change_cnt)

'''
consumer
'''

class Consumer(threading.Thread):
    def __init__(self,interval):
        super().__init__()
        self.interval = interval


    def run(self):
        while True:
            if not q.empty():
                table_change_cnt = q.get()
                verbose(str(table_change_cnt))
            else:
                verbose("Not Found Change")
            time.sleep(self.interval)

@click.command()
@click.option('--section_name',help='configure file section')
@click.option('--conf', default='conf/cut_over.conf' ,help='config file, need abpath')
def main(section_name,conf):
    try:
        config = Config(ini_file_path=conf)
        host = config.get_value(section_name=section_name, option_name='host')
        user = config.get_value(section_name=section_name, option_name='user')
        port = int(config.get_value(section_name=section_name, option_name='port'))
        password = config.get_value(section_name=section_name, option_name='passwrod')
        databases = config.get_value(section_name=section_name, option_name='databases')
        tables = config.get_value(section_name=section_name, option_name='tables')
        interval = config.get_value(section_name=section_name, option_name='interval')
    except:
        sys.exit(-1)

    conn_settings = {
        "user": user,
        "host": host,
        "port": port,
        "passwd": password
    }

    databases = databases.split(',')
    tables = tables.split(',')

    producer = Producer(conn_settings=conn_settings,databases=databases,tables=tables)
    producer.start()

    consumer = Consumer(interval=int(interval))
    consumer.start()

'''
python3 is_dml_by_tbls.py --section_name=is-dml-dev
print: 打印累计变动数据量
'''
main()


