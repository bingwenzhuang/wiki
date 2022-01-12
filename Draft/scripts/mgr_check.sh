#!/bin/bash
# set -x
TOOLS=`dirname $0`
CONN_TIMEOUT=2
HOSTS=$1
HOSTS=(${HOSTS//,/ })
PORTS=$2
PORTS=(${PORTS//,/ })
LOCAL_HOST=$3
LOCAL_PORT=$4
CONFIG_FILE=$5

# 多数派个数定义
NODE_COUNT=${#HOSTS[*]}
let MAJOR_NODE=($NODE_COUNT/2+1)
# 自身连通的数量, 用此代表少数派或者多数派
MGR_LOCAL_MEMBER_CNT=0
# MGR在线数
MGR_ONLINE_CNT=0
# gtid
## laster gtid
GTID=0
## local gtid
LOCAL_GTID=0
mysqlconn="mysql --defaults-file=${CONFIG_FILE} --connect-timeout=$CONN_TIMEOUT"

function get_local_stat {
    LOCAL_STAT=$($mysqlconn -h$LOCAL_HOST -P$LOCAL_PORT  -Bse "SELECT MEMBER_STATE FROM performance_schema.replication_group_members where (MEMBER_HOST='$LOCAL_HOST' and MEMBER_PORT='$LOCAL_PORT') or (MEMBER_HOST='' and MEMBER_PORT is null);" 2>/dev/null)     
    if [ $? -ne 0 ] ; then
        /bin/echo -en "HTTP/1.1 503 Service Unavailable\r\n" 
        /bin/echo -en "Content-Type: Content-Type: text/plain\r\n" 
        /bin/echo -en "Connection: close\r\n" 
        /bin/echo -en "\r\n" 
        /bin/echo -en "Local node service not running.\r\n" 
        /bin/echo -en "\r\n" 
        exit 1
    fi
}

function get_mgr_info_and_local_roleandGTID {
    for ((i=0;i<$NODE_COUNT;i++))
    do
        CNT=$($mysqlconn -h${HOSTS[i]} -P${PORTS[i]}  -Bse "SELECT count(*) as cnt FROM performance_schema.replication_group_members where MEMBER_STATE='ONLINE';" 2>/dev/null )
        if [ $? -eq 0 ] ; then
            let MGR_LOCAL_MEMBER_CNT+=1
            if [ $MGR_ONLINE_CNT -lt $CNT ] ; then
                MGR_ONLINE_CNT=$CNT
            fi
            GTID_EXECUTED=$($mysqlconn -h  ${HOSTS[i]} -P${PORTS[i]}  -Bse "select SUBSTRING_INDEX(SUBSTRING_INDEX(max(case when VIEW_ID is null or view_id = '' then '0:0' else VIEW_ID end),':',2),':',-1) as view_id from performance_schema.replication_group_member_stats;" 2>/dev/null)
            if [[ "$LOCAL_HOST" = ${HOSTS[i]}  && $LOCAL_PORT -ge ${PORTS[i]} ]] ; then
                LOCAL_GTID=$GTID_EXECUTED
                LOCAL_ROLE=$($mysqlconn -h$LOCAL_HOST -P$LOCAL_PORT  -Bse "SELECT MEMBER_ROLE FROM performance_schema.replication_group_members where (MEMBER_HOST='$LOCAL_HOST' and MEMBER_PORT='$LOCAL_PORT') or (MEMBER_HOST='' and MEMBER_PORT is null);" 2>/dev/null)
                LOCAL_ID=$i
            fi
            if [[ $GTID_EXECUTED -gt $GTID ]] ; then
                GTID=$GTID_EXECUTED
                PRIMARY_ID=$i
            fi
        fi
    done 
}
 

get_local_stat
get_mgr_info_and_local_roleandGTID

if [ $MGR_LOCAL_MEMBER_CNT -lt $MAJOR_NODE ] ; then
    ## 少数派的,重启myrouter
    # mysql is unhealthy, return http 503 
    /bin/echo -en "HTTP/1.1 503 Service Unavailable\r\n" 
    /bin/echo -en "Content-Type: Content-Type: text/plain\r\n" 
    /bin/echo -en "Connection: close\r\n" 
    /bin/echo -en "\r\n" 
    /bin/echo -en "Local nodes are in the minority: $MGR_LOCAL_MEMBER_CNT.\r\n" 
    /bin/echo -en "\r\n" 
    exit 1
else
    ## 多数派
    ## 只有当它是主的时候,才提供服务
    ## 本地在线，集群状态正常 
    if [[ "$LOCAL_STAT" = "ONLINE"  && $MGR_ONLINE_CNT -ge $MAJOR_NODE && $LOCAL_ROLE = "PRIMARY" && $LOCAL_GTID -eq $GTID ]] ; then
        /bin/echo -en "HTTP/1.1 200 OK\r\n" 
        /bin/echo -en "Content-Type: Content-Type: text/plain\r\n" 
        /bin/echo -en "Connection: close\r\n" 
        /bin/echo -en "\r\n" 
        /bin/echo -en "Local nodes are in the majority: $MGR_LOCAL_MEMBER_CNT and Local is primary.\r\n" 
        /bin/echo -en "\r\n" 
        exit 0
    else
        /bin/echo -en "HTTP/1.1 503 Service Unavailable\r\n" 
        /bin/echo -en "Content-Type: Content-Type: text/plain\r\n" 
        /bin/echo -en "Connection: close\r\n" 
        /bin/echo -en "\r\n" 
        /bin/echo -en "Local nodes are in the majority: $MGR_LOCAL_MEMBER_CNT and Local is secondary.\r\n" 
        /bin/echo -en "\r\n" 
        exit 1
    fi
fi
