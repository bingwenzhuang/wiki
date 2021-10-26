#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys,os,socket
import base64
import argparse
import sh
import cassandra
import oss2
import threading
from datetime import datetime,timezone
from boto3.session import Session
from boto3.s3.transfer import TransferConfig
from cassandra.cluster import Cluster
from cassandra.auth import PlainTextAuthProvider
from oss2 import SizedFileAdapter, determine_part_size
from oss2.models import PartInfo

TIMEOUT = 120.0
FETCH_SIZE = 100
DOT_EVERY = 1000
CONCURRENT_BATCH_SIZE = 1000

class NodeTool(object):
    def __init__(self,host,port,user,password,keyspaces,jmxusername,jmxpassword,jmxport,tag,dir,protocol_version,datadir = '/opt/cassandra/data/data'):
        print("Init NodeTool")
        self.host = host
        self.port = port
        self.user = user
        self.protocol_version = protocol_version
        self.password = str(base64.b64decode(password),'utf-8') if password is not None else None
        self.keyspaces = keyspaces
        self.jmxusername = jmxusername
        self.jmxpassword = str(base64.b64decode(jmxpassword),'utf-8') if jmxpassword is not None else None
        self.jmxport = jmxport
        self.tag = tag
        self.dir = dir
        self.datadir = datadir
        self.exclude_keyspaces = ['system','system_traces','system_schema','system_auth','system_distributed']
        self.session = self._init_cluster()
        self._init_paras()

    def _init_paras(self):
        print("Init NodeTool args")
        if self.tag is None:
            if self.keyspaces == 'all':
                self.tag = "bk_all_%s" % datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
            else:
                self.tag = "bk_part_%s" % datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        if self.keyspaces == 'all':
            self.keyspaces = self._getallkeyspaces()
        else:
            self.keyspaces = self.keyspaces.split(',')
        sh.mkdir('-p',self.dir)
        sh.chown('-R','cassandra.cassandra',self.dir)

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

    def _getallkeyspaces(self):
        try:
            keyspaces = list(self.session.cluster.metadata.keyspaces.keys())
            for keyname in self.exclude_keyspaces:
                if keyname in keyspaces:
                    keyspaces.remove(keyname)
            return keyspaces
        except:
            print("get all keyspaces failed")
            raise

    def _makemetadata(self,keyspace):
        print("make schema meta data %s" % keyspace)
        try:
            sh.mkdir('-p', "%s/%s/%s" % (self.dir, self.tag, keyspace))
            sh.chown('-R', 'cassandra.cassandra', "%s/%s/%s" % (self.dir, self.tag, keyspace))
            ret = self.session.cluster.metadata.keyspaces.get(keyspace)
            write_sql_file(filename=("%s/%s/%s/%s" % (self.dir, self.tag, keyspace,'schema.sql')), info=ret.export_as_string())
        except:
            print("make schema meta data %s failed" % keyspace)
            raise

    def _makesnapshot(self,keyspace):
        print("make snapshot %s" % keyspace)
        try:
            if self.jmxusername and self.jmxpassword:
                sh.nodetool('-u', self.jmxusername, '-pw', self.jmxpassword, '-h', self.host, '-p', self.jmxport,
                            'snapshot', '-t', self.tag, keyspace)
            else:
                sh.nodetool('-h', self.host, '-p', self.jmxport, 'snapshot', '-t', self.tag, keyspace)
        except:
            print("make snapshot %s failed" % keyspace)
            raise


    def _clearsnapshot(self,keyspace):
        print("clear snapshot %s" % keyspace)
        try:
            if self.jmxusername and self.jmxpassword:
                sh.nodetool('-u', self.jmxusername, '-pw', self.jmxpassword, '-h', self.host, '-p', self.jmxport,
                            'clearsnapshot', '-t', self.tag, keyspace)
            else:
                sh.nodetool('-h', self.host, '-p', self.jmxport, 'clearsnapshot', '-t', self.tag, keyspace)
        except:
            print("clear snapshot %s failed" % keyspace)
            raise

    def _package_backup_file(self):
        print("package backup file")
        try:
            sh.mkdir('-p', "%s/%s" % (self.dir, self.tag))
            sh.chown('-R', 'cassandra.cassandra', "%s/%s" % (self.dir, self.tag))
            dirs = sh.find(self.datadir,'-name',self.tag).splitlines()
            for dir in dirs:
                for root,_,files in os.walk(dir):
                    for filename in files:
                        file = os.path.join(root,filename)
                        root_arr = root.split('/')[:-2]
                        tablename = root_arr[-1].split('-')[0]
                        keyspace = root_arr[-2]
                        sh.mkdir('-p', "%s/%s/%s/%s" % (self.dir, self.tag,keyspace,tablename))
                        sh.chown('-R', 'cassandra.cassandra', "%s/%s/%s/%s" % (self.dir, self.tag,keyspace,tablename))
                        sh.cp(file,"%s/%s/%s/%s/%s" % (self.dir, self.tag,keyspace,tablename,filename))
            sh.cd("%s" % self.dir)
            sh.tar('-czf',"%s.tar.gz" % self.tag, self.tag, '--remove-files')
        except:
            print("package backup file failed for %s" % self.tag)
            raise
        print("package backup file %s/%s.tar.gz" % (self.dir, self.tag))
        return self.dir , "%s.tar.gz" % self.tag

    def backup(self):
        for keyspace in self.keyspaces:
            self._makesnapshot(keyspace)
            self._makemetadata(keyspace)
        filename = self._package_backup_file()
        for keyspace in self.keyspaces:
            self._clearsnapshot(keyspace)
        return filename

