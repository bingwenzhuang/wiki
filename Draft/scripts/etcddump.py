#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys,argparse
import oss2
from boto3.session import Session
from boto3.s3.transfer import TransferConfig
import etcd3
import json
import threading,os
import base64
from kubernetes import client, config
from datetime import datetime,timezone


def get_secret(namespace,secretname):
    v1 = client.CoreV1Api()
    ret = v1.read_namespaced_secret(namespace=namespace, name=secretname)
    return str(base64.b64decode(ret.data['accessid']), 'utf-8'),str(base64.b64decode(ret.data['accesskey']), 'utf-8')

def parse_args(parser, commands):
    split_argv = [[]]
    for c in sys.argv[1:]:
        if c in commands.choices:
            split_argv.append([c])
        else:
            split_argv[-1].append(c)
    args = argparse.Namespace()
    for c in commands.choices:
        setattr(args, c, None)
    parser.parse_args(split_argv[0], namespace=args)
    for argv in split_argv[1:]:
        n = argparse.Namespace()
        setattr(args, argv[0], n)
        parser.parse_args(argv, namespace=n)
    return args


def entry_from_result(entry,leaseInfo):
    return {
        'key': entry[1].key.decode('utf-8'),
        'value': entry[0].decode('utf-8'),
        'lease_info': {
                    'id': leaseInfo.ID,
                    'ttl': leaseInfo.TTL,
                    'grantedttl': leaseInfo.grantedTTL
                  }
    }

def get_etcd_client(host='localhost', port=2379,
           ca_cert=None, cert_key=None, cert_cert=None, timeout=None,
           user=None, password=None, grpc_options=None):
    return etcd3.client(host=host,port=port,ca_cert=ca_cert,cert_key=cert_key,cert_cert=cert_cert,timeout=timeout,user=user,password=password,grpc_options=grpc_options)

