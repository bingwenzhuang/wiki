#!/usr/bin/env python
import pyinotify
import boto

import argparse
from traceback import format_exc
from threading import Thread
from Queue import Queue
import logging
import logging.handlers
import os.path
import socket
import json
import sys
import os
import pwd
import grp
import re
import signal
import StringIO

default_log = logging.getLogger('tablesnap')
if os.environ.get('TABLESNAP_SYSLOG', False):
    facility = logging.handlers.SysLogHandler.LOG_DAEMON
    syslog = logging.handlers.SysLogHandler(address='/dev/log', facility=facility)
    syslog.setFormatter(logging.Formatter('tablesnap: %(message)s'))
    default_log.addHandler(syslog)
else:
    stderr = logging.StreamHandler()
    stderr.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    default_log.addHandler(stderr)

if os.environ.get('TDEBUG', False):
    default_log.setLevel(logging.DEBUG)
else:
    default_log.setLevel(logging.INFO)

default_log.info('Starting up')

# Default number of writer threads
default_threads = 4

# S3 limit for single file upload
s3_limit = 5 * 2**30

# Max file size to upload without doing multipart in MB
max_file_size = 5120

# Default chunk size for multipart uploads in MB
default_chunk_size = 256

# Default inotify event to listen on
default_listen_events = ['IN_MOVED_TO', 'IN_CLOSE_WRITE']