def write_sql_file(filename,info):
    with open(filename, 'a',encoding="utf-8") as f:
        f.write(info)

# oss 普通上传
def oss_upload(buck,keyname,filename):
    buck.put_object_from_file(keyname, filename)

# oss 断点续传
def oss_resum_upload(buck,keyname,filename):
    oss2.resumable_upload(buck, keyname, filename ,store=oss2.ResumableStore(root='/tmp'),num_threads=2)

# oss 分片上传
def oss_part_upload(buck,keyname,filename):
    total_size = os.path.getsize(filename)
    part_size = determine_part_size(total_size, preferred_size=100 * 1024)
    upload_id = buck.init_multipart_upload(keyname).upload_id
    parts = []
    with open(filename, 'rb') as fileobj:
        part_number = 1
        offset = 0
        while offset < total_size:
            num_to_upload = min(part_size, total_size - offset)
            # 调用SizedFileAdapter(fileobj, size)方法会生成一个新的文件对象，重新计算起始追加位置。
            result = buck.upload_part(keyname, upload_id, part_number,SizedFileAdapter(fileobj, num_to_upload))
            parts.append(PartInfo(part_number, result.etag))
            offset += num_to_upload
            part_number += 1
    with open(filename, 'rb') as fileobj:
        assert buck.get_object(keyname).read() == fileobj.read()

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

def s3_upload(session,filename,keyname,bucket):
    config = TransferConfig(multipart_threshold=1024*25, max_concurrency=10, multipart_chunksize=1024*25, use_threads=True)
    s3_client = session.client("s3")
    # ExtraArgs={'ACL': 'public-read', 'ContentType': 'video/mp4'}
    s3_client.upload_file(filename, bucket, keyname,Config=config,Callback=ProgressPercentage(filename))

