#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys,os,socket,re
import base64
import argparse
import sh
import cassandra
import oss2
import threading
from boto3.session import Session
from boto3.s3.transfer import TransferConfig
from cassandra.cluster import Cluster
from cassandra.auth import PlainTextAuthProvider

TIMEOUT = 120.0
FETCH_SIZE = 100
DOT_EVERY = 1000
CONCURRENT_BATCH_SIZE = 1000


class NodeTool(object):
    def __init__(self,host,user,password,port,keyspaces,dir,filename,protocol_version,skip_schema):
        self.host = host
        self.user = user
        self.password = str(base64.b64decode(password),'utf-8') if password is not None else None
        self.port = port
        self.protocol_version = protocol_version
        self.keyspaces = keyspaces
        self.dir = dir
        self.filename = filename
        self.skip_schema = skip_schema
        self.exclude_keyspaces = ['system', 'system_traces', 'system_schema', 'system_auth', 'system_distributed']
        self.restore_dirname = self._get_restore_dirname()
        self.session = self._init_cluster()
        self._init_paras()

    def _get_restore_dirname(self):
        sh.cd("%s" % self.dir)
        sh.tar('-zxvf',self.filename)
        sh.rm('-rf',self.filename)
        return self.filename.split('.')[0]

    def _init_cluster(self):
        ssl_opts = {}
        connect_timeout = 10
        nodes = [self.host]
        if self.user and self.password:
            auth = PlainTextAuthProvider(username=self.user, password=self.password)
            cluster = Cluster(control_connection_timeout=connect_timeout, connect_timeout=connect_timeout,contact_points=nodes, port=self.port, protocol_version=self.protocol_version, auth_provider=auth,load_balancing_policy=cassandra.policies.WhiteListRoundRobinPolicy(nodes),ssl_options=ssl_opts)
        else:
            cluster = Cluster(control_connection_timeout=connect_timeout, connect_timeout=connect_timeout, contact_points=nodes, port=self.port, load_balancing_policy=cassandra.policies.WhiteListRoundRobinPolicy(nodes), ssl_options=ssl_opts)
        session = cluster.connect()
        session.default_timeout = TIMEOUT
        session.default_fetch_size = FETCH_SIZE
        session.row_factory = cassandra.query.ordered_dict_factory
        return session

    def _init_paras(self):
        if self.keyspaces == 'all':
            sh.cd("%s/%s" % (self.dir,self.restore_dirname))
            results = sh.ls()
            keyspaces = []
            for k_list in results.splitlines():
                if len(k_list) > 0:
                    keyspaces = keyspaces + k_list.split()
            for keyname in self.exclude_keyspaces:
                if keyname in keyspaces:
                    keyspaces.remove(keyname)
            self.keyspaces = keyspaces
        else:
            self.keyspaces = self.keyspaces.split(',')

    def _parse_sqlfile(self,fname):
        statement = ""
        sqls = []
        for line in open(fname,'r',encoding="utf-8"):
            if re.match(r'--', line):  # ignore sql comment lines
                continue
            if not re.search(r';$', line):  # keep appending lines that don't end in ';'
                statement = statement + line
            else:
                statement = statement + line
                try:
                    sqls.append(statement)
                except:
                    print("parse sql script file %s failed" % fname)
                    raise
                statement = ""
        return sqls

    def _dropkeyspace(self,keyspace):
        print("drop schema for %s" % keyspace)
        try:
            self.session.execute(query='drop keyspace IF EXISTS %s' % keyspace)
        except:
            print("drop schema for %s failed" % keyspace)
            raise

    def _createkeyspace(self,keyspace):
        print("create schema for %s" % keyspace)
        try:
            fname = "%s/%s/%s/%s" % (self.dir,self.restore_dirname,keyspace,'schema.sql')
            sqls = self._parse_sqlfile(fname=fname)
            for sql in sqls:
                self.session.execute(query=sql)
        except:
            print("create schema for %s failed" % keyspace)
            raise

    def _sstableloader(self,keyspace):
        print("sstableloader schema for %s" % keyspace)
        try:
            sh.cd("%s/%s/%s" % (self.dir, self.restore_dirname, keyspace))
            results = sh.ls()
            tables = []
            for tb_list in results.splitlines():
                if len(tb_list) > 0:
                    tables = tables + tb_list.split()
            if 'schema.sql' in tables:
                tables.remove('schema.sql')
            for table in tables:
                print("sstableloader table %s.%s" % (keyspace,table))
                try:
                    if self.user and self.password:
                        results = sh.sstableloader('-v','-u',self.user,'-pw',self.password,'-p',self.port,'-d',self.host,"%s/%s/%s/%s" % (self.dir, self.restore_dirname, keyspace,table))
                    else:
                        results = sh.sstableloader('-v','-p',self.port,'-d',self.host,"%s/%s/%s/%s" % (self.dir, self.restore_dirname, keyspace,table))
                    print(results)
                except:
                    print("sstableloader table %s.%s failed" % (keyspace,table))
                    raise
        except:
            print("sstableloader schema for %s failed" % keyspace)
            raise

    def restore(self):
        for keyspace in self.keyspaces:
            if self.skip_schema == 0:
                self._dropkeyspace(keyspace)
                self._createkeyspace(keyspace)
            self._sstableloader(keyspace)
            print("Finish %s keysapce restore" % keyspace)
        sh.rm('-rf', "%s/%s" % (self.dir, self.restore_dirname))


