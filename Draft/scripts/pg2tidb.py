#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# target = ETLAlchemyTarget("postgresql://etlalchemy:etlalchemy@localhost/test", drop_database=True)
# STORES is used to configure databases
STORES = [
    {
        'name': 'tidb',
        'url': 'mysql://root:Yealink1105@47.103.98.51:9436/tmp?charset=utf8',
    },
    {
        'name': 'testdb',
        'url': 'postgresql://postgres:YeaPostgres@10.120.27.7:9432/ytmsdb',
    }
]
# TASK is used to configure the ETL process
TASKS = [
    {
        'from': [{
            'name': 'testdb'
        }],
        'to': {
            'name': 'tidb',
        },
        'orders': [
            'qrtz_job_details'
        ]
    }
]

if __name__ == '__main__':
    import carry

    carry.run(__file__)
