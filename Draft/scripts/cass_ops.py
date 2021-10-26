#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys,base64,socket,re
import argparse
import sh
import time
from kubernetes import client, config
from kubernetes.stream import stream
from kubernetes.client.api import core_v1_api
import cassandra
from cassandra.cluster import Cluster
from cassandra.auth import PlainTextAuthProvider
import threading

TIMEOUT = 120.0
FETCH_SIZE = 100
DOT_EVERY = 1000
CONCURRENT_BATCH_SIZE = 1000
BK_RESULTS = {}

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


def get_secret(namespace,secretname):
    v1 = client.CoreV1Api()
    ret = v1.read_namespaced_secret(namespace=namespace, name=secretname)
    return str(base64.b64decode(ret.data['cassandra-user']), 'utf-8'),ret.data['cassandra-pw'],str(base64.b64decode(ret.data['jmx-user']), 'utf-8'),ret.data['jmx-pw'],ret.data['accessid'],ret.data['accesskey']

def get_sts_num(namespace,sts):
    v2 = client.AppsV1Api()
    ret = v2.read_namespaced_stateful_set(name=sts, namespace=namespace)
    return ret.spec.replicas

def backup(paras,podname):
    global BK_RESULTS
    user,password,jmxuser,jmxpassword,accessid,accesskey = get_secret(namespace=paras.ns,secretname=paras.secret)
    # sts_num = get_sts_num(namespace=paras.ns,sts=paras.sts)
    core_v1 = core_v1_api.CoreV1Api()
    exec_command = [
        'bash',
        '-c',
        '/usr/bin/python3 /cass_backup.py --host=%s --port=%s --user=%s --password=%s --keyspaces=%s --jmxusername=%s --jmxpassword=%s --jmxport=%s --ns=%s --dir=%s --datadir=%s --type=%s --region=%s --bucket=%s --accessid=%s --accesskey=%s --upload;echo $?' % (paras.host,paras.port,user,password,paras.keyspaces,jmxuser,jmxpassword,paras.jmxport,paras.ns,paras.dir,paras.datadir,paras.type,paras.region,paras.bucket,accessid,accesskey)]
    ret = stream(core_v1.connect_get_namespaced_pod_exec, podname, paras.ns, command=exec_command, container=paras.container,stderr=True,stdin=False, stdout=True, tty=False)
    print("Response: " + ret)
    succ_flag = int(ret.splitlines()[-1])
    if succ_flag != 0:
        # failed
        BK_RESULTS[podname] = 1
    else:
        BK_RESULTS[podname] = 0

def restore(paras,key,skip_schema):
    user,password,jmxuser,jmxpassword,accessid,accesskey = get_secret(namespace=paras.ns,secretname=paras.secret)
    ret = sh.bash('-c',
            '/usr/bin/python3 /cass_restore.py --host=%s --port=%s --user=%s --password=%s --keyspaces=%s --dir=%s --type=%s --region=%s --bucket=%s --key=%s --accessid=%s --accesskey=%s --skip-schema=%s' % (paras.host,paras.port,user,password,paras.keyspaces,paras.dir,paras.type,paras.region,paras.bucket,key,accessid,accesskey,skip_schema))
    print(ret)


def ready(hostname,port):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        ret = sock.connect_ex((hostname, port))
        if ret == 0:
            print("%s:%s access..." % (hostname, str(port)))
            return True
        else:
            print("%s:%s not access..." % (hostname, str(port)))
            return False
    except:
        print("%s:%s not exists..." % (hostname, str(port)))
        return False

def parse_sqlfile(fname):
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

def init_cluster(paras,user,password):
    ssl_opts = {}
    connect_timeout = 10
    nodes = [paras.host]
    if paras.pwauth:
        auth = PlainTextAuthProvider(username=user, password=password)
        cluster = Cluster(control_connection_timeout=connect_timeout, connect_timeout=connect_timeout,
                          contact_points=nodes, port=paras.port, protocol_version=paras.protocol_version,
                          auth_provider=auth, load_balancing_policy=cassandra.policies.WhiteListRoundRobinPolicy(nodes),
                          ssl_options=ssl_opts)
    else:
        cluster = Cluster(control_connection_timeout=connect_timeout, connect_timeout=connect_timeout,
                          contact_points=nodes, port=paras.port,
                          load_balancing_policy=cassandra.policies.WhiteListRoundRobinPolicy(nodes),
                          ssl_options=ssl_opts)
    session = cluster.connect()
    session.default_timeout = TIMEOUT
    session.default_fetch_size = FETCH_SIZE
    session.row_factory = cassandra.query.ordered_dict_factory
    return session

def init(paras):
    if paras.host is None:
        print("init host is not None")
        sys.exit(1)

    user, password, _, _, _, _ = get_secret(namespace=paras.ns, secretname=paras.secret)
    if user.lower() == 'cassandra':
        print("super user name is cassandra name,please modify super user name")
        sys.exit(1)

    # podname = "%s-%d" % (paras.sts,0)
    while True:
        if ready(hostname=paras.host,port=paras.port):
            break

    session = init_cluster(paras=paras,user='cassandra',password='cassandra')
    sql = '''CREATE USER IF NOT EXISTS '%s' WITH PASSWORD '%s' SUPERUSER''' % (user,str(base64.b64decode(password),'utf-8'))
    session.execute(query=sql)
    print("create user %s success" % user)
    print("permissions_validity_in_ms")
    time.sleep(paras.permissions_time*2)
    session1 = init_cluster(paras=paras, user=user, password=str(base64.b64decode(password),'utf-8'))
    dropsql = '''DROP USER IF EXISTS cassandra'''
    session1.execute(query=dropsql)
    print("drop default super %s success" % user)
    if paras.file:
        sqls = parse_sqlfile(fname=paras.file)
        for sql in sqls:
            session1.execute(query=sql)
            print("execute sql %s success" % sql[:15])
        print("init sql scripts success for %s " % paras.file)

