#!/bin/bash
# set -x
TOOLS=`dirname $0`
CONN_TIMEOUT=2
LOG_FILE=/opt/mgr/log/mgr_auto_recovery_`date +%Y%m%d`.log

# 解析CONFIG_DIR
HOSTS=$1
HOSTS=(${HOSTS//,/ })
PORTS=$2
PORTS=(${PORTS//,/ })
LOCAL_HOST=$3
LOCAL_PORT=$4
CONFIG_FILE=$5
MYSQL_REPLICATION_USER=$6
MYSQL_REPLICATION_PASSWORD=$7
PERIOD=$8

# 多数派个数定义
NODE_COUNT=${#HOSTS[*]}
let MAJOR_NODE=($NODE_COUNT/2+1)
# 自身连通的数量, 用此代表少数派或者多数派
MGR_LOCAL_MEMBER_CNT=0
# MGR在线数
MGR_ONLINE_CNT=0
# gtid
GTID=0
# 在线hosts的下标
ONLINE_HOSTS_INDEX=0
ONLINE_PORTS_INDEX=0
# mysql command cli
mysqlconn="mysql --defaults-file=${CONFIG_FILE} --connect-timeout=$CONN_TIMEOUT"
# define default group name
GROUP_NAME="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
MYSQL_REPLICATION_USER_CNT=1


function reset_var_value {
    # 自身连通的数量, 用此代表少数派或者多数派
    MGR_LOCAL_MEMBER_CNT=0
    # MGR在线数
    MGR_ONLINE_CNT=0
    # gtid
    GTID=0
    # 在线hosts的下标
    ONLINE_HOSTS_INDEX=0
    ONLINE_PORTS_INDEX=0
    # define default group name
    GROUP_NAME="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    MYSQL_REPLICATION_USER_CNT=1
}

function print_log {
    typeset MSG
    MSG="[`date "+%Y-%m-%d %H:%M:%S"`] - $1"
    echo $MSG
    echo $MSG >> $LOG_FILE 
}

function get_local_stat {
    LOCAL_STAT=$($mysqlconn -h$LOCAL_HOST -P$LOCAL_PORT -Bse "SELECT MEMBER_STATE FROM performance_schema.replication_group_members where (MEMBER_HOST='$LOCAL_HOST' and MEMBER_PORT='$LOCAL_PORT') or (MEMBER_HOST='' and MEMBER_PORT is null);")     
    if [ $? -ne 0 ] ; then
        print_log "local node service not running ..."
        continue
    else
        print_log "local node mgr status: $LOCAL_STAT ..."
        GROUP_NAME=$($mysqlconn -h$LOCAL_HOST -P$LOCAL_PORT -Bse "show global variables like 'group_replication_group_name';" | awk  '{print $2}')
    fi
}

function get_mgr_info {
    for ((i=0;i<$NODE_COUNT;i++))
    do
        CNT=$($mysqlconn -h${HOSTS[i]} -P${PORTS[i]}  -Bse "SELECT count(*) as cnt FROM performance_schema.replication_group_members where MEMBER_STATE='ONLINE';")
        if [ $? -eq 0 ] ; then
            ONLINE_HOSTS[$ONLINE_HOSTS_INDEX]=${HOSTS[i]}
            ONLINE_PORTS[$ONLINE_PORTS_INDEX]=${PORTS[i]}
            let ONLINE_HOSTS_INDEX+=1
            let ONLINE_PORTS_INDEX+=1
            let MGR_LOCAL_MEMBER_CNT+=1
            if [ $MGR_ONLINE_CNT -lt $CNT ] ; then
                MGR_ONLINE_CNT=$CNT
            fi
            gtid_tmp=$($mysqlconn -h${HOSTS[i]} -P${PORTS[i]}  -Bse "select VARIABLE_VALUE as gtid from performance_schema.global_variables where VARIABLE_NAME='gtid_executed';")
            GTID_EXECUTED=${gtid_tmp##*$GROUP_NAME':'}
            if [[ $GTID_EXECUTED -gt $GTID ]] ; then
                GTID=$GTID_EXECUTED
                PRIMARY_ID=$i
            fi
        else
            print_log "${HOSTS[i]}:${PORT[i]} is UNREACHABLE..."
        fi
    done 
}

function mgr_recovery {
    ONLINE_COUNT=${#ONLINE_HOSTS[*]}
    for ((i=0;i<$ONLINE_COUNT;i++))
    do
        if [ $? -eq 0 ] ; then
            $mysqlconn -h${ONLINE_HOSTS[i]} -P${ONLINE_PORTS[i]} -Bse "STOP GROUP_REPLICATION;"
            if  [ $? -eq 0 ] ; then
                print_log "${ONLINE_HOSTS[i]} STOP GROUP_REPLICATION suceess..."
            else
                print_log "${ONLINE_HOSTS[i]} STOP GROUP_REPLICATION failure..."
                continue
            fi
        fi
    done

    $mysqlconn -h${HOSTS[PRIMARY_ID]} -P${PORTS[PRIMARY_ID]} -Bse " SET GLOBAL group_replication_bootstrap_group=ON; \
                                                                                        START GROUP_REPLICATION;\
                                                                                        SET GLOBAL group_replication_bootstrap_group=OFF;"
    if  [ $? -eq 0 ] ; then
        print_log "${HOSTS[PRIMARY_ID]} START BOOTSTRAP GROUP_REPLICATION suceess..."
    else
        print_log "${HOSTS[PRIMARY_ID]} START BOOTSTRAP GROUP_REPLICATION failure..."
        continue
    fi

    for ((i=0;i<$ONLINE_COUNT;i++))
    do
        $mysqlconn -h${ONLINE_HOSTS[i]} -P${ONLINE_PORTS[i]} -Bse "START GROUP_REPLICATION;" 
        if  [ $? -eq 0 ] ; then
            print_log "${ONLINE_HOSTS[i]} START GROUP_REPLICATION suceess..."
        else
            print_log "${ONLINE_HOSTS[i]} START GROUP_REPLICATION failure..."
        fi
    done
}


while true
do
    sleep $PERIOD
    print_log "==="
    reset_var_value
    get_local_stat
    get_mgr_info
    if [ $MGR_LOCAL_MEMBER_CNT -lt $MAJOR_NODE ] ; then
        ## 少数派的不管
        print_log "Local nodes are in the minority: $MGR_LOCAL_MEMBER_CNT"
        continue
    else
        ## 多数派
        print_log "Local nodes are in the majority: $MGR_LOCAL_MEMBER_CNT"
        ## 本地在线，集群状态正常,不管
        if [[ "$LOCAL_STAT" = "ONLINE"  && $MGR_ONLINE_CNT -ge $MAJOR_NODE ]] ; then
            print_log "Local nodes is ok and mgr cluster is ok..."
            continue
        fi
        ## 本地在线，集群状态不正常
        if [[ "$LOCAL_STAT" = "ONLINE"  && $MGR_ONLINE_CNT -lt $MAJOR_NODE ]] ; then
            # 重启整个集群,仅能有选举为primary节点才有资格做这个操作
            print_log "start mgr recovery for local stat is ok but mgr cluster stats is not ok..."
            print_log "recovery process on ${HOSTS[PRIMARY_ID]}:${PORTS[PRIMARY_ID]}"
            if [ $LOCAL_HOST = ${HOSTS[PRIMARY_ID]} ] ; then
                mgr_recovery
            fi
        fi
        ## 本地不在线,集群状态正常
        if [[ "$LOCAL_STAT" = "OFFLINE"  && $MGR_ONLINE_CNT -ge $MAJOR_NODE ]] ; then
            print_log "Local nodes is not ok and mgr cluster is ok..."
            MYSQL_REPLICATION_USER_CNT=$($mysqlconn -h$LOCAL_HOST -P$LOCAL_PORT  -Bse "select count(*) as cnt from mysql.user where user='${MYSQL_REPLICATION_USER}'  and host='%';")
            if [ $MYSQL_REPLICATION_USER_CNT -eq 0 ] ; then
                $mysqlconn -h$LOCAL_HOST -P$LOCAL_PORT   -Bse "SET SQL_LOG_BIN=0; \
                                                               CREATE USER '$MYSQL_REPLICATION_USER'@'%' IDENTIFIED BY '$MYSQL_REPLICATION_PASSWORD'; \
                                                               GRANT REPLICATION SLAVE ON *.* TO '$MYSQL_REPLICATION_USER'@'%'; \
                                                               GRANT BACKUP_ADMIN ON *.* TO '$MYSQL_REPLICATION_USER'@'%'; \
                                                               reset master; \
                                                               CHANGE REPLICATION SOURCE TO SOURCE_USER='$MYSQL_REPLICATION_USER', SOURCE_PASSWORD='$MYSQL_REPLICATION_PASSWORD' FOR CHANNEL 'group_replication_recovery';
                                                               "  
            fi
            $mysqlconn -h$LOCAL_HOST -P$LOCAL_PORT   -Bse "start group_replication;"
            if [ $? -eq 0 ] ; then
                print_log "local node start group_replication sucess..."
            else
                print_log "local node start group_replication failure and need manual ops..."
            fi
        fi
        ## 本地不在线，集群状态不正常
        if [[ "$LOCAL_STAT" = "OFFLINE"  && $MGR_ONLINE_CNT -lt $MAJOR_NODE ]] ; then
            # 重启整个集群,仅能有选举为primary节点才有资格做这个操作
            print_log "start mgr recovery for local stat is not ok but mgr cluster stats is not ok..."
            print_log "recovery process on ${HOSTS[PRIMARY_ID]}:${PORTS[PRIMARY_ID]}"
            if [ $LOCAL_HOST = ${HOSTS[PRIMARY_ID]} ] ; then
                mgr_recovery
            fi
        fi
    fi
done