def dumper(paras):
    client = get_etcd_client(host=paras.host,port=paras.port,ca_cert=paras.ca_cert,cert_key=paras.cert_key,cert_cert=paras.cert_cert,timeout=paras.timeout,user=paras.user,password=paras.password,grpc_options=paras.grpc_options)
    # 一致性读是 Linearizable
    datas = client.get_all()
    dumplist = []
    for data in datas:
        leaseInfo = client.get_lease_info(lease_id=data[1].lease_id)
        row = entry_from_result(entry=data,leaseInfo=leaseInfo)
        if paras.file:
            dumplist.append(row)
        else:
            print(json.dumps(row))

    if paras.file:
        filename = "%s_%s.json" % (os.path.splitext(paras.file)[0],datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S"))
        with open(filename, 'w') as f:
            json.dump(dumplist, f)

    return filename

def restore(paras,dumpfile):
    client = get_etcd_client(host=paras.host,port=paras.port,ca_cert=paras.ca_cert,cert_key=paras.cert_key,cert_cert=paras.cert_cert,timeout=paras.timeout,user=paras.user,password=paras.password,grpc_options=paras.grpc_options)
    if dumpfile:
        with open(dumpfile,'rb') as f:
            datas = json.load(f)
    else:
        print("Please input dump file")
        sys.exit(1)

    for data in datas:
        if data['lease_info']['ttl'] >0:
            leaseInfo = client.lease(ttl=data['lease_info']['ttl'])
            client.put(key=data['key'],value=data['value'],lease=leaseInfo.id)
        else:
            client.put(key=data['key'], value=data['value'])

    print('completed restore etcd from %s' % dumpfile)

# oss 断点续传下载
def oss_resum_download(buck,key,filename):
    oss2.resumable_download(buck, key, filename ,store=oss2.ResumableDownloadStore(root='/tmp'),multiget_threshold=20 * 1024 * 1024,part_size=10 * 1024 * 1024,num_threads=2)

def s3_download(session,filename,key,bucket):
    config = TransferConfig(multipart_threshold=1024 * 25, max_concurrency=10, multipart_chunksize=1024 * 25,use_threads=True)
    s3_client = session.client("s3")
    s3_client.download_file(bucket,key,filename,Config=config,Callback=ProgressPercentage(filename))

def download_cloud_storage(type,region,bucket,accessid,accesskey,key):
    print("Start download %s from %s cloud storage" % (key, type))
    _, filename = os.path.split(key)
    if type == "oss":
        auth = oss2.Auth(accessid, accesskey)
        buck = oss2.Bucket(auth, 'http://oss-%s.aliyuncs.com' % region, bucket)
        oss_resum_download(buck=buck,key=key,filename=filename)
    elif type == "s3":
        session = Session(aws_access_key_id=accessid, aws_secret_access_key=accesskey, region_name=region)
        s3_download(session=session,filename=filename,key=key,bucket=bucket)
    else:
        print("Invalid cloud storage type")
        sys.exit(1)
    print("End download %s" % (filename))
    return filename

# oss 断点续传
def oss_resum_upload(buck,keyname,filename):
    oss2.resumable_upload(buck, keyname, filename ,store=oss2.ResumableStore(root='/tmp'),num_threads=2)

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

def upload_cloud_storage(type,region,bucket,accessid,accesskey,filename,path):
    print("Start upload %s to %s cloud storage" % (filename,type))
    filename_without_path = os.path.basename(filename)
    keyname = '%s/%s' % (path, filename_without_path)
    if type == "oss":
        auth = oss2.Auth(accessid, accesskey)
        buck = oss2.Bucket(auth, 'http://oss-%s.aliyuncs.com' % region, bucket)
        # 默认采用断电续传的方式
        oss_resum_upload(buck=buck,keyname=keyname,filename=filename)
    elif type == "s3":
        session = Session(aws_access_key_id=accessid, aws_secret_access_key=accesskey, region_name=region)
        s3_upload(session=session,filename=filename,keyname=keyname,bucket=bucket)
    else:
        print("Invalid cloud storage type")
        sys.exit(1)
    print("End upload")

def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(title='sub-commands')

    dumper_parser = commands.add_parser('dump')
    dumper_parser.add_argument('--host', help='etcd host ip address,default=None', default=None)
    dumper_parser.add_argument('--port',help='etcd client port,default=2379',type=int,default=2379)
    dumper_parser.add_argument('--ca_cert',help='etcd ca cert,default=None', default=None)
    dumper_parser.add_argument('--cert_key',help='etcd cert key,default=None', default=None)
    dumper_parser.add_argument('--cert_cert',help='etcd cert,default=None', default=None)
    dumper_parser.add_argument('--timeout',help='etcd connection timeout ,default=10', type=int, default=10)
    dumper_parser.add_argument('--user', help='etcd user name,default=None',default=None)
    dumper_parser.add_argument('--password',help='etcd password,default=None',default=None)
    dumper_parser.add_argument('--grpc_options',help='etcd grpc parameter config,default=None',default=None)
    dumper_parser.add_argument('--file',help='File where the dump is located',default=None)
    dumper_parser.add_argument('--type', help="cloud storage type,s3 or oss,default=oss", default='oss')
    dumper_parser.add_argument('--region', help="cloud storage region", default=None)
    dumper_parser.add_argument('--bucket', help="cloud storage bucket", default=None)
    dumper_parser.add_argument('--ns', help="k8s namespace name,default=test", default='test')
    dumper_parser.add_argument('--secret', help="secret name for accessid,accesskey,default=etcd-bk-secret", default='etcd-bk-secret')
    dumper_parser.add_argument('--upath',help='cloud storage file path,default=etcd/dump/',default='etcd/dump/')
    dumper_parser.add_argument('--upload', help="whether to upload to the cloud,default=False", default=False,action='store_true')

    restore_parser = commands.add_parser('restore')
    restore_parser.add_argument('--host', help='etcd host ip address,default=None', default=None)
    restore_parser.add_argument('--port',help='etcd client port,default=2379',type=int,default=2379)
    restore_parser.add_argument('--ca_cert',help='etcd ca cert,default=None', default=None)
    restore_parser.add_argument('--cert_key',help='etcd cert key,default=None', default=None)
    restore_parser.add_argument('--cert_cert',help='etcd cert,default=None', default=None)
    restore_parser.add_argument('--timeout',help='etcd connection timeout ,default=10', type=int, default=10)
    restore_parser.add_argument('--user', help='etcd user name,default=None',default=None)
    restore_parser.add_argument('--password',help='etcd password,default=None',default=None)
    restore_parser.add_argument('--file', help='Directly recover local file,default=None', default=None)
    restore_parser.add_argument('--grpc_options',help='etcd grpc parameter config,default=None',default=None)
    restore_parser.add_argument('--type', help="cloud storage type,s3 or oss,default=oss", default='oss')
    restore_parser.add_argument('--region', help="cloud storage region", default=None)
    restore_parser.add_argument('--bucket', help="cloud storage bucket", default=None)
    restore_parser.add_argument('--key', help="cloud storage bucket key", default=None)
    restore_parser.add_argument('--ns', help="k8s namespace name,default=test", default='test')
    restore_parser.add_argument('--secret', help="secret name for accessid,accesskey,default=etcd-bk-secret", default='etcd-bk-secret')
    args = parse_args(parser, commands)

    try:
        if args.dump:
            dumpfile = dumper(args.dump)
            if args.dump.upload:
                config.load_incluster_config()
                accessid , accesskey =get_secret(namespace=args.dump.ns,secretname=args.dump.secret)
                upload_cloud_storage(type=args.dump.type, region=args.dump.region, bucket=args.dump.bucket,accessid=accessid, accesskey=accesskey, filename=dumpfile,path=args.dump.upath)
        if args.restore:
            if args.restore.file:
                dumpfile = args.restore.file
            else:
                config.load_incluster_config()
                accessid, accesskey = get_secret(namespace=args.dump.ns, secretname=args.dump.secret)
                dumpfile = download_cloud_storage(type=args.restore.type, region=args.restore.region,bucket=args.restore.bucket, accessid=accessid, accesskey=accesskey,key=args.restore.key)

            restore(args.restore, dumpfile)
    except Exception as e:
        print('ERROR:', e)
        sys.exit(1)

main()