class UploadHandler(pyinotify.ProcessEvent):
    def my_init(self, threads=None, key=None, secret=None, token=None, region=None,
                bucket_name=None,
                prefix=None, name=None, max_size=None, chunk_size=None,
                include=None,
                reduced_redundancy=False,
                with_index=True,
                with_sse=False,
                keyname_separator=None,
                delete_on_backup=False,
                log=default_log,
                md5_on_start=False,
                retries=1,
                listen_events=None):
        self.key = key
        self.secret = secret
        self.token = token
        self.region = region
        self.bucket_name = bucket_name
        self.prefix = prefix
        self.name = name or socket.getfqdn()
        self.keyname_separator = keyname_separator
        self.retries = retries
        self.log = log
        self.include = include
        self.reduced_redundancy = reduced_redundancy
        self.with_index = with_index
        self.with_sse = with_sse
        self.delete_on_backup = delete_on_backup
        self.md5_on_start = md5_on_start
        self.listen_events = listen_events or default_listen_events

        if max_size:
            self.max_size = max_size * 2**20
        else:
            self.max_size = max_file_size * 2**20

        if chunk_size:
            self.chunk_size = chunk_size * 2**20
        else:
            self.chunk_size = None

        self.fileq = Queue()
        for i in range(int(threads)):
            t = Thread(target=self.worker)
            t.daemon = True
            t.start()

    def build_keyname(self, pathname):
        return '%s%s%s%s' % (self.prefix, self.name, self.keyname_separator,
                             pathname)

    def add_file(self, filename):
        if self.include is None or (self.include is not None
                                    and self.include(filename)):
            self.fileq.put(filename)
        else:
            self.log.info('Skipping %s due to exclusion rule' % filename)

    def get_bucket(self):
        # Reconnect to S3
        if self.token:
            s3 = boto.s3.connect_to_region(self.region,
                                           aws_access_key_id=self.key,
                                           aws_secret_access_key=self.secret,
                                           security_token=self.token)
        elif self.key and self.secret:
            s3 = boto.s3.connect_to_region(self.region,
                                           aws_access_key_id=self.key,
                                           aws_secret_access_key=self.secret)
        else:
            s3 = boto.s3.connect_to_region(self.region)

        return s3.get_bucket(self.bucket_name)

    def worker(self):
        bucket = self.get_bucket()

        while True:
            f = self.fileq.get()
            keyname = self.build_keyname(f)
            try:
                self.upload_sstable(bucket, keyname, f)
            except:
                self.log.critical("Failed uploading %s. Aborting.\n%s" %
                             (f, format_exc()))
                # Brute force kill self
                os.kill(os.getpid(), signal.SIGKILL)

            self.fileq.task_done()

    def process_IN_CLOSE_WRITE(self, event):
        self.add_file(event.pathname)

    def process_IN_MOVED_TO(self, event):
        self.add_file(event.pathname)

    def process_IN_CREATE(self, event):
        if 'IN_CREATE' in self.listen_events:
            # If the --auto-add argument is specified when running tablesnap,
            # the handler will be notified by pyinotify for IN_CREATE file
            # events automatically, so they need to be ignored by the
            # handler unless explicitly listened for (via the
            # --listen-events) argument.
            self.add_file(event.pathname)
    #
    # Check if this keyname (ie, file) has already been uploaded to
    # the S3 bucket. This will verify that not only does the keyname
    # exist, but that the MD5 sum is the same -- this protects against
    # partial or corrupt uploads. IF you enable md5 at start
    #
    def key_exists(self, bucket, keyname, filename, stat):
        key = None
        for r in range(self.retries):
            try:
                key = bucket.get_key(keyname)
                if key == None:
                    self.log.debug('Key %s does not exist' % (keyname,))
                    return False
                else:
                    self.log.debug('Found key %s' % (keyname,))
                    break
            except:
                bucket = self.get_bucket()
                continue

        if key == None:
            self.log.critical("Failed to lookup keyname %s after %d"
                              " retries\n%s" %
                             (keyname, self.retries, format_exc()))
            raise

        if key.size != stat.st_size:
            self.log.warning('ATTENTION: your source (%s) and target (%s) '
                'sizes differ, you should take a look. As immutable files '
                'never change, one must assume the local file got corrupted '
                'and the right version is the one in S3. Will skip this file '
                'to avoid future complications' % (filename, keyname, ))
            return True
        else:
            if not self.md5_on_start:
                # Don't bother computing MD5 at startup
                return True
            else:
                # Compute MD5 sum of file
                try:
                    fp = open(filename, "r")
                except IOError as (errno, strerror):
                    if errno == 2:
                        # The file was removed, return True to skip this file.
                        return True

                    self.log.critical("Failed to open file: %s (%s)\n%s" %
                                 (filename, strerror, format_exc(),))
                    raise

                md5 = key.compute_md5(fp)
                fp.close()
                self.log.debug('Computed md5: %s' % (md5,))

                meta = key.get_metadata('md5sum')

                if meta:
                    self.log.debug('MD5 metadata comparison: %s == %s? : %s' %
                                  (md5[0], meta, (md5[0] == meta)))
                    result = (md5[0] == meta)
                else:
                    self.log.debug('ETag comparison: %s == %s? : %s' %
                                  (md5[0], key.etag.strip('"'),
                                  (md5[0] == key.etag.strip('"'))))
                    result = (md5[0] == key.etag.strip('"'))
                    if result:
                        self.log.debug('Setting missing md5sum metadata for %s' %
                                      (keyname,))
                        key.set_metadata('md5sum', md5[0])

                if result:
                    self.log.info("Keyname %s already exists, skipping upload"
                                  % (keyname))
                else:
                    self.log.warning('ATTENTION: your source (%s) and target (%s) '
                        'MD5 hashes differ, you should take a look. As immutable '
                        'files never change, one must assume the local file got '
                        'corrupted and the right version is the one in S3. Will '
                        'skip this file to avoid future complications' %
                        (filename, keyname, ))

                return result

    def get_free_memory_in_kb(self):
        f = open('/proc/meminfo', 'r')
        memlines = f.readlines()
        f.close()
        lines = []
        for line in memlines:
            ml = line.rstrip(' kB\n').split(':')
            lines.append((ml[0], int(ml[1].strip())))
        d = dict(lines)
        return d['Cached'] + d['MemFree'] + d['Buffers']

    def split_sstable(self, filename):
        free = self.get_free_memory_in_kb() * 1024
        self.log.debug('Free memory check: %d < %d ? : %s' %
            (free, self.chunk_size, (free < self.chunk_size)))
        if free < self.chunk_size:
            self.log.warn('Your system is low on memory, '
                          'reading in smaller chunks')
            chunk_size = free / 20
        else:
            chunk_size = self.chunk_size
        self.log.debug('Reading %s in %d byte sized chunks' %
                       (filename, chunk_size))
        f = open(filename, 'rb')
        while True:
            chunk = f.read(chunk_size)
            if chunk:
                yield StringIO.StringIO(chunk)
            else:
                break
        if f and not f.closed:
            f.close()

    def should_create_index(self, filename):
        """ Should we upload a JSON index for this file? Cassandra
            SSTables are composed of six files, so only generate a
            JSON file when we upload a .Data.db file. """
        return ("Data.db" in filename)

    def upload_sstable(self, bucket, keyname, filename):

        # Include the file system metadata so that we have the
        # option of using it to restore the file modes correctly.
        #
        try:
            stat = os.stat(filename)
        except OSError:
            # File removed?
            return

        if self.key_exists(bucket, keyname, filename, stat):
            return
        else:
            try:
                fp = open(filename, 'rb')
            except IOError:
                # File removed?
                return
            md5 = boto.utils.compute_md5(fp)
            self.log.debug('Computed md5sum before upload is: %s' % (md5,))
            fp.close()

        def progress(sent, total):
            if sent == total:
                self.log.info('Finished uploading %s' % filename)

        def delete_if_enabled():
            if self.delete_on_backup:
                os.remove(filename)
                self.log.info('Deleted %s' % filename)

        try:
            dirname = os.path.dirname(filename)
            if self.with_index and self.should_create_index(filename):
                listdir = []
                for listfile in os.listdir(dirname):
                    if self.include is None or (self.include is not None
                                                and self.include(listfile)):
                        listdir.append(listfile)
                json_str = json.dumps({dirname: listdir})
                for r in range(self.retries):
                    try:
                        key = bucket.new_key('%s-listdir.json' % keyname)
                        key.set_contents_from_string(json_str,
                            headers={'Content-Type': 'application/json'},
                            replace=True,
                            reduced_redundancy=self.reduced_redundancy,
                            encrypt_key=self.with_sse)
                        break
                    except:
                        if r == self.retries - 1:
                            self.log.critical("Failed to upload directory "
                                              "listing.")
                            raise
                        bucket = self.get_bucket()
                        continue

            meta = {'uid': stat.st_uid,
                    'gid': stat.st_gid,
                    'mode': stat.st_mode}
            try:
                u = pwd.getpwuid(stat.st_uid)
                meta['user'] = u.pw_name
            except:
                pass

            try:
                g = grp.getgrgid(stat.st_gid)
                meta['group'] = g.gr_name
            except:
                pass

            self.log.info('Uploading %s' % filename)

            meta = json.dumps(meta)

            for r in range(self.retries):
                try:
                    self.log.debug('File size check: %s > %s ? : %s' %
                        (stat.st_size, self.max_size,
                        (stat.st_size > self.max_size),))
                    if stat.st_size > self.max_size:
                        self.log.info('Performing multipart upload for %s' %
                                     (filename))
                        mp = bucket.initiate_multipart_upload(keyname,
                            reduced_redundancy=self.reduced_redundancy,
                            metadata={'stat': meta, 'md5sum': md5[0]},
                            encrypt_key=self.with_sse)
                        part = 1
                        chunk = None
                        try:
                            for chunk in self.split_sstable(filename):
                                self.log.debug('Uploading part #%d '
                                               '(size: %d)' %
                                               (part, chunk.len,))
                                mp.upload_part_from_file(chunk, part)
                                chunk.close()
                                part += 1
                            part -= 1
                        except Exception as e:
                            self.log.debug(e)
                            self.log.info('Error uploading part %d' % (part,))
                            mp.cancel_upload()
                            if chunk:
                                chunk.close()
                            raise
                        self.log.debug('Uploaded %d parts, '
                                       'completing upload' % (part,))
                        mp.complete_upload()
                        progress(100, 100)
                        delete_if_enabled()
                    else:
                        self.log.debug('Performing monolithic upload')
                        key = bucket.new_key(keyname)
                        key.set_metadata('stat', meta)
                        key.set_metadata('md5sum', md5[0])
                        key.set_contents_from_filename(filename, replace=True,
                                                       cb=progress, num_cb=1,
                                                       md5=md5,
                                                       reduced_redundancy=self.reduced_redundancy,
                                                       encrypt_key=self.with_sse)
                        delete_if_enabled()
                    break
                except:
                    if not os.path.exists(filename):
                        # File was removed? Skip
                        return

                    if r == self.retries - 1:
                        self.log.critical("Failed to upload file contents.")
                        raise
                    bucket = self.get_bucket()
                    continue
        except:
            self.log.error('Error uploading %s\n%s' % (keyname, format_exc()))
            raise

