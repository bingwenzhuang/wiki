#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import struct
import datetime
import os
import click
import MySQLdb

DML_INSERT_TYPE = 'insert'
DML_UPDATE_TYPE = 'update'
DML_DELETE_TYPE = 'delete'

'''
create table `binlog_trxs`(
`id` bigint(20) NOT NULL AUTO_INCREMENT,
 `trxnum` bigint(20),
`filename` varchar(100) not null,
`bgtime` bigint(20) not null,
`edtime` bigint(20) not null,
`trxsec` int(11) not null default 0,
`atops` bigint(20) not null default 0,
`endops` bigint(20) not null default 0,
`dmlcnt` int(11) not null default 0,
PRIMARY KEY (`id`) ,
key `idx_trxsec_filename` (`trxsec`,`filename`)
);



create table `binlog_tbl_events`(
`id` bigint(20) NOT NULL AUTO_INCREMENT,
`filename` varchar(100) not null,
`tblname` varchar(100) not null,
`bgtime` bigint(20) not null,
`edtime` bigint(20) not null,from enum import Enum, unique
`dmltype` varchar(20) not null,
PRIMARY KEY (`id`) ,
key `idx_tblname_filename` (`tblname`,`filename`)
);
'''

delete_binlog_trxs = "delete from binlog_trxs where filename ='%s' "
insert_binlog_trxs = "INSERT INTO binlog_trxs(trxnum,filename,bgtime, edtime, trxsec, atops,endops,dmlcnt) VALUES "

delete_binlog_tbl_events = "delete from binlog_tbl_events where filename = '%s'"
insert_binlog_tbl_events = "INSERT INTO binlog_tbl_events(filename,tblname,bgtime, edtime,dmltype) VALUES "

# Constants from PyMYSQL source code
NULL_COLUMN = 251
UNSIGNED_CHAR_COLUMN = 251
UNSIGNED_SHORT_COLUMN = 252
UNSIGNED_INT24_COLUMN = 253
UNSIGNED_INT64_COLUMN = 254
UNSIGNED_CHAR_LENGTH = 1
UNSIGNED_SHORT_LENGTH = 2
UNSIGNED_INT24_LENGTH = 3
UNSIGNED_INT64_LENGTH = 8


JSONB_TYPE_SMALL_OBJECT = 0x0
JSONB_TYPE_LARGE_OBJECT = 0x1
JSONB_TYPE_SMALL_ARRAY = 0x2
JSONB_TYPE_LARGE_ARRAY = 0x3
JSONB_TYPE_LITERAL = 0x4
JSONB_TYPE_INT16 = 0x5
JSONB_TYPE_UINT16 = 0x6
JSONB_TYPE_INT32 = 0x7
JSONB_TYPE_UINT32 = 0x8
JSONB_TYPE_INT64 = 0x9
JSONB_TYPE_UINT64 = 0xA
JSONB_TYPE_DOUBLE = 0xB
JSONB_TYPE_STRING = 0xC
JSONB_TYPE_OPAQUE = 0xF

JSONB_LITERAL_NULL = 0x0
JSONB_LITERAL_TRUE = 0x1
JSONB_LITERAL_FALSE = 0x2


def byte2int(b):
    if isinstance(b, int):
        return b
    else:
        return struct.unpack("!B", b)[0]


def int2byte(i):
    return struct.pack("!B", i)

class binlog_events(object):
    UNKNOWN_EVENT = 0
    START_EVENT_V3 = 1
    QUERY_EVENT = 2
    STOP_EVENT = 3
    ROTATE_EVENT = 4
    INTVAR_EVENT = 5
    LOAD_EVENT = 6
    SLAVE_EVENT = 7
    CREATE_FILE_EVENT = 8
    APPEND_BLOCK_EVENT = 9
    EXEC_LOAD_EVENT = 10
    DELETE_FILE_EVENT = 11
    NEW_LOAD_EVENT = 12
    RAND_EVENT = 13
    USER_VAR_EVENT = 14
    FORMAT_DESCRIPTION_EVENT = 15
    XID_EVENT = 16
    BEGIN_LOAD_QUERY_EVENT = 17
    EXECUTE_LOAD_QUERY_EVENT = 18
    TABLE_MAP_EVENT = 19
    PRE_GA_WRITE_ROWS_EVENT = 20
    PRE_GA_UPDATE_ROWS_EVENT = 21
    PRE_GA_DELETE_ROWS_EVENT = 22
    WRITE_ROWS_EVENT = 23
    UPDATE_ROWS_EVENT = 24
    DELETE_ROWS_EVENT = 25
    INCIDENT_EVENT = 26
    HEARTBEAT_LOG_EVENT = 27
    IGNORABLE_LOG_EVENT = 28
    ROWS_QUERY_LOG_EVENT = 29
    WRITE_ROWS_EVENT = 30
    UPDATE_ROWS_EVENT = 31
    DELETE_ROWS_EVENT = 32
    GTID_LOG_EVENT = 33
    ANONYMOUS_GTID_LOG_EVENT = 34
    PREVIOUS_GTIDS_LOG_EVENT = 35


