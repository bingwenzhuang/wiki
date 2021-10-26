#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import redis
import json
import time
import socket
import argparse

'''
##new 
get key by command type
##showlog data struct
id,
timestamp 
duration (unit Microsecond 1000000 Microsecond = 1s)
command
args
client ip:port
cleint name
##slowlog data struc
id
start_time
duration
cmd
key 
args
client_ip
client_port
client_name
'''
count = 10
SINGE_COMMAND=(
''
)
BATCH_COMMAND=(
''
)


def parse_slowlog_get(response, **options):
    space = ' ' if options.get('decode_responses', False) else b' '
    return [{
        'id': item[0],
        'start_time': int(item[1]),
        'duration': int(item[2]),
        'command': space.join(item[3]),
        'client_ip': item[4],
        'client_name': item[5]
    } for item in response]




def main():
    parser = argparse.ArgumentParser(description='analyze redis slow log')
    parser.add_argument('--host', '-n', help='redis hostname,default=127.0.0.1', default='127.0.0.1')
    parser.add_argument('--port', '-P', help='redis port,default=6379', default=6379)
    parser.add_argument('--password', '-p', help='redis password,default=None', default=None)
    parser.add_argument('--period','-c', help='redis get peroid, unit second,default=10', type= int, default=10)
    args = parser.parse_args()

    num = 0
    r = redis.StrictRedis(host=args.host, port=args.port, db=0, password=args.password, socket_timeout=5, socket_connect_timeout=2,decode_responses=True)
    while True:
        try:
            r.ping()
        except Exception as e:
            raise e
            print('ERROR:', e)
            r = redis.StrictRedis(host=args.host, port=args.port, db=0, password=args.password, socket_timeout=5, socket_connect_timeout=2,decode_responses=True)
        try:
            print(str(r.slowlog_len()))
            response_slowlog = r.execute_command("slowlog get "+ str(r.slowlog_len()))
            slowlog = parse_slowlog_get(response_slowlog)
        except Exception as e:
            raise e
            print('ERROR:', e)
            time.sleep(args.period*5)
            # num += 1
            # if num > count:
            #     break
            continue

        data = dict()
        for entry in slowlog:
            data['id'] = entry['id']
            data['start_time'] = entry['start_time']
            # datetime.fromtimestamp(entry['start_time'])
            # start_time = datetime.utcfromtimestamp(entry['start_time'])
            data['duration'] = entry['duration']
            data['cmd'] = entry['command'].split(' ')[0]
            data['args'] = entry['command'].split(' ')[1:]
            data['client_ip'] = entry['client_ip'].split(':')[0]
            data['client_port'] = entry['client_ip'].split(':')[1]
            data['client_name'] = entry['client_name']
            try:
                data['client_name'] = socket.gethostbyaddr(data['client_ip'])[0]
            except:
                data['client_name'] = entry['client_name']
            print(json.dumps(data))

        r.slowlog_reset()
        time.sleep(args.period)

'''
usage: analyze_redis_slowlog.py [-h] [--host HOST] [--port PORT]
                                [--password PASSWORD] [--period PERIOD]

analyze redis slow log

optional arguments:
  -h, --help            show this help message and exit
  --host HOST, -n HOST  redis hostname
  --port PORT, -P PORT  redis port
  --password PASSWORD, -p PASSWORD
                        redis password
  --period PERIOD, -c PERIOD
                        redis get peroid, unit second
'''
main()