def get_mask(listen_events):
    if not listen_events:
        listen_events = default_listen_events

    mask = 0
    while listen_events:
        mask = mask | getattr(pyinotify, listen_events.pop())

    return mask


def backup_file(handler, filename, filedir, include, log):
    if not filedir.endswith('/'):
        filedir += '/'

    fullpath = os.path.abspath('%s%s' % (filedir, filename))

    if os.path.isdir(fullpath):
        return

    if not include(fullpath):
        log.info('Skipping %s due to exclusion rule' % fullpath)
        return

    handler.add_file(fullpath)


def backup_files(handler, paths, recurse, include, log=default_log):
    for path in paths:
        log.info('Backing up %s' % path)
        if recurse:
            for root, dirs, files in os.walk(path):
                for filename in files:
                    backup_file(handler, filename, root, include, log)
        else:
            for filename in os.listdir(path):
                backup_file(handler, filename, path, include, log)
    return 0


def main():
    parser = argparse.ArgumentParser(description='Tablesnap is a script that '
        'uses inotify to monitor a directory for events and reacts to them by '
        'spawning a new thread to upload that file to Amazon S3, along with '
        'a JSON-formatted list of what other files were in the directory at '
        'the time of the copy.')
    parser.add_argument('bucket', help='S3 bucket')
    parser.add_argument('paths', nargs='+', help='Paths to be watched')
    parser.add_argument('-k', '--aws-key',
        default=os.environ.get('AWS_ACCESS_KEY_ID'),
        help='Amazon S3 Key (default from AWS_ACCESS_KEY_ID in environment)')
    parser.add_argument('-s', '--aws-secret',
        default=os.environ.get('AWS_SECRET_ACCESS_KEY'),
        help='Amazon S3 Secret (default from AWS_SECRET_ACCESS_KEY in environment)')
    parser.add_argument('--aws-region',
        default='us-east-1',
        choices=[region.name for region in boto.s3.regions()],
        help='AWS region to connect to.')
    parser.add_argument('--aws-token',
        default=os.environ.get('AWS_SECURITY_TOKEN'),
        help='Amazon S3 Token (default from AWS_SECURITY_TOKEN in environment)')
    parser.add_argument('-r', '--recursive', action='store_true',
        default=False,
        help='Recursively watch the given path(s)s for new SSTables')
    parser.add_argument('-a', '--auto-add', action='store_true', default=False,
        help='Automatically start watching new subdirectories within path(s)')
    parser.add_argument('-B', '--backup', action='store_true', default=False,
        help='Backup existing files to S3 if they are not already there')
    parser.add_argument('-R', '--reduced-redundancy', action='store_true',
        default=False, help='Use reduced redundancy for files uploaded to S3.')
    parser.add_argument('-p', '--prefix', default='',
        help='Set a string prefix for uploaded files in S3')
    parser.add_argument('--without-index', action='store_true', default=False,
        help='Do not store a JSON representation of the current directory '
             'listing in S3 when uploading a file to S3.')
    parser.add_argument('--with-sse', action='store_true', default=False,
        help='Enable server-side encryption for all uploads to S3.')
    parser.add_argument('--keyname-separator', default=':',
        help='Separator for the keyname between name and path.')
    parser.add_argument('-t', '--threads', default=default_threads,
        help='Number of writer threads')
    parser.add_argument('-n', '--name',
        help='Use this name instead of the FQDN to identify the files from '
             'this host')
    parser.add_argument('--md5-on-start', default=False, action='store_true',
        help='If you want to compute *every file* for its MD5 checksum at '
             'start time, enable this option.')
    parser.add_argument('--listen-events', action='append',
        choices=['IN_MOVED_TO', 'IN_CLOSE_WRITE', 'IN_CREATE'],
        help='Which events to listen on, can be specified multiple times. '
             'Values: IN_MOVED_TO, IN_CLOSE_WRITE, IN_CREATE (default IN_MOVED_TO, IN_CLOSE_WRITE)')
    parser.add_argument('--delete-on-backup', action='store_true', default=False,
                        help='Delete the file upon successful backup.')
    include_group = parser.add_mutually_exclusive_group()
    include_group.add_argument('-e', '--exclude', default=None, nargs='+',
        help='Exclude files matching this regular expression from upload.'
             'WARNING: If neither exclude nor include are defined, then all '
             'files matching "-tmp" are excluded. This option may be used '
             'more than once.')
    include_group.add_argument('-i', '--include', default=None, nargs='+',
        help='Include only files matching this regular expression into upload.'
             'WARNING: If neither exclude nor include are defined, then all '
             'files matching "-tmp" are excluded. This option may be used '
             'more than once.')

    parser.add_argument('--max-upload-size', default=max_file_size,
        help='Max size for files to be uploaded before doing multipart '
             '(default %dM)' % max_file_size)
    parser.add_argument('--multipart-chunk-size', default=default_chunk_size,
        help='Chunk size for multipart uploads (default: %dM or 10%%%% of '
             'free memory if default is not available)' % default_chunk_size)

    parser.add_argument('--retries', default=0, type=int,
        help='If a file upload fails, retry N times before throwing an'
             'exception (default: no retrying)')

    args = parser.parse_args()

    # For backwards-compatibility: If neither exclude nor include are set,
    # then include only files not matching '-tmp'. This was the default
    include = lambda path: path.find('-tmp') == -1
    if args.exclude:
        include = lambda path: not any([re.search(x, path) for x in args.exclude])
    if args.include:
        include = lambda path: any([re.search(x, path) for x in args.include])

    # Check S3 credentials only. We reconnect per-thread to avoid any
    # potential thread-safety problems.

    if args.aws_token:
        s3 = boto.s3.connect_to_region(args.aws_region,
                                       aws_access_key_id=args.aws_key,
                                       aws_secret_access_key=args.aws_secret,
                                       security_token=args.aws_token)
    elif args.aws_key and args.aws_secret:
        s3 = boto.s3.connect_to_region(args.aws_region,
                                       aws_access_key_id=args.aws_key,
                                       aws_secret_access_key=args.aws_secret)
    else:
        s3 = boto.s3.connect_to_region(args.aws_region)

    bucket = s3.get_bucket(args.bucket)

    handler = UploadHandler(threads=args.threads, key=args.aws_key,
                            secret=args.aws_secret, token=args.aws_token,
                            region=args.aws_region,
                            bucket_name=bucket,
                            prefix=args.prefix, name=args.name,
                            include=include,
                            reduced_redundancy=args.reduced_redundancy,
                            with_index=(not args.without_index),
                            with_sse=args.with_sse,
                            delete_on_backup=args.delete_on_backup,
                            keyname_separator=args.keyname_separator,
                            max_size=int(args.max_upload_size),
                            chunk_size=int(args.multipart_chunk_size),
                            md5_on_start=args.md5_on_start,
                            retries=(args.retries + 1),
                            listen_events=args.listen_events)

    wm = pyinotify.WatchManager()
    notifier = pyinotify.Notifier(wm, handler)

    mask = get_mask(args.listen_events)
    for path in args.paths:
        ret = wm.add_watch(path, mask, rec=args.recursive,
                           auto_add=args.auto_add)
        if ret[path] == -1:
            default_log.critical('add_watch failed for %s, bailing out!' %
                                (path))
            return 1

    if args.backup:
        backup_files(handler, args.paths, args.recursive, include)

    notifier.loop()

if __name__ == '__main__':
    sys.exit(main())