def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(title='sub-commands')

    init_parser = commands.add_parser('init')
    init_parser.add_argument('--host', '-n', help='cassandra host ip address,default=hostname', default=None)
    init_parser.add_argument('--port', '-P', help='cassandra client port,default=9042', type=int, default=9042)
    init_parser.add_argument('--ns', help="k8s namespace name,default=cassandra", default='cassandra')
    init_parser.add_argument('--sts', help='cassandra statefulset name,default=cassandra', default='cassandra')
    init_parser.add_argument('--secret', help="secret name for cassandra-user,cassandra-pw,jmx-user,jmx-pw,accessid,accesskey,default=cass-secret", default='cass-secret')
    init_parser.add_argument('--protocol-version', help='set protocol version (required for authentication)', type=int,default=3)
    init_parser.add_argument('--file', help='cassandra init sql script file', default=None)
    init_parser.add_argument('--permissions-time', help='permissions validity time,default 2s', type=int,default=2)
    init_parser.add_argument('--pwauth', help="cassandra whether PasswordAuthenticator and cassandraauthorizer,default=False", default=False,action='store_true')

    backup_parser = commands.add_parser('backup')
    backup_parser.add_argument('--host', '-n', help='cassandra host ip address,default=hostname', default=None)
    backup_parser.add_argument('--port', '-P', help='cassandra client port,default=9042', type=int, default=9042)
    backup_parser.add_argument('--keyspaces', '-k',help='need backup or restore keyspace names,default=all(not contain system,system_traces keyspaces)',default='all')
    backup_parser.add_argument('--jmxport', help='cassandra jmx port,default=7199', type=int, default=7199)
    backup_parser.add_argument('--ns', help="k8s namespace name,default=cassandra", default='cassandra')
    backup_parser.add_argument('--dir', help="backup and restore files dir", default='/opt/cassandra/data')
    backup_parser.add_argument('--datadir', help="cassandra data dir", default='/opt/cassandra/data/data')
    backup_parser.add_argument('--type', help="cloud storage type,s3 or oss,default=oss", default='oss')
    backup_parser.add_argument('--region', help="cloud storage region", default=None)
    backup_parser.add_argument('--bucket', help="cloud storage bucket", default=None)
    backup_parser.add_argument('--sts', help='cassandra statefulset name,default=cassandra', default='cassandra')
    backup_parser.add_argument('--container', help='cassandra statefulset pod container name,default=cass', default='cass')
    backup_parser.add_argument('--secret', help="secret name for cassandra-user,cassandra-pw,jmx-user,jmx-pw,accessid,accesskey,default=cass-secret", default='cass-secret')
    backup_parser.add_argument('--protocol-version', help='set protocol version (required for authentication)', type=int,default=3)

    restore_parser = commands.add_parser('restore')
    restore_parser.add_argument('--host', '-n', help='cassandra host ip address,default=hostname', default=None)
    restore_parser.add_argument('--port', '-P', help='cassandra client port,default=9042', type=int, default=9042)
    restore_parser.add_argument('--ns', help="k8s namespace name,default=cassandra", default='cassandra')
    restore_parser.add_argument('--keyspaces', '-k',help='need backup or restore keyspace names,default=all(not contain system,system_traces keyspaces)',default='all')
    restore_parser.add_argument('--dir', help="backup and restore files dir", default='/opt/cassandra/data')
    restore_parser.add_argument('--type', help="cloud storage type,s3 or oss,default=oss", default='oss')
    restore_parser.add_argument('--region', help="cloud storage region", default=None)
    restore_parser.add_argument('--bucket', help="cloud storage bucket", default=None)
    restore_parser.add_argument('--keys', help="cloud storage bucket keys,separated by commas", default=None)
    restore_parser.add_argument('--secret', help="secret name for cassandra-user,cassandra-pw,jmx-user,jmx-pw,accessid,accesskey,default=cass-secret", default='cass-secret')
    restore_parser.add_argument('--protocol-version', help='set protocol version (required for authentication)', type=int,default=3)

    config.load_incluster_config()
    args = parse_args(parser, commands)
    if args.backup:
        # 兼容旧版本
        args.backup.host = None
        sts_num = get_sts_num(namespace=args.backup.ns, sts=args.backup.sts)
        for index in range(sts_num):
            podname = "%s-%d" % (args.backup.sts,index)
            t = threading.Thread(target=backup, args=(args.backup,podname,))
            t.start()
        while len(threading.enumerate()) != 1:
            time.sleep(30)
        if 1 in BK_RESULTS.values():
            print(BK_RESULTS)
            sys.exit(1)
    if args.restore:
        skip_schema = 0
        for key in args.restore.keys.split(','):
            restore(args.restore,key,skip_schema)
            skip_schema=1
    if args.init:
        init(args.init)

main()
