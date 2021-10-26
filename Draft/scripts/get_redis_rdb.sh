#!/bin/bash
TOOLS=`dirname $0`

function build_help {
    echo "get_redis_rdb.sh [OPTIONS]"
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

if [ -z $REDIS_NAME ] ; then REDIS_NAME="qa-ybsp-redis-cluster"; fi;
if [ -z $NS ] ; then NS="redis"; fi;
if [ -z $REDIS_PORT ] ; then REDIS_PORT=6379; fi;
if [ -z $MODULE_LIST ] ; then MODULE_LIST="ymall,ytms,ypush,userm,phonebookm,devem,meetingctrl,notim,uss,mdmigrator"; fi;
if [ -z $BIG_KEY_LEN ] ; then BIG_KEY_LEN=5000; fi;
if [ -z $BIG_KEY_VALUE ] ; then BIG_KEY_VALUE=10240; fi;

rm -rf $TOOLS/../data
mkdir -p $TOOLS/../data
cd $TOOLS/../data

cat <<EOF > redis-shake.conf
conf.version = 1
id = redis-shake
log.file = /var/log/redis-shake.log
log.level = error
pid_path =  /tmp/
http_profile = -1
parallel = 32
source.type = cluster
source.address = slave@${REDIS_NAME}.${NS}.svc.cluster.local:${REDIS_PORT}
source.password_raw =
source.auth_type = auth
target.rdb.output = rdb
EOF

redis-shake.linux -conf redis-shake.conf -type dump
unset RDB_FILES
for filename in `ls rdb.*`
do
  if [[ -z $RDB_FILES ]] ; then
    RDB_FILES=$filename
  else
    RDB_FILES=$RDB_FILES,$filename
  fi
done
analyze_redis_rdb.py --redis_name="$REDIS_NAME" --module_list="$MODULE_LIST" --big_key_len=$BIG_KEY_LEN --big_key_value=$BIG_KEY_VALUE --rdb_files="${RDB_FILES}"
rm -rf rdb.*


