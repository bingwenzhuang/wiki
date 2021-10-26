#!/bin/bash

# k get pod -n tidb -o wide 

# k get nodes --show-labels  | grep cn-shanghai.10.4.232.94  | awk -F "zone=" '{print $2}' | awk -F "," '{print $1}'
kubectl get pod -n $1 -o wide > /tmp/tmp
old_IFS=${IFS}
IFS=$'\n'
for line in `cat /tmp/tmp  | grep -v NAME`
do 
 PODNAME=`echo $line | awk '{print $1}'`
 NODENAME=`echo $line | awk '{print $7}'`
 ZONE=`kubectl get nodes --show-labels  | grep ${NODENAME}  | awk -F "zone=" '{print $2}' | awk -F "," '{print $1}'`
 echo "| $PODNAME | $NODENAME | $ZONE | " 
done 
IFS=$old_IFS
