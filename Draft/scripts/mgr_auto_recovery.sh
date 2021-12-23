#!/bin/bash
TOOLS=`dirname $0`

CONFIG_DIR=$1
LOG_DIR=$2
SEEDS=$3
CONN_TIMEOUT=2
LOG_FILE=${LOG_DIR}/mgr_auto_recovery_`date +%Y%m%d`.log
# 解析CONFIG_DIR
USER=
PASSWORD=
HOSTS=
PORTS=
LOCAL_HOST=
LOCAL_PORT=
# 多数派个数定义
let MAJOR_NODE=(${#HOSTS[*]}/2+1)
# 自身连通的数量, 用此代表少数派或者多数派
MGR_LOCAL_MEMBER_CNT=0
# MGR在线数
MGR_ONLINE_CNT=0
# gtid
GTID=0
# 在线hosts的下标
ONLINE_HOSTS_INDEX=0
ONLINE_PORTS_INDEX=0


function print_log {
    typeset MSG
    MSG="$1"
    echo $MSG
    echo $MSG >> $LOG_FILE 
}

function get_local_stat {
    LOCAL_STAT=$(mysql --connect-timeout=$CONN_TIMEOUT -u$USER -p$PASSWORD -h$LOCAL_NODE -P$LOCAL_PORT  -Bse "SELECT MEMBER_STATE FROM performance_schema.replication_group_members where MEMBER_HOST='$LOCAL_NODE' and MEMBER_PORT='$LOCAL_PORT';")     
    if [ $? -ne 0 ] ; then
        print_log "local node service not running ..."
        exit 1
    else
        print_log "local node mgr status: $LOCAL_STAT ..."
    fi
}

function get_mgr_info {
    for ((i=0;i<${#HOSTS[*]};i++))
    do
        CNT=$(mysql --connect-timeout=$CONN_TIMEOUT -u$USER -p$PASSWORD -h${HOSTS[i]} -P${PORT[i]}  -Bse "SELECT count(*) as cnt FROM performance_schema.replication_group_members where MEMBER_STATE='ONLINE';")
        if [ $? -eq 0 ] ; then
            ONLINE_HOSTS[$ONLINE_HOSTS_INDEX]=${HOSTS[i]}
            ONLINE_PORTS[$ONLINE_PORTS_INDEX]=${PORT[i]}
            let ONLINE_HOSTS_INDEX+=1
            let ONLINE_PORTS_INDEX+=1
            let MGR_LOCAL_MEMBER_CNT+=1
            if [ $MGR_ONLINE_CNT -lt $CNT ] ; then
                MGR_ONLINE_CNT=$CNT
            fi
            GTID_EXECUTED=$(mysql --connect-timeout=$CONN_TIMEOUT -u$USER -p$PASSWORD -h  ${HOSTS[i]} -P${PORT[i]}  -Bse "select max(interval_end) from mysql.gtid_executed where source_uuid='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa';")
            if [ $gtid_executed -gt $GTID ] ; then
                GTID=$gtid_executed
                PRIMARY_ID=$i
            fi
        else
            print_log "${HOSTS[i]}:${PORT[i]} is UNREACHABLE..."
        fi
    done 
}

function mgr_recovery {
    touch /tmp/mgr_recovery.lock
    if [ -f /tmp/mgr_recovery.lock ] ; then 
        print_log "mgr recovering..."
        exit 0
    fi
    for ((i=0;i<${#ONLINE_HOSTS[*]};i++))
    do
        if [ $? -eq 0 ] ; then
            mysql --connect-timeout=$CONN_TIMEOUT -u$USER -p$PASSWORD -h${ONLINE_HOSTS[i]} -P${ONLINE_PORTS[i]} -Bse "STOP GROUP_REPLICATION;"
            if  [ $? -eq 0 ] ; then
                print_log "${ONLINE_HOSTS[i]} STOP GROUP_REPLICATION suceess..."
            else
                print_log "${ONLINE_HOSTS[i]} STOP GROUP_REPLICATION failure..."
                rm -rf /tmp/mgr_recovery.lock
                exit 1
            fi
        fi
    done
    mysql --connect-timeout=$CONN_TIMEOUT -u$USER -p$PASSWORD -h${HOSTS[PRIMARY_ID]} -P${PORT[PRIMARY_ID]} -Bse " SET GLOBAL group_replication_bootstrap_group=ON; \
                                                                                        START GROUP_REPLICATION;\
                                                                                        SET GLOBAL group_replication_bootstrap_group=OFF;"
    if  [ $? -eq 0 ] ; then
        print_log "${HOSTS[PRIMARY_ID]} STOP GROUP_REPLICATION suceess..."
    else
        print_log "${HOSTS[PRIMARY_ID]} STOP GROUP_REPLICATION failure..."
        rm -rf /tmp/mgr_recovery.lock
        exit 1
    fi

    for ((i=0;i<${#ONLINE_HOSTS[*]};i++))
    do
        mysql --connect-timeout=$CONN_TIMEOUT -u$USER -p$PASSWORD -h${ONLINE_HOSTS[i]} -P${ONLINE_PORTS[i]} -Bse "START GROUP_REPLICATION;" 
        if  [ $? -eq 0 ] ; then
            print_log "${ONLINE_HOSTS[i]} STOP GROUP_REPLICATION suceess..."
        else
            print_log "${ONLINE_HOSTS[i]} STOP GROUP_REPLICATION failure..."
        fi
    done
    rm -rf /tmp/mgr_recovery.lock
}

get_local_stat
get_mgr_info

if [ $MGR_LOCAL_MEMBER_CNT -lt $MAJOR_NODE ] ; then
    ## 少数派的不管
    print_log "Local nodes are in the minority: $MGR_LOCAL_MEMBER_CNT"
    exit 1
else
    ## 多数派
    print_log "Local nodes are in the majority: $MGR_LOCAL_MEMBER_CNT"
    ## 本地在线，集群状态正常,不管
    if [[ "$LOCAL_STAT" = "ONLINE"  && $MGR_ONLINE_CNT -ge $MAJOR_NODE ]] ; then
        print_log "Local nodes is ok and mgr cluster is ok..."
        exit 1
    fi
    ## 本地在线，集群状态不正常
    if [[ "$LOCAL_STAT" = "ONLINE"  && $MGR_ONLINE_CNT -lt $MAJOR_NODE ]] ; then
        # 重启整个集群,仅能有选举为primary节点才有资格做这个操作
        print_log "start mgr recovery for local stat is ok but mgr cluster stats is not ok..."
        print_log "recovery process on ${HOSTS[PRIMARY_ID]}:${PORT[PRIMARY_ID]}"
        if [ $LOCAL_NODE = ${HOSTS[PRIMARY_ID]} ] ; then
            mgr_recovery
        fi
    fi
    ## 本地不在线,集群状态正常
    if [[ "$LOCAL_STAT" = "OFFLINE"  && $MGR_ONLINE_CNT -ge $MAJOR_NODE ]] ; then
        print_log "Local nodes is not ok and mgr cluster is ok..."
        mysql --connect-timeout=$CONN_TIMEOUT -u$USER -p$PASSWORD -h$LOCAL_NODE -P$LOCAL_PORT   -Bse "start group_replication;"
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
        print_log "recovery process on ${HOSTS[PRIMARY_ID]}:${PORT[PRIMARY_ID]}"
        if [ $LOCAL_NODE = ${HOSTS[PRIMARY_ID]} ] ; then
            mgr_recovery
        fi
    fi
fi

