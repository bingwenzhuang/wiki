#!/usr/bin/env python
# -*- coding:utf-8 -*-
# Author: mithril
# Created Date: 2019-07-01 17:59:11
# Last Modified: 2019-07-02 13:11:56


from sqlalchemy import create_engine
import click
from itertools import chain
import codecs
from urllib.parse import urlparse




@click.command()
@click.argument('url')
@click.option('-o', '--output-path', default='db_schema.md')
@click.option('-t', '--tables')
def dump_schema(url, output_path, tables):
    '''
    url : 'mysql://user:password@host:port/database?charset=utf8'

    example: python dump_schema.py url -o db_schema.md -t products
    
    '''
    p = urlparse(url)
    database = p.path[1:]
    
    engine = create_engine(url)
    conn = engine.raw_connection()

    c = conn.cursor()
    if tables:
        tables = sorted(tables.split(','))
    else:
        print('export all tables')
        c.execute('show tables;')
        tables = c.fetchall()
        tables = list(chain(*tables))

    t = ''

    for tb in tables:
        if '_' in tb or tb.startswith('sbtest'):
            # tb.endswith('_t') or tb.startswith('__')
            continue
        # suppliers

        c.execute(f"SHOW FULL COLUMNS FROM {tb}")
        columns = c.fetchall()

        t +=f"# {database}.{tb}\n"
        t +="| 字段名 | 类型 | 允许为空 | 默认值 | 索引 | 备注 |\n"
        t +="| --- | --- | --- | --- | --- | --- |\n"
        for i in columns:
            field, type, _ , nullable, key, default, extra, _, comment = i
            comment = comment.replace("\n", '|').replace('<br />', '-')
            default = extra if extra else default
            t += f"| {field} | {type} | {nullable} | {default} | {key} | {comment} |\n"
        t += "\n\n"

    with codecs.open(output_path, mode='w', encoding='utf-8') as f:
        f.write(t)


if __name__ == '__main__':
    dump_schema()