class ProgressPercentage(object):

    def __init__(self, filename):
        self._filename = filename
        self._size = float(os.path.getsize(filename))
        self._seen_so_far = 0
        self._lock = threading.Lock()

    def __call__(self, bytes_amount):
        # To simplify, assume this is hooked up to a single filename
        with self._lock:
            self._seen_so_far += bytes_amount
            percentage = (self._seen_so_far / self._size) * 100
            sys.stdout.write(
                "\r%s  %s / %s  (%.2f%%)" % (
                    self._filename, self._seen_so_far, self._size,
                    percentage))
            sys.stdout.flush()

def s3_download(session,filename,key,bucket):
    config = TransferConfig(multipart_threshold=1024 * 25, max_concurrency=10, multipart_chunksize=1024 * 25,use_threads=True)
    s3_client = session.client("s3")
    s3_client.download_file(bucket,key,filename,Config=config,Callback=ProgressPercentage(filename))

# oss 断点续传下载
def oss_resum_download(buck,key,filename):
    oss2.resumable_download(buck, key, filename ,store=oss2.ResumableDownloadStore(root='/tmp'),multiget_threshold=20 * 1024 * 1024,part_size=10 * 1024 * 1024,num_threads=2)

def download_cloud_storage(type,region,bucket,accessid,accesskey,localdir,key):
    print("Start download %s from %s cloud storage" % (key, type))
    sh.mkdir("-p",localdir)
    sh.cd("%s" % localdir)
    _, filename = os.path.split(key)
    if type == "oss":
        auth = oss2.Auth(accessid, accesskey)
        buck = oss2.Bucket(auth, 'http://oss-%s.aliyuncs.com' % region, bucket)
        # buck.get_object_to_file(key=key, filename=filename)
        oss_resum_download(buck=buck,key=key,filename=filename)
    elif type == "s3":
        session = Session(aws_access_key_id=accessid, aws_secret_access_key=accesskey, region_name=region)
        s3_download(session=session,filename=filename,key=key,bucket=bucket)
        # s3 = session.client("s3")
        # s3.download_file(Filename=filename, Key=key, Bucket=bucket)
    else:
        print("Invalid cloud storage type")
        sys.exit(1)
    print("End download %s/%s.tar.gz" % (localdir, filename))
    return localdir,filename

def main():
    parser = argparse.ArgumentParser(description='cassandra backup on local by nodetool snapshot')
    parser.add_argument('--host', '-n', help='cassandra host ip address,default=hostname', default=None)
    parser.add_argument('--port', '-P', help='cassandra client port,default=9042', type=int, default=9042)
    parser.add_argument('--user', '-u', help='cassandra username', default=None)
    parser.add_argument('--password', '-p', help='cassandra user passwrod,need base64 encode', default=None)
    parser.add_argument('--keyspaces', '-k', help='need backup or restore keyspace names,default=all(not contain system,system_traces keyspaces)', default='all')
    parser.add_argument('--dir', help="backup and restore files dir", default='/opt/cassandra/data')
    parser.add_argument('--type',help="cloud storage type,s3 or oss,default=oss",default='oss')
    parser.add_argument('--region', help="cloud storage region", default=None)
    parser.add_argument('--bucket', help="cloud storage bucket", default=None)
    parser.add_argument('--key', help="cloud storage bucket key", default=None)
    parser.add_argument('--accessid', help="cloud storage access id,need base64 encode", default=None)
    parser.add_argument('--accesskey', help="cloud storage access key,need base64 encode", default=None)
    parser.add_argument('--protocol-version', help='set protocol version (required for authentication)', type=int,default=3)
    parser.add_argument('--skip-schema', help='skip create schema',type=int,default=0)
    args = parser.parse_args()

    for arg in vars(args):
        if getattr(args, arg) == 'None':
            setattr(args, arg, None)

    if args.host is None:
        hostname = socket.gethostname()
    else:
        hostname = args.host

    if args.region is None or args.bucket is None or args.accessid is None or args.accesskey is None or args.key is None:
        print("[region-bucket-accessid-accesskey] args must set value")
        sys.exit(1)

    try:
        accessid = str(base64.b64decode(args.accessid), 'utf-8')
        accesskey = str(base64.b64decode(args.accesskey), 'utf-8')
        localdir,filename = download_cloud_storage(type=args.type, region=args.region, bucket=args.bucket, accessid=accessid,accesskey=accesskey, localdir=args.dir, key=args.key)
        node = NodeTool(host=hostname,user=args.user,password=args.password,port=args.port,keyspaces=args.keyspaces,dir=localdir,filename=filename,protocol_version=args.protocol_version,skip_schema=args.skip_schema)
        node.restore()
    except Exception as e:
        print('ERROR:', e)
        sys.exit(1)

main()
