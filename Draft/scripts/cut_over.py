#!/usr/bin/env python3
# -*- coding: utf-8 -*-

'''
cut-over progrocess

parameter1: table_name
parameter2: ghost_table_name

1. session1
create table `_b_20190908220120_del` (
   id int auto_increment primary key
) engine=InnoDB comment='ghost-cut-over-sentry';

2. session1
  lock tables b write, `_b_20190908220120_del` write;


3. session2
   set session lock_wait_timeout=1

   rename table `test`.`b` to `test`.`_b_20190908220120_del`, `test`.`_b_gho` to `test`.`b`
   rename table `pupu_main`.`stockquantitylogs` to `pupu_main`.`_stockquantitylogs_20190908220120_del`,`pupu_main`.`stockquantitylogs_bak` to `pupu_main`.`stockquantitylogs`,
   `pupu_main`.`stockquantitylogs_tmp` to `pupu_main`.`stockquantitylogs_bak`;

4. session1
   在这步监听ghost表是否还在写入，如果在写入，则等待超时退出
   drop table if exists `test`.`_b_20190908220120_del`;
   unlock tables;

5. Done.

6. 需要一些前期的pre-check 譬如check 表是否存在
   第三步骤是多线程，并且执行完毕后需要通知主线程
'''

import datetime,json,configparser,os,sys
import time
import requests
import click
from requests.auth import HTTPBasicAuth
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from threading import Thread,Event


IS_EXECPTION = False
CREATE_SAENTRY_SQL = ''' create table `%s`.`%s` (id int auto_increment primary key) engine = InnoDB comment='cut-over-sentry' '''
CREATE_TBL_TMP_SQL = ''' create table `%s`.`%s` like `%s`.`%s` '''

LOCK_TBL_SQL = ''' lock table  `%s`.`%s` write, `%s`.`%s` write '''

SET_TIMEOUT_SQL = ''' set session lock_wait_timeout=%s  '''
GET_TIMEOUT_VAR = ''' select @@session.lock_wait_timeout as lock_wait_timeout '''
RENAME_SQL = ''' rename table `%s`.`%s` to `%s`.`%s`,
`%s`.`%s` to `%s`.`%s`,
`%s`.`%s` to `%s`.`%s` '''

DROP_TBLS  = ''' drop table if exists `%s`.`%s` '''
UNLOCK_TBL_SQL = ''' unlock tables '''


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


def verbose(message):
    print("-- %s" % message)

def get_row(session, sql):
    verbose('Thread ID [%s] : %s' % (session.connection().connection.thread_id(),sql))
    result = session.execute(sql)
    if result.rowcount > 0:
        return result.fetchone()[0]
    else:
        return 0

def rename_opers(session,sql,started_evt):
    global IS_EXECPTION
    print
    started_evt.set()
    try:
        get_row(session,sql)
    except:
        IS_EXECPTION =True
        raise


def get_mq_unconsumed_cnt(queue_name,username,password,host,port=15672):
    url = 'http://%s:%s/api/queues' % (host,port)
    res = requests.get(url = url ,auth = HTTPBasicAuth(username=username,password=password))
    if res.status_code == 200:
        queues = json.loads(res.text)
        for q in queues:
            if q['name'].lower() == queue_name.lower():
                return int(q.get("messages", 0))
    return -1