class ReadBinlog(object):
    BINLOG_FILE_HEADER = b'\xFE\x62\x69\x6E'
    query_event_fix_part_length = 13
    table_map_event_fix_part_length = 8
    event_header_length = 19

    def __init__(self, filename, start_position=4, trx_sec=0, is_details=False, is_out_file=True, out_tb=None):
        self.start_position = start_position
        self.filename = filename
        self.file_bytes_data = open(filename, 'rb')
        self.__check_binlog_filename()
        self.table_dml_events = []
        self.trxs_statices = []
        self.tables_statices = {}
        self.trx_sec = trx_sec
        self.is_details = is_details
        self.out_tb = out_tb
        self.is_out_file = is_out_file
        self.db_name = ''
        self.table_name = ''

    def __check_binlog_filename(self):
        binlog_file_header = self.file_bytes_data.read(4)
        if binlog_file_header != self.BINLOG_FILE_HEADER:
            print('error : Is not a standard binlog file format')
            exit()

    def __tables_statices(self):
        for table_dml_event in self.table_dml_events:
            table_statices = self.tables_statices.get(table_dml_event.table_name,
                                                      TableStatices(table_dml_event.table_name))
            table_statices.add_dml_count(table_dml_event.dml_type)
            self.tables_statices[table_dml_event.table_name] = table_statices

    def __dump(self):

        print ('%-15s %s\n%-15s %s\n%-15s %s\n%-15s %s\n' % ("Binlog FileName", os.path.basename(self.filename) \
                                                                , "Binlog version", self.binlog_file_header[0] \
                                                                , "Server version", self.binlog_file_header[1] \
                                                                , "Create version", self.binlog_file_header[2] ))

        trxs_f = None
        dmls_f = None
        if self.is_out_file:
            trxs_f = open("{}_trxs.log".format(os.path.basename(self.filename)),'w')
            dmls_f = open("{}_dmls.log".format(os.path.basename(self.filename)),'w')


        print ("[Transaction Total:]")
        for trx in self.trxs_statices:
            if trx.trx_id is None:
                self.trxs_statices.remove(trx)
                continue
            if trx.execute_time >= self.trx_sec:
                trx.dump(f=trxs_f)

        print ("[Tables Total:]")
        for key, value in self.tables_statices.items():
            value.dump()

        if self.is_details:
            print ("[Tables Details:]")
            for dml_event in self.table_dml_events:
                dml_event.dump(f=dmls_f)

        if self.out_tb is not None:
            host = self.out_tb.get('host', '')
            user = self.out_tb.get('user', '')
            password = self.out_tb.get('password', '')
            port = int(self.out_tb.get('port', '3306'))
            dbname = self.out_tb.get('dbname', 'tmp')
            conn = MySQLdb.connect(
                host=host,
                user=user,
                passwd=password,
                port=port,
                db=dbname)

            cursor = conn.cursor()

            try:
                binlog_trxs_values = ''
                binlog_dm_events_values = ''
                cursor.execute(delete_binlog_trxs % os.path.basename(self.filename))
                cursor.execute(delete_binlog_tbl_events % os.path.basename(self.filename))
                for trx in self.trxs_statices:
                    if trx.trx_id is None:
                        self.trxs_statices.remove(trx)
                        continue
                    if trx.execute_time >= self.trx_sec:
                        binlog_trxs_values += "(%s,'%s',%s,%s,%s,%s,%s,%s)," % (
                            trx.trx_id, os.path.basename(self.filename), trx.start_time, trx.end_time, trx.execute_time,
                            trx.start_position, trx.end_postion, trx.dml_count)

                        # cursor.execute(insert_binlog_trxs % (trx.trx_id,os.path.basename(self.filename),trx.start_time,trx.end_time,trx.execute_time,trx.start_position,trx.end_postion,trx.dml_count))

                for dml_event in self.table_dml_events:
                    binlog_dm_events_values += "('%s','%s',%s,%s,'%s')," % (
                        os.path.basename(self.filename), dml_event.table_name, dml_event.start_time, dml_event.end_time,
                        dml_event.dml_type)

                    # cursor.execute(insert_binlog_tbl_events % (os.path.basename(self.filename),dml_event.table_name,dml_event.start_time,dml_event.end_time,dml_event.dml_type))

                if binlog_trxs_values != '':
                    cursor.execute(insert_binlog_trxs + binlog_trxs_values[:-1])

                if binlog_dm_events_values != '':
                    cursor.execute(insert_binlog_tbl_events + binlog_dm_events_values[:-1])

                conn.commit()
            except:
                print("db exception")
                raise

        if trxs_f is not None:
            trxs_f.close()

        if dmls_f is not None:
            dmls_f.close()


    def read(self):
        while True:
            event_header = self.file_bytes_data.read(self.event_header_length)
            if len(event_header) == 0:
                break

            timestamp, event_type, server_id, event_size, end_pos, flags = self.__read_event_header(event_header)
            event_body = self.file_bytes_data.read(event_size - self.event_header_length)
            self.__read_event_body(event_type, timestamp, self.start_position, end_pos, event_body)
            self.start_position = end_pos

        self.__tables_statices()
        self.__dump()

    def __read_event_body(self, event_type, timestamp, start_ops, end_pos, event_body):

        if event_type == binlog_events.FORMAT_DESCRIPTION_EVENT:
            self.binlog_file_header = self.__read_format_desc_event(event_body)

        elif event_type == binlog_events.QUERY_EVENT:
            self.__read_query_event(event_body)
            trx = TrxStatices(start_time=timestamp, start_position=start_ops)
            self.trxs_statices.append(trx)

        elif event_type == binlog_events.TABLE_MAP_EVENT:
            self.db_name, self.table_name = self.__read_table_map_event(event_body)
            self.trxs_statices[-1].tables_name_list.add("{}.{}".format(self.db_name, self.table_name))

        elif event_type == binlog_events.XID_EVENT:
            trx_id = self.__read_xid_event(event_body)
            self.trxs_statices[-1].trx_id = trx_id
            self.trxs_statices[-1].end_time = timestamp
            self.trxs_statices[-1].end_postion = end_pos

        elif event_type == binlog_events.DELETE_ROWS_EVENT:

            change_cnt = self.__read_rows_event(event_body,binlog_events.DELETE_ROWS_EVENT)
            self.trxs_statices[-1].dml_count += change_cnt

            table_dml_event = TableDMLEvent(start_time=timestamp,table_name="{}.{}".format(self.db_name, self.table_name))
            table_dml_event.end_time = timestamp
            table_dml_event.dml_type = DML_DELETE_TYPE
            self.table_dml_events.append(table_dml_event)

        elif event_type == binlog_events.UPDATE_ROWS_EVENT:

            change_cnt = self.__read_rows_event(event_body,binlog_events.UPDATE_ROWS_EVENT)
            self.trxs_statices[-1].dml_count += change_cnt

            table_dml_event = TableDMLEvent(start_time=timestamp,table_name="{}.{}".format(self.db_name, self.table_name))
            table_dml_event.end_time = timestamp
            table_dml_event.dml_type = DML_UPDATE_TYPE
            self.table_dml_events.append(table_dml_event)

        elif event_type == binlog_events.WRITE_ROWS_EVENT:

            change_cnt = self.__read_rows_event(event_body,binlog_events.WRITE_ROWS_EVENT)
            self.trxs_statices[-1].dml_count += change_cnt

            table_dml_event = TableDMLEvent(start_time=timestamp,table_name="{}.{}".format(self.db_name, self.table_name))
            table_dml_event.end_time = timestamp
            table_dml_event.dml_type = DML_INSERT_TYPE
            self.table_dml_events.append(table_dml_event)

        elif event_type == binlog_events.GTID_LOG_EVENT:
            self.__read_gtid_event(event_body)

    def __read_event_header(self, event_header):
        timestamp, event_type, server_id, event_size, log_pos, flags = struct.unpack('=IBIIIH', event_header)
        return timestamp, event_type, server_id, event_size, log_pos, flags

    def __read_format_desc_event(self, event_body):
        binlog_ver, = struct.unpack('H', event_body[:2])
        server_ver, = struct.unpack('50s', event_body[2:52])
        create_time, = struct.unpack('I', event_body[52:56])
        return binlog_ver, server_ver, create_time

    def __read_query_event(self, event_body):
        '''
        fix_part = 13:
            thread_id : 4bytes
            execute_seconds : 4bytes
            database_length : 1bytes
            error_code : 2bytes
            variable_block_length : 2bytes
        variable_part :
            variable_block_length = fix_part.variable_block_length
            database_name = fix_part.database_length
            sql_statement = event_header.event_length - 19 - 13 - variable_block_length - database_length - 4
         '''
        fix_part = event_body[:self.query_event_fix_part_length]
        thread_id, execute_seconds, database_length, error_code, variable_block_length = struct.unpack('=IIBHH',
                                                                                                       fix_part)
        database_name_bytes = event_body[(self.query_event_fix_part_length + variable_block_length):(
                self.query_event_fix_part_length + variable_block_length + database_length)]
        database_name, = struct.unpack('{}s'.format(database_length), database_name_bytes)
        sql_statment_bytes = event_body[
                             (self.query_event_fix_part_length + variable_block_length + database_length + 1):]
        sql_statement, = struct.unpack('{}s'.format(
            (len(event_body) - (self.query_event_fix_part_length + variable_block_length + database_length + 1))),
            sql_statment_bytes)
        return thread_id, database_name, sql_statement

    def __read_table_map_event(self, event_body):
        '''
        fix_part = 8
            table_id : 6bytes
            Reserved : 2bytes
        variable_part:
            database_name_length : 1bytes
            database_name : database_name_length bytes + 1
            table_name_length : 1bytes
            table_name : table_name_length bytes + 1
            cloums_count : 1bytes
            colums_type_array : one byte per column
            mmetadata_lenth : 1bytes
            metadata : .....(only available in the variable length field，varchar:2bytes，text、blob:1bytes,time、timestamp、datetime: 1bytes
                            blob、float、decimal : 1bytes, char、enum、binary、set: 2bytes(column type id :1bytes metadatea: 1bytes))
            bit_filed : 1bytes
            crc : 4bytes
            .........
        :param event_length:
        :return:
        '''
        # fix_part = event_body[:8]
        database_name_length, = struct.unpack('B', event_body[
                                                   self.table_map_event_fix_part_length:self.table_map_event_fix_part_length + 1])
        database_name, _a, = struct.unpack('{}ss'.format(database_name_length), event_body[(
                                                                                                   self.table_map_event_fix_part_length + 1):(
                                                                                                   self.table_map_event_fix_part_length + 1 + database_name_length + 1)])
        table_name_length, = struct.unpack('B', event_body[
                                                (self.table_map_event_fix_part_length + 1 + database_name_length + 1):(
                                                        self.table_map_event_fix_part_length + 1 + database_name_length + 1 + 1)])
        table_name, _a, = struct.unpack('{}ss'.format(table_name_length), event_body[(
                                                                                             self.table_map_event_fix_part_length + 1 + database_name_length + 1 + 1):(
                                                                                             self.table_map_event_fix_part_length + 1 + database_name_length + 1 + 1 + table_name_length + 1)])

        return database_name, table_name

    def unpack_uint16(self, n):
        return struct.unpack('<H', n[0:2])[0]

    def unpack_int24(self, n):
        try:
            return struct.unpack('B', n[0])[0] \
                + (struct.unpack('B', n[1])[0] << 8) \
                + (struct.unpack('B', n[2])[0] << 16)
        except TypeError:
            return n[0] + (n[1] << 8) + (n[2] << 16)

    def unpack_int64(self, n):
        try:
            return struct.unpack('B', n[0])[0] \
                   + (struct.unpack('B', n[1])[0] << 8) \
                   + (struct.unpack('B', n[2])[0] << 16) \
                   + (struct.unpack('B', n[3])[0] << 24) \
                   + (struct.unpack('B', n[4])[0] << 32)
        except TypeError:
            return n[0] + (n[1] << 8) + (n[2] << 16) + (n[3] << 24) + (n[4] << 32)

    def __read_rows_event(self, event_body,event_type):
        ## lenenc_int length:1
        ## columns-present-bitmap1 length: (num of columns+7)/8
        ## columns-present-bitmap2 length: (num of columns+7)/8

        ## import information:number_of_columns
        total_length = len(event_body)
        remove_length = 1
        number_of_columns = 0
        c = byte2int(event_body[:1])
        if c == NULL_COLUMN:
            return None
        if c < UNSIGNED_CHAR_COLUMN: #251
            remove_length = 1
            number_of_columns = c
        elif c == UNSIGNED_SHORT_COLUMN: #252
            ##read(UNSIGNED_SHORT_LENGTH)
            remove_length = 1+UNSIGNED_SHORT_LENGTH
            number_of_columns = self.unpack_uint16(event_body[1:1+UNSIGNED_SHORT_LENGTH])
        elif c == UNSIGNED_INT24_COLUMN: #253
            ##read(UNSIGNED_INT24_LENGTH)
            remove_length = 1 + UNSIGNED_INT24_LENGTH
            number_of_columns = self.unpack_int24(event_body[1:1+UNSIGNED_INT24_LENGTH])
        elif c == UNSIGNED_INT64_COLUMN: #254
            ##read(UNSIGNED_INT64_LENGTH)
            remove_length = 1 + UNSIGNED_INT64_LENGTH
            number_of_columns = self.unpack_int64(event_body[1:1+UNSIGNED_INT64_LENGTH])

        columns_present_bitmap = (number_of_columns + 7) / 8

        # print self.table_name,columns_present_bitmap,number_of_columns,c
        if event_type == binlog_events.UPDATE_ROWS_EVENT:
            ## UPDATE
            change_cnt = (total_length -remove_length)/(columns_present_bitmap*2)
        elif event_type == binlog_events.DELETE_ROWS_EVENT:
            ## DELETE
            change_cnt = (total_length - remove_length) / columns_present_bitmap
        elif event_type == binlog_events.WRITE_ROWS_EVENT:
            ## INSERT
            change_cnt = (total_length - remove_length) / columns_present_bitmap

        return change_cnt

    def __read_xid_event(self, event_body):
        xid_num, = struct.unpack('Q', event_body[:8])
        return xid_num

    def __read_gtid_event(self, event_body):
        uuid = event_body[1:17]
        gtid = "%s%s%s%s-%s%s-%s%s-%s%s-%s%s%s%s%s%s" % tuple("{0:02x}".format(ord(c)) for c in uuid)
        gno_bytes = event_body[17:25]
        gno_id, = struct.unpack('Q', gno_bytes)
        gtid += ":{}".format(gno_id)
        return gtid


