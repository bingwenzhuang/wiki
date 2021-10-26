#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import re
import argparse
from datetime import datetime, timezone
from rdbtools import RdbParser,MemoryCallback

STRING_ESCAPE_RAW = 'raw'
STRING_ESCAPE_PRINT = 'print'
STRING_ESCAPE_UTF8 = 'utf8'
STRING_ESCAPE_BASE64 = 'base64'
ESCAPE_CHOICES = [STRING_ESCAPE_RAW, STRING_ESCAPE_PRINT, STRING_ESCAPE_UTF8, STRING_ESCAPE_BASE64]

class PrintAllKeys(object):
    def __init__(self,redis_name,report_name,big_key_len,big_key_value, module_list):
        self._big_key_len = big_key_len
        self._big_key_value = big_key_value
        self._module_list = module_list
        self._redis_name = redis_name
        self._now = datetime.now(timezone.utc)
        if report_name is None:
            self._report_name = '%s-%s' %(redis_name,self._now.strftime("%Y%m%d%H%M%S"))
        else:
            self._report_name = report_name

    def next_record(self, record):
        '''
            start_time 记录时间戳
            report_name 报告名称
            redis_name redis集群名称
            database 数据库名称
            type 数据类型
            key 键名称
            size_in_bytes 内存使用量(包含键，值和其他开销)
            encoding 内部编码结构
            num_elements 元素个数(string是指长度/hash、set、zset、list等是指个数)
            len_largest_element 最大元素的长度
            expiry 过期时间
            module 模块名称
            is_key_violation Key是否违反规范
            is_big_key 是否为bigkey
            is_value_expiry 是否设置过期时间
        '''
        if record.key is None:
            return  # some records are not keys (e.g. dict)

        if record.expiry:
            is_expiry = 1
            is_not_expiry = 0
        else:
            is_expiry = 0
            is_not_expiry = 1

        is_key_violation,module = self.is_key_violation(record.key)
        is_big_key = self.is_big_key(record.type,record.bytes,record.size,record.len_largest_element)

        data = record._asdict()
        data['expiry'] = record.expiry.isoformat() if record.expiry else '1970-01-01T00:00:00.000000'
        data['is_expiry'] = is_expiry
        data['is_not_expiry'] = is_not_expiry
        data['is_key_violation'] = is_key_violation
        data['is_big_key'] = is_big_key
        data['module'] = module
        data['redis_name'] = self._redis_name
        data['report_name'] = self._report_name
        data['start_time'] = self._now.timestamp()
        print(json.dumps(data))


    def is_key_violation(self,key):
        '''
        内部：
            mwhset mcu:k1 f1 v1 f2 v2
            mw:mwhash:hs:{xxx}:xxx
            mw:mwhash:he:{xxx}:xxx
            mw:mwhash:ws:{xxx}:xxx
            mw:mwhash:we:{xxx}:xxx

            mw:mwhash:hs:xxx:{xxx}:xxx
            mw:mwhash:he:xxx:{xxx}:xxx
            mw:mwhash:ws:xxx:{xxx}:xxx
            mw:mwhash:we:xxx:{xxx}:xxx

            mwlock <ns>mcu:xxx 10 Keys lockname1 lockname2
            {key}distribute_lock_id
            {key}lockname1
            {key}lockname2
        外部SDK:
            redisson_xxxx{Key}
            {Key}xxx
        常规:
        :param key:
        :return:
        '''
        is_key_violation = 1
        module = 'nil'
        pattern1 = '(^redisson_.*{.*?})|(^{.*?})'
        pattern2 = '(^mw:mwhash:hs:)|(^mw:mwhash:he:)|(^mw:mwhash:ws:)|(^mw:mwhash:we:)'
        match1 = re.match(pattern1,key)
        match2 = re.match(pattern2,key)
        if match1:
            module = re.findall('[{](.*?)[}]', match1.group(0))[0].split(':')[0]
        elif match2:
            module = key.split(":")[3].lstrip("{").rstrip("}")
        else:
            module = key.split(':')[0]

        if module in self._module_list:
            is_key_violation = 0

        return is_key_violation,module

    def is_big_key(self,type,bytes,num_elements,len_largest_element):
        '''
        字符串类型，value不大于10KB
        hash、list、set、zset元素个数不超过5000
        :param num_elements: 元素个数
        :param len_largest_element: 最大元素的长度
        :return: (num_elements or len_largest_element) >= self._max_big_len
        '''
        is_big_key = 0
        if type == 'string' and bytes >= self._big_key_value:
            is_big_key = 1
        elif (num_elements > self._big_key_len or len_largest_element > self._big_key_value):
            is_big_key = 1

        return is_big_key


def main():
    parser = argparse.ArgumentParser(description='analyze redis rdb file')
    parser.add_argument('--redis_name', help='redis cluster name,default=redis-cluster', default='redis-cluster')
    parser.add_argument('--report_name', help='redis rdb report name,default=redis_name-<YYYYMMDDHH24MISS>', default=None)
    parser.add_argument('--module_list', help='redis prefix whitelist,please separated by commas', default='ymall,ytms,ypush,userm,phonebookm,devem,meetingctrl,notim,uss,mdmigrator')
    parser.add_argument('--big_key_len', help='definition of the number of big key elements,default=5000', type = int, default= 5000)
    parser.add_argument('--big_key_value', help='definition of the size of big key elements for string type,default=10240', type= int, default=10240)
    parser.add_argument('--rdb_files', help='rdb files,default=rdb.0,rdb.1,rdb.2',default='rdb.0,rdb.1,rdb.2')
    args = parser.parse_args()

    module_list = args.module_list.split(',')
    printTool = PrintAllKeys(redis_name=args.redis_name,report_name=args.report_name,big_key_len=args.big_key_len,big_key_value=args.big_key_value,module_list=module_list)
    callback = MemoryCallback(printTool,64, string_escape=STRING_ESCAPE_RAW)
    parser = RdbParser(callback)
    for rdb_file in args.rdb_files.split(','):
        parser.parse(rdb_file)

main()
