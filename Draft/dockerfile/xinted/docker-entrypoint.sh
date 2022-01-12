#!/bin/bash
set -e

cat >>/etc/services<<eof
$SERVICES_NAME  $SERVICES_PORT/tcp      
eof

exec "$@"