class TrxStatices(object):

    def __init__(self, start_time, start_position, end_postion=None, end_time=None, dml_count=None, trx_id=None):
        self.start_time = start_time
        self.end_time = end_time
        self.start_position = start_position
        self.end_postion = end_postion
        self.trx_id = trx_id
        self.dml_count = 0 if dml_count is None else dml_count
        self.tables_name_list = set()

    @property
    def execute_time(self):
        return (datetime.datetime.utcfromtimestamp(self.end_time) - datetime.datetime.utcfromtimestamp(
            self.start_time)).seconds

    @propertyF
    def format_start_time(self):
        return datetime.datetime.fromtimestamp(self.start_time).isoformat()

    @property
    def format_end_time(self):
        return datetime.datetime.fromtimestamp(self.end_time).isoformat() if self.end_time is not None else 0

    def dump(self,f=None):
        print ("trx_num:{} trx_begin_time:[{}] trx_end_time:[{}] trx_begin_ops:{} trx_end_pos:{} trx_sec:{} trx_dml_count:{} trx_change_tables:[{}]".format(
            self.trx_id, self.format_start_time, self.format_end_time, self.start_position, self.end_postion,
            self.execute_time, self.dml_count, ','.join(self.tables_name_list)))
        if f is not None:
            print >> f, "trx_num:{} trx_begin_time:[{}] trx_end_time:[{}] trx_begin_ops:{} trx_end_pos:{} trx_sec:{} trx_dml_count:{} trx_change_tables:[{}]".format(
                self.trx_id, self.format_start_time, self.format_end_time, self.start_position, self.end_postion,
                self.execute_time, self.dml_count, ','.join(self.tables_name_list))

