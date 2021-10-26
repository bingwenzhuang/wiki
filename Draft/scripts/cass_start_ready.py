#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import socket
import time
import argparse
import os
from kubernetes import client ,config

def main():
    parser = argparse.ArgumentParser(description='cassandra init start tools')
    parser.add_argument('--sts', help='cassandra statefulset name,default=cassandra', default='cassandra')
    parser.add_argument('--stsnum', help='cassandra statefulset replicas,default=3', type=int , default=3)
    parser.add_argument('--ns', help='cassandra statefulset namespace,default=cassandra', default='cassandra')
    parser.add_argument('--port', help='cassandra client port,default=9042',type=int, default=9042)
    parser.add_argument('--loop', help='cassandra int loop,default=6', type=int, default=6)
    parser.add_argument('--period','-c', help='cassandra get peroid, unit second,default=10', type= int, default=10)
    args = parser.parse_args()

    for arg in vars(args):
        if getattr(args, arg) == 'None':
            setattr(args, arg, None)

    hostname = socket.gethostname()
    try:
        index = int(hostname.split("-")[-1])
    except:
        index = 0
    pre_index = index - 1
    next_index = index + 1
    statefulset = args.sts
    namespace = args.ns
    client_port = args.port
    period = args.period
    loop = args.loop
    cnt = 0


    try:
        config.load_incluster_config()
        v2 = client.AppsV1Api()
        ret = v2.read_namespaced_stateful_set(name=statefulset, namespace=namespace)
        statefulset_num = ret.spec.replicas
        print("get %s num is %s by k8s statefulset api on %s" % (statefulset ,str(statefulset_num) ,namespace))
    except:
        print("get %s num failed by k8s statefulset api on %s" % (statefulset,namespace))
        statefulset_num = args.stsnum

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    while True:
        if pre_index >= 0:
            pre_hostname = "%s-%s.%s.%s.svc.cluster.local" % (statefulset,str(pre_index),statefulset,namespace)
            result = sock.connect_ex((pre_hostname, client_port))
            if result == 0:
                print("%s:%s access..." % (pre_hostname, str(client_port)))
                break
            print("%s:%s not access..." %(pre_hostname,str(client_port)))
        if next_index < statefulset_num:
            next_hostname = "%s-%s.%s.%s.svc.cluster.local" % (statefulset, str(next_index), statefulset, namespace)
            try:
                result = sock.connect_ex((next_hostname, client_port))
                if result == 0:
                    print("%s:%s access..." % (next_hostname, str(client_port)))
                    break
                print("%s:%s not access..." % (next_hostname, str(client_port)))
            except socket.gaierror:
                print("%s:%s not exists..." % (next_hostname, str(client_port)))
                if pre_index < 0:
                    print("first node , it skip check pre or next node ready...")
                    break


        time.sleep(period)
        cnt = cnt + 1
        if cnt >= loop:
            print("exceed init time:%s..." % str(period*loop))
            break

env_dist = os.environ
if env_dist.get("CASS_SKIP_START_READY",'false').lower() == 'true':
    print("start process skip execute cass_start_ready.py")
main()
