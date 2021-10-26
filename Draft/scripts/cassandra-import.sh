#!/bin/bash

tar_file=$1
bkp_name="bkp-$$"

if [ -z "${tar_file}" ]; then
    echo "Usage import.sh [tar file]"
    exit 1
fi

keyspace=$(basename "${tar_file}" ".tar.gz")

mkdir -p "${bkp_name}"

tar -xvzf "${tar_file}" -C "${bkp_name}"

echo "Drop keyspace ${keyspace}"
cqlsh -u cassandra -p cassandra -e "drop keyspace \"${keyspace}\";"

echo "Create empty keyspace: ${keyspace}"
cat "${bkp_name}/${keyspace}.sql" | cqlsh -u cassandra -p cassandra

echo "${bkp_name}/${keyspace}/"
for dir in "${bkp_name}/${keyspace}/"*; do
    echo "sstableloader -u cassandra -pw cassandra -d localhost "${dir}""
    sstableloader -u cassandra -pw cassandra -d localhost "${dir}"
done