class TableDMLEvent(object):
    def __init__(self, start_time, table_name, end_time=None, dml_type=None):
        self.start_time = start_time
        self.table_name = table_name
        self.end_time = end_time
        # insert,update,delete
        self.dml_type = dml_type

    @property
    def execute_time(self):
        return (datetime.datetime.utcfromtimestamp(self.end_time) - datetime.datetime.utcfromtimestamp(
            self.start_time)).seconds

    @property
    def format_start_time(self):
        return datetime.datetime.fromtimestamp(self.start_time).isoformat()

    @property
    def format_end_time(self):
        return datetime.datetime.fromtimestamp(self.end_time).isoformat()

    def dump(self,f=None):
        print ("tbl_name:%-60s tbl_begin_time:[%-19s] tbl_end_time:[%-19s] tbl_sec:%-4s tbl_dml_type:%s" % (
            self.table_name, self.format_start_time, self.format_end_time, self.execute_time, self.dml_type))
        if f is not None:
            print >> f, "tbl_name:%-60s tbl_begin_time:[%-19s] tbl_end_time:[%-19s] tbl_sec:%-4s tbl_dml_type:%s" % (
                self.table_name, self.format_start_time, self.format_end_time, self.execute_time, self.dml_type)

class TableStatices(object):
    def __init__(self, table_name, insert_count=None, update_count=None, delete_count=None):
        self.table_name = table_name
        self.insert_count = 0 if insert_count is None else insert_count
        self.update_count = 0 if update_count is None else update_count
        self.delete_count = 0 if delete_count is None else delete_count

    def add_dml_count(self, dml_type):
        if dml_type == DML_INSERT_TYPE:
            self.insert_count += 1
        elif dml_type == DML_UPDATE_TYPE:
            self.update_count += 1
        elif dml_type == DML_DELETE_TYPE:
            self.delete_count += 1

    @property
    def dml_count(self):
        return self.insert_count + self.update_count + self.delete_count

    def dump(self,f=None):
        print ("tbl_name:%-60s tbl_update:%-15s tbl_insert:%-15s tbl_delete:%-15s tbl_dml_count:%-15s" % (
            self.table_name, self.update_count, self.insert_count, self.delete_count, self.dml_count))
        if f is not None:
            print >> f , "tbl_name:%-60s tbl_update:%-15s tbl_insert:%-15s tbl_delete:%-15s tbl_dml_count:%-15s" % (
                self.table_name, self.update_count, self.insert_count, self.delete_count, self.dml_count)