@click.command()
@click.option('--instance_name',help='mysql/tidb instance name')
@click.option('--mq_name',help='rabbitmq instance name')
@click.option('--db_name',help='database name')
@click.option('--table_name',help='table name')
@click.option('--lock_wait_timeout', default=1, type=int ,help='config file, need abpath')
def main(instance_name,mq_name,db_name,table_name,lock_wait_timeout):
    '''
    python3 cut_over.py --instance_name=dev-master --mq_name=dev-rabbitmq --db_name=tmp --table_name=order_canceld --lock_wait_timeout=2

    python3 cut_over.py --instance_name=prd-master --mq_name=sim-rabbitmq --db_name=pupu_main --table_name=logisticsroutes --lock_wait_timeout=2

    select from_unixtime(max(time_update)/1000),from_unixtime(min(time_update)/1000) from logisticsroutes_bak;

    数据完整型检验(缺失数据):
    select a.* from logisticsroutes_bak a left join logisticsroutes b on a.id=b.id where b.id is null;

    '''
    dirname, filename = os.path.split(os.path.abspath(__file__))
    conf =  "%s%sconf%scut_over.conf" % (dirname,os.sep,os.sep)
    try:
        config = Config(ini_file_path=conf)
        host = config.get_value(section_name=instance_name, option_name='host')
        user = config.get_value(section_name=instance_name, option_name='user')
        port = int(config.get_value(section_name=instance_name, option_name='port'))
        password = config.get_value(section_name=instance_name, option_name='passwrod')

        mq_host = config.get_value(section_name=mq_name, option_name='host')
        mq_user = config.get_value(section_name=mq_name, option_name='user')
        mq_port = int(config.get_value(section_name=mq_name, option_name='port'))
        mq_password = config.get_value(section_name=mq_name, option_name='passwrod')
    except:
        print('please check you config file')
        raise
        sys.exit(-1)

    URL = 'mysql+pymysql://%s:%s@%s:%s/%s' % (user, password, host, port, db_name)
    engine = create_engine(URL)
    Session = sessionmaker(bind=engine)

    session1 = Session()
    session2 = Session()


    try:

        started_evt = Event()
        saentry_table_name = '%s_%s' % (table_name,str(int(time.time())))
        tmp_table_name = '%s_tmp' % table_name
        arch_table_name = 'arch_%s_%s' % (table_name, datetime.datetime.strftime(datetime.datetime.now(), '%Y%m%d%H%M%S'))
        get_row(session1, CREATE_SAENTRY_SQL % (db_name, saentry_table_name))
        get_row(session1, CREATE_TBL_TMP_SQL % (db_name, tmp_table_name, db_name, table_name))
        get_row(session1, LOCK_TBL_SQL % (db_name, saentry_table_name, db_name, table_name))
        get_row(session2, SET_TIMEOUT_SQL % lock_wait_timeout)
        verbose('Thread ID [%s] :  current session lock_wait_timeout variable is %s' % (session2.connection().connection.thread_id(),get_row(session2, GET_TIMEOUT_VAR)))
        ## 子线程执行，执行完毕后通知
        t = Thread(target=rename_opers,args=(session2,RENAME_SQL % (db_name, table_name, db_name, arch_table_name , db_name , table_name +'_bak',db_name , table_name ,db_name, tmp_table_name,db_name,table_name + '_bak'),started_evt))
        t.start()
        started_evt.wait()
        ## 判断数据是否已经应用完了没(比较难) 需要确保lock wait time out session1 有释放锁
        ## 需要获取mq 队列的消息条数为0才能执行
        cnt = get_mq_unconsumed_cnt(queue_name=table_name, username=mq_user, password=mq_password,host=mq_host, port=mq_port)
        print("-- Rabbitmq have %s unconsume and child exception is %s " % (cnt, IS_EXECPTION))
        while cnt != 0 and IS_EXECPTION is False:
            cnt = get_mq_unconsumed_cnt(queue_name=table_name, username=mq_user, password=mq_password,host=mq_host, port=mq_port)
            print("-- Rabbitmq have %s unconsume and child exception is %s " % (cnt, IS_EXECPTION))

    except Exception as e:
        print("happen exception")
    finally:
        ## 删除魔幻表
        get_row(session1, DROP_TBLS % (db_name, saentry_table_name))
        ## 释放锁
        get_row(session1, UNLOCK_TBL_SQL)
        # session1.commit()
        # session2.commit()
        ## 删除临时表
        get_row(session1, DROP_TBLS % (db_name, tmp_table_name))




main()
