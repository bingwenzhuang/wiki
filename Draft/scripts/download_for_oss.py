#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import asyncio
import asyncoss
import base64
import time
from aiofile import async_open

endpoint = 'https://oss-cn-shenzhen.aliyuncs.com'
access_id = str(base64.b64decode('TFRBSTRGeTJEZExVRko1czVpa3ZTRld3'),'utf-8')
access_key = str(base64.b64decode('alVnQXlOdmRWSVZCZ2xDalhWZGs5ODdsSXVjYThq'),'utf-8')
bucket_name = 'tidb-backup-test'

def to_string(data):
    """若输入为bytes，则认为是utf-8编码，并返回str"""
    if isinstance(data, bytes):
        return data.decode('utf-8')
    else:
        return data

def to_unicode(data):
    """把输入转换为unicode，要求输入是unicode或者utf-8编码的bytes。"""
    return to_string(data)

async def write_filename(fsrc,filename,chunk_size=256*1024,start_time=time.time()):
    async with async_open(to_unicode(filename), 'wb') as fdst:
        num_read = 0
        while 1:
            buf = await fsrc.read(chunk_size)
            if not buf:
                break
            num_read += len(buf)
            await fdst.write(buf)
    print('end download %s seconds :%s' % (filename,time.time()-start_time))


async def download(bucket,key):
    print('start download %s ' % key)
    start_time = time.time()
    filen = key.split('/')[-1]
    result = await bucket.get_object(key)
    await write_filename(result,f'/download/{filen}',start_time)
 

async def main():
    # The object key in the bucket is story.txt
    flag = True
    marker = ''
    auth = asyncoss.Auth(access_id, access_key)
    bucket = asyncoss.Bucket(auth, endpoint, bucket_name,connect_timeout = 1000*60)
    tasks = []
    while flag:
        result = await bucket.list_objects(prefix='./pre-prod-common-pd.tidb-2379-2020-11-17t11-30-00',marker = marker,max_keys=1000)
        for obj in result.object_list:
            tasks.append(asyncio.ensure_future(download(bucket=bucket,key=obj.key)))
            # await download(bucket=bucket,key=obj.key)
        if  result.next_marker is None or result.next_marker == '':
            flag = False
            marker = ''
        else:
            flag = True
            marker = result.next_marker
    await asyncio.wait(tasks)
    await bucket.close()
    
 
start_time = time.time()
loop = asyncio.get_event_loop()
loop.run_until_complete(main())
print('total seconds:%s' % str(time.time()-start_time))