'''
binlog filename
trx_sec
is_details
out_tables=host,user,password,port,dbname,tbl_name

--filename="/Users/bowenz/Downloads/mysql-bin-changelog.177994" --trx_sec=1 --is_details --is_out_file
--filename="/Users/bowenz/Downloads/mysql-bin-changelog.162501" --trx_sec=2
--filename="/Users/bowenz/Downloads/mysql-bin-changelog.162500" --trx_sec=1 --out_db="host=pupu-pre.cq1hwn7a0n9c.rds.cn-north-1.amazonaws.com.cn;user=root;password=qFCSaiEOInwuxyy9;port=3306;dbname=tmp"


--filename="/Users/bowenz/Downloads/mysql-bin.000016" --trx_sec=1
'''


@click.command()
@click.option('--filename', help='MySQL binlog file')
@click.option('--trx_sec', default=0, help="Need Print Trx sec")
@click.option('--is_details', is_flag=True, help='Whether to print table DML evevt')
@click.option('--is_out_file', is_flag=True, help='Whether to print file for output information')
@click.option('--out_db', default=None, help='Whether insert tables')
def main(filename, trx_sec, is_details, is_out_file, out_db):
    d = None if out_db is None else dict(x.split("=") for x in out_db.split(";"))
    readbinlog = ReadBinlog(filename=filename \
                            ,trx_sec=trx_sec \
                            ,is_details=is_details \
                            ,is_out_file=is_out_file \
                            ,out_tb=d)
    readbinlog.read()


main()