def upload_cloud_storage(type,region,bucket,accessid,accesskey,filedir,filename,namespace,host):
    print("Start upload %s to %s cloud storage" % (('%s/%s/%s' % (namespace,host,filename)),type))
    sh.cd("%s" % filedir)
    keyname = '%s/%s/%s' % (namespace, host, filename)
    if type == "oss":
        auth = oss2.Auth(accessid, accesskey)
        buck = oss2.Bucket(auth, 'http://oss-%s.aliyuncs.com' % region, bucket)
        # 默认采用断电续传的方式
        oss_resum_upload(buck=buck,keyname=keyname,filename=filename)
    elif type == "s3":
        session = Session(aws_access_key_id=accessid, aws_secret_access_key=accesskey, region_name=region)
        s3_upload(session=session,filename=filename,keyname=keyname,bucket=bucket)
        # s3 = session.client("s3")
        # s3.upload_file(Filename=filename, Key=keyname, Bucket=bucket)
    else:
        print("Invalid cloud storage type")
        sys.exit(1)
    sh.rm('-rf',filename)
    print("End upload")


def main():
    parser = argparse.ArgumentParser(description='cassandra backup on local by nodetool snapshot')
    parser.add_argument('--host', '-n', help='cassandra host ip address,default=hostname', default=None)
    parser.add_argument('--port', '-P', help='cassandra client port,default=9042', type=int, default=9042)
    parser.add_argument('--user', '-u', help='cassandra username', default=None)
    parser.add_argument('--password', '-p', help='cassandra user passwrod,need base64 encode', default=None)
    parser.add_argument('--keyspaces', '-k', help='need backup or restore keyspace names,default=all(not contain system,system_traces keyspaces)', default='all')
    parser.add_argument('--jmxusername', help='cassandra jmx username', default=None)
    parser.add_argument('--jmxpassword', help='cassandra jmx password,need base64 encode', default=None)
    parser.add_argument('--jmxport', help='cassandra jmx port,default=7199', type=int, default=7199)
    parser.add_argument('--tag', help='snapshot tag,default=None', default=None)
    parser.add_argument('--ns',help="k8s namespace name,default=cassandra",default='cassandra')
    parser.add_argument('--dir', help="backup and restore files dir", default='/opt/cassandra/data')
    parser.add_argument('--datadir', help="cassandra data dir", default='/opt/cassandra/data/data')
    parser.add_argument('--type',help="cloud storage type,s3 or oss,default=oss",default='oss')
    parser.add_argument('--region', help="cloud storage region", default=None)
    parser.add_argument('--bucket', help="cloud storage bucket", default=None)
    parser.add_argument('--accessid', help="cloud storage access id,need base64 encode", default=None)
    parser.add_argument('--accesskey', help="cloud storage access key,need base64 encode", default=None)
    parser.add_argument('--protocol-version', help='set protocol version (required for authentication)', type=int ,default=3)
    parser.add_argument('--upload', help="whether to upload to the cloud,default=False", default=False,action='store_true')
    args = parser.parse_args()

    for arg in vars(args):
        if getattr(args, arg) == 'None':
            setattr(args, arg, None)

    if args.host is None:
        hostname = socket.gethostname()
    else:
        hostname = args.host

    if args.upload:
        if args.region is None or args.bucket is None or args.accessid is None or args.accesskey is None:
            print("[region-bucket-accessid-accesskey] args must set value")
            sys.exit(1)

    try:
        node = NodeTool(host=hostname,port=args.port,user=args.user,password=args.password,keyspaces=args.keyspaces,jmxusername=args.jmxusername,jmxpassword=args.jmxpassword,jmxport=args.jmxport,tag=args.tag,dir=args.dir,protocol_version=args.protocol_version,datadir=args.datadir)
        bkdir , bkfilename = node.backup()
        if args.upload:
            accessid=str(base64.b64decode(args.accessid),'utf-8')
            accesskey = str(base64.b64decode(args.accesskey), 'utf-8')
            upload_cloud_storage(type=args.type,region=args.region,bucket=args.bucket,accessid=accessid,accesskey=accesskey,filedir = bkdir ,filename=bkfilename,namespace=args.ns,host=socket.gethostname())
    except Exception as e:
        print('ERROR:', e)
        sys.exit(1)

main()
