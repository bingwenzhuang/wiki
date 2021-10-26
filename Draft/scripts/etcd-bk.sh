#!/bin/bash
BASE_DRI=`dirname $0`

## 备份etcd数据
### 逻辑备份方式
### 快照备份方式

function build_help {
    echo "etcd-bk.sh [OPTIONS]"
		echo "-n|--name redis-cluster name. options:qa-ybsp-redis-cluster"
		echo "-N|--ns k8s namespace name. default:redis"
		echo "-P|--port redis port. default: 6379"
		echo "-m|--modules redis prefix whitelist. default: ymall,ytms,ypush,userm,phonebookm,devem,meetingctrl,notim,uss,mdmigrator"
		echo "-l|--biglen definition of the number of big key elements,default=5000"
		echo "-v|--bigvalue definition of the value of big key elements,default=10240"
}

if [ $# == 0 ]; then build_help; exit 1; fi;

if ! options=$(getopt -o n:N:P:m:l:v -al name:,ns:,port:,modules:,biglen:,bigvalue: -- "$@")
then
   # something went wrong, getopt will put out an error message for us
   exit 1
fi

eval set -- $options
while [ $# -gt 0 ]
do
case "$1" in
 -n|--name) REDIS_NAME=$2; shift 2;;
 -N|--ns) NS=$2; shift 2;;
 -P|--port) REDIS_PORT=$2; shift 2;;
 -m|--modules) MODULE_LIST=$2; shift 2;;
 -l|--biglen) BIG_KEY_LEN=$2; shift 2;;
 -v|--bigvalue) BIG_KEY_VALUE=$2; shift 2;;
 (--) shift ; break ;;
 (-*) echo "$0: error - unrecognized option $1" 1>&2; exit 1;;
 (*) build_help; exit 1 ;;
esac
done


