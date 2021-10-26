#!/usr/bin/env python3
#
# Copyright 2014-2017 Spotify AB
# Copyright 2016-2019 The Last Pickle Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
import argparse
import getpass
import json
import logging
import os
import urllib
from urllib.parse import urlparse

import sys
import requests

# The following monkey-patching is required to get json.dumps float rounding nicer,
# for intensity 0.9 we got 0.8999999761581421, but now we get 0.900
from json import encoder

encoder.FLOAT_REPR = lambda o: format(o, '.3f')

USER = getpass.getuser()
COOKIES = {}
HEADERS = {}
DEFAULT_CAUSE = "manual spreaper run"

log_level = logging.WARN
quiet = False
if "-v" in sys.argv or "--verbose" in sys.argv:
    log_level = logging.INFO
if "-vv" in sys.argv:
    log_level = logging.DEBUG
if "-q" in sys.argv or "--quiet" in sys.argv:
    if logging.WARN != log_level:
        print("--verbose and --quiet options can't both be specified")
        exit(1)
    quiet = True

logging.basicConfig(level=log_level, format='  %(levelname)s - %(message)s')

log = logging.getLogger("spreaper")
log.debug("logging initialized, the user running spreaper: {0}".format(USER))


# === ReaperCaller deals with talking to the Reaper =====================================

class ReaperCaller(object):
    """Implements the functionality for calling the Reaper service.
    Keep the CLI specific stuff in ReaperCLI.
    """

    def __init__(self, host_name, host_port, use_ssl):
        self.host_name = host_name
        self.host_port = host_port
        self.base_url = "{0}://{1}:{2}".format(use_ssl and 'https' or 'http',
                                               str(self.host_name), int(self.host_port))

    def _http_req(self, http_method, the_url, cookies={}, params=None):
        http_method = http_method.upper()
        if params is None:
            params = {}
        log.info("making HTTP %s to %s", http_method, the_url)
        if http_method == 'GET':
            r = requests.get(the_url, params=params, cookies=cookies, headers=HEADERS)
        elif http_method == 'POST':
            r = requests.post(the_url, params=params, cookies=cookies, headers=HEADERS)
        elif http_method == 'POST_FORM':
            r = requests.post(the_url, data=params, cookies=cookies, headers=HEADERS)
        elif http_method == 'PUT':
            r = requests.put(the_url, params=params, cookies=cookies, headers=HEADERS)
        elif http_method == 'DELETE':
            r = requests.delete(the_url, params=params, cookies=cookies, headers=HEADERS)
        else:
            assert False, "invalid HTTP method: {0}".format(http_method)
        log.info("HTTP %s return code %s with content of length %s",
                 http_method, r.status_code, len(str(r.text)))
        log.debug("Response content:\n%s", r.text)
        if str(r.status_code) == "403":
            printq("Access to this operation seems to be restricted. Please login before making any call to the API:")
            printq("spreaper login <username>")
        if not str(r.status_code).startswith("2"):
            print(r.text)
        r.raise_for_status()
        if 'Location' in r.headers:
            # any non 303/307 response with a 'Location' header, is a successful mutation pointing to the resource
            if not self.host_port in r.headers['Location']:
                r.headers['Location'] = r.headers['Location'].replace(self.host_name, "%s:%s" % (self.host_name, self.host_port))
            if 'snapshot' in  r.headers['Location'] and not 'cluster' in  r.headers['Location']:
                r.headers['Location'] = r.headers['Location'].replace("snapshot/","snapshot/cluster/")
            print(r.headers['Location'])
            return self._http_req("GET", r.headers['Location'])
        if http_method == 'POST_FORM':
            return r
        else:
            return r.text

    def get(self, endpoint):
        the_url = urllib.parse.urljoin(self.base_url, endpoint)
        return self._http_req("GET", the_url, cookies=COOKIES)

    def post(self, endpoint, **params):
        the_url = urllib.parse.urljoin(self.base_url, endpoint)
        return self._http_req("POST", the_url, params=params)

    def postFormData(self, endpoint, payload):
        the_url = urllib.parse.urljoin(self.base_url, endpoint)
        return self._http_req("POST_FORM", the_url, params=payload)

    def put(self, endpoint, **params):
        the_url = urllib.parse.urljoin(self.base_url, endpoint)
        return self._http_req("PUT", the_url, params=params)

    def delete(self, endpoint, **params):
        the_url = urllib.parse.urljoin(self.base_url, endpoint)
        return self._http_req("DELETE", the_url, params=params)


# === Arguments for commands ============================================================


def _global_arguments(parser, command):
    """Arguments relevant for every CLI command"""
    if 'REAPER_HOST' in os.environ:
        default_reaper_host = os.environ.get('REAPER_HOST')
    else:
        default_reaper_host = 'localhost'

    if 'REAPER_PORT' in os.environ:
        default_reaper_port = os.environ.get('REAPER_PORT')
    else:
        default_reaper_port = '8080'

    group = parser.add_argument_group('global arguments')
    group.add_argument("--reaper-host", default=default_reaper_host,
                       help="hostname of the Reaper service [{0}]".format(default_reaper_host))
    group.add_argument("--reaper-port", default=default_reaper_port,
                       help="port of the Reaper service [{0}]".format(default_reaper_port))
    group.add_argument("--reaper-use-ssl", default=False, action='store_true',
                       help="use https to call Reaper [False]")
    group.add_argument("-v", "--verbose", help="increase output verbosity",
                       action="store_true")
    group.add_argument("-vv", help="extra output verbosity", action="store_true")
    group.add_argument("-q", "--quiet", help="unix mode", action="store_true")
    group.add_argument("--jwt", default=None, help="JSON Web Token (JWT) to authenticate with")
    parser.add_argument(command)


def _arguments_for_status_cluster(parser):
    """Arguments relevant for querying cluster status"""
    parser.add_argument("cluster_name", help="the cluster name")


def _arguments_for_list_schedules(parser):
    """Arguments relevant for listing repair schedules. Almost the same as args for status cluster,
    but allows cluster name to not be present
    """
    parser.add_argument("cluster_name", nargs='?', default=None, help="the cluster name")


def _arguments_for_status_keyspace(parser):
    """Arguments relevant for querying cluster keyspace status"""
    parser.add_argument("cluster_name", help="the cluster name")
    parser.add_argument("keyspace_name", help="the keyspace name")


def _arguments_for_list_segments(parser):
    """Arguments relevant for querying a repair status"""
    parser.add_argument("run_id", help="identifier of the run to fetch segments from")


def _arguments_for_status_repair(parser):
    """Arguments relevant for querying a repair status"""
    parser.add_argument("run_id", help="identifier of the run to fetch more info about")


def _arguments_for_status_schedule(parser):
    """Arguments relevant for querying a repair schedule status"""
    parser.add_argument("schedule_id", help="identifier of the schedule to fetch more info about")


def _arguments_for_add_cluster(parser):
    """Arguments relevant for registering a cluster"""
    parser.add_argument("seed_host", help="the seed host of the Cassandra cluster to be registered")
    parser.add_argument("jmx_port", help="the JMX port of the Cassandra cluster to be registered", default="7199")
    parser.add_argument("--jmx-username", help="JMX username in case authentication is activated", default=None)
    parser.add_argument("--jmx-password", help="JMX password in case authentication is activated", default=None)


def _argument_owner(parser):
    parser.add_argument("--owner", default=USER,
                        help="name of local user calling the Reaper [\"{0}\"]".format(USER))


def _argument_cause(parser):
    parser.add_argument("--cause", default=DEFAULT_CAUSE,
                        help="cause string used for logging and auditing "
                             "purposes [\"{0}\"]".format(DEFAULT_CAUSE))


def _arguments_for_repair(parser):
    _arguments_for_repair_and_schedule(parser)
    parser.add_argument("--dont-start-repair", action='store_true',
                        help="don't start the repair run immediately after registering it")
    _argument_owner(parser)
    _argument_cause(parser)


def _arguments_for_repair_and_schedule(parser):
    """Arguments relevant for registering a repair and optionally triggering it,
    either immediately or in scheduled manner.
    """
    parser.add_argument("cluster_name",
                        help="the name of the target Cassandra cluster")
    parser.add_argument("keyspace_name",
                        help="the keyspace name in the Cassandra cluster")
    parser.add_argument("--tables", default=None,
                        help=("a comma separated list of tables within a keyspace "
                              "in the Cassandra cluster (do not use spaces after commas)"))
    parser.add_argument("--segment-count", default=None,
                        help=("amount of segments to create for the repair run, "
                              "or use the configured default if not given"))
    parser.add_argument("--repair-parallelism", default=None,
                        help=("the repair parallelism level to use for new repair run, "
                              "or use the configured default if not given"))
    parser.add_argument("--intensity", default=None,
                        help=("repair intensity float value (between 0.0 and 1.0), "
                              "or use the configured default if not given"))
    parser.add_argument("--incremental", default="false",
                        help=("Incremental repair (true or false), "
                              "or use the configured default if not given (false)"))
    parser.add_argument("--datacenters", default=None,
                        help=("a comma separated list of datacenters to repair (do not use spaces after commas). "
                              "Cannot be used in conjunction with --nodes."))
    parser.add_argument("--nodes", default=None,
                        help=("a comma separated list of nodes to repair, "
                              "appropriate for repairing a specific list of nodes after an outage (do not use spaces after commas). "
                              "Cannot be used in conjunction with --datacenters"))
    parser.add_argument("--blacklisted-tables", default=None,
                        help=("a comma separated list of tables that must not be repaired "
                              "within the keyspace (do not use spaces after commas)"))
    parser.add_argument("--repair-threads", default=1,
                        help=("The number of threads to use in Cassandra to parallelize repair sessions (from 1 to 4)"))


def _arguments_for_scheduling(parser):
    """Arguments relevant to scheduling repair runs."""
    _arguments_for_repair_and_schedule(parser)
    parser.add_argument("--schedule-days-between", default=None,
                        help="how many days between repair triggerings, e.g. 7 for weekly "
                             "schedule, or 0 for continuous repairs")
    parser.add_argument("--schedule-trigger-time", default=None,
                        help="at which time to trigger the first repair (UTC), "
                             "e.g. \"2015-02-10T15:00:00\"")
    _argument_owner(parser)


def _arguments_for_resume_repair(parser):
    """Arguments relevant for resuming a repair"""
    parser.add_argument("run_id", help="ID of the repair run to resume, start or reattempt")


def _arguments_for_pause_repair(parser):
    """Arguments needed for pausing or resuming a repair"""
    parser.add_argument("run_id", help="ID of the repair run to pause")


def _arguments_for_update_repair(parser):
    """Arguments needed for updating intensity to a paused repair"""
    parser.add_argument("run_id", help="ID of the repair run")
    parser.add_argument("intensity", help="new intensity")


def _arguments_for_abort_repair(parser):
    """Arguments needed for aborting a repair"""
    parser.add_argument("run_id", help="ID of the repair run to abort")


def _arguments_for_abort_segment(parser):
    """Arguments needed for aborting a repair"""
    parser.add_argument("run_id", help="ID of the repair run to abort")
    parser.add_argument("segment_id", help="ID of the segment to abort")


def _arguments_for_start_schedule(parser):
    """Arguments relevant for resuming a repair schedule"""
    parser.add_argument("schedule_id", help="ID of the repair schedule to resume")


def _arguments_for_pause_schedule(parser):
    """Arguments needed for pausing a repair schedule"""
    parser.add_argument("schedule_id", help="ID of the repair schedule to pause")


def _arguments_for_delete_schedule(parser):
    """Arguments needed for deleting a repair schedule"""
    parser.add_argument("schedule_id", help="ID of the repair schedule to delete")
    _argument_owner(parser)


def _arguments_for_list_runs(parser):
    """Arguments needed to filter what repair runs are listed"""
    parser.add_argument("--state", help="Comma separated states used to filter returned runs")
    parser.add_argument("cluster", nargs='?', default=None, help="the cluster name")
    parser.add_argument("keyspace", nargs='?', default=None, help="the keyspace name")


def _arguments_for_list_snapshots(parser):
    """Arguments needed to list snapshots"""
    parser.add_argument("cluster", nargs='?', default=None, help="the cluster name")
    parser.add_argument("--node", default=None,
                        help=("A single node to get the snapshot list from"))


def _arguments_for_create_snapshots(parser):
    """Arguments needed to create snapshots"""
    parser.add_argument("cluster_name", nargs='?', default=None, help="the cluster name")
    parser.add_argument("--node", default=None,
                        help=("A single node to get the snapshot list from"))
    parser.add_argument("--keyspace", default=None,
                        help=("A single keyspace to snapshot"))
    parser.add_argument("--name", default="reaper",
                        help=("Name that will prefix the snapshot"))
    _argument_owner(parser)
    _argument_cause(parser)


def _arguments_for_delete_snapshots(parser):
    """Arguments needed to delete snapshots"""
    parser.add_argument("cluster_name", nargs='?', default=None, help="the cluster name")
    parser.add_argument("snapshot_name", nargs='?', default=None, help="the snapshot name")
    parser.add_argument("--node", default=None,
                        help=("A single node to get the snapshot list from"))


def _arguments_for_login(parser):
    """Arguments needed to login"""
    parser.add_argument("username", help="Username to login with")


def _arguments_for_delete_cluster(parser):
    """Arguments needed for deleting a cluster"""
    parser.add_argument("cluster_name", help="Name of the cluster to delete")
    parser.add_argument("--force", action="store_true", help="Force deletion of the cluster")


def _parse_arguments(command, description, usage=None, extra_arguments=None):
    """Generic argument parsing done by every command"""
    parser = argparse.ArgumentParser(description=description, usage=usage)
    _global_arguments(parser, command)
    if extra_arguments:
        extra_arguments(parser)
    return parser.parse_args()


# === The actual CLI ========================================================================

SPREAPER_DESCRIPTION = \
    """Cassandra Reaper is a centralized, stateful, and highly configurable
    tool for running Cassandra repairs for multi-site clusters.
    This CLI tool is used to control the Cassandra Reaper service through
    its REST API.

    First register your cluster with "add-cluster" command,
    and then start repairing the cluster with "repair" command
    giving the cluster name registered in the Reaper.
    You can also schedule regular repairs for registered clusters.
    """

REAPER_USAGE = SPREAPER_DESCRIPTION + """
Usage: spreaper [<global_args>] <command> [<command_args>]

<command> can be:
    login           Login to Reaper.
    list-clusters   List all registered Cassandra clusters.
    list-runs       List registered repair runs.
    list-schedules  List registered repair schedules.
    list-segments   List all segments for a given repair run.
    status-cluster  Show status of a Cassandra cluster,
                    and any existing repair runs for the cluster.
    status-keyspace Show status of a keyspace in a cluster.
    status-repair   Show status of a repair run.
    status-schedule Show status of a repair schedule.
    add-cluster     Register a cluster.
    delete-cluster  Delete a cluster.
    repair          Create a repair run, optionally starting it. You need to register
                    a cluster into Reaper (add-cluster) before calling repair.
    schedule        Create a repair schedule, choosing the first activation time and days
                    between repair activations. You need to register a cluster into
                    Reaper (add-cluster) before calling this.
    resume-repair   Resume a paused, start a not started or reattempt a failed repair run.
    pause-repair    Pause a repair run.
    update-repair   Update intensity to a repair run.
    abort-repair    Abort a repair run.
    abort-segment   Abort a segment.
    start-schedule  Resume a paused repair schedule.
    pause-schedule  Pause a repair schedule.
    delete-schedule Delete a repair schedule.
    ping            Test connectivity to the Reaper service.
    list-snapshots  List all snapshots for a given cluster or node.
    take-snapshot   Take a snapshot for a whole cluster or a specific node.
    delete-snapshot Delete a named snapshot on a whole cluster or a specific node.
"""


class ReaperCLI(object):
    """Aim of this class is to separate CLI (argparse) specific stuff
    from the actual logic of calling the Reaper service."""

    def __init__(self):
        if len(sys.argv) < 2:
            print(REAPER_USAGE)
            exit(1)
        commands = [arg for arg in sys.argv[1:] if not arg[0].startswith('-')]
        if len(commands) < 1:
            print(REAPER_USAGE)
            exit(1)
        command = commands[0].replace('-', '_')
        if not hasattr(self, command):
            print('Unrecognized command: {0}'.format(command))
            print(REAPER_USAGE)
            exit(1)
        # use dispatch pattern to invoke method with same name as given command
        try:
            getattr(self, command)()

        except requests.exceptions.HTTPError as err:
            print("")
            print("# HTTP request failed with err: {}".format(err))
            exit(2)

    @staticmethod
    def prepare_reaper(command, description, usage=None, extra_arguments=None):
        args = _parse_arguments(command, description, usage, extra_arguments)
        reaper = ReaperCaller(args.reaper_host, args.reaper_port, args.reaper_use_ssl)
        jwt = ReaperCLI.get_jwt(args)
        ReaperCLI.addJwtHeader(jwt)
        return reaper, args

    @staticmethod
    def addJwtHeader(jwt):
        if (jwt != None):
            HEADERS['Authorization'] = 'Bearer ' + jwt

    @staticmethod
    def get_password():
        password = None
        # Use the password from the file if it exists or prompt the user for it
        if os.path.exists(os.path.expanduser('~/.reaper/credentials')):
            with open(os.path.expanduser('~/.reaper/credentials'), 'r') as f:
                password = f.readline().rstrip("\r\n")
        else:
            password = getpass.getpass()

        return password

    @staticmethod
    def get_jwt(args):
        jwt = None
        if args.jwt == None:
            # Use the password from the file if it exists or prompt the user for it
            if os.path.exists(os.path.expanduser('~/.reaper/jwt')):
                with open(os.path.expanduser('~/.reaper/jwt'), 'r') as f:
                    jwt = f.readline().rstrip("\r\n")
        else:
            jwt = args.jwt

        return jwt

    @staticmethod
    def save_jwt(jwt):
        try:
            os.makedirs(os.path.expanduser('~/.reaper'))
            os.chmod(os.path.expanduser('~/.reaper'), 0o700)
        except OSError:
            pass
        with open(os.path.expanduser('~/.reaper/jwt'), 'w+') as f:
            f.write(jwt)
            os.chmod(os.path.expanduser('~/.reaper/jwt'), 0o600)
            printq("# JWT saved")

    def login(self):
        reaper, args = ReaperCLI.prepare_reaper(
            "login",
            "Authenticate to Reaper",
            extra_arguments=_arguments_for_login
        )
        password = ReaperCLI.get_password()
        printq("# Logging in...")
        payload = {'username': args.username, 'password': password, 'rememberMe': False}
        reply = reaper.postFormData("login",
                                    payload=payload)
        # Use shiro's session id to request a JWT
        COOKIES["JSESSIONID"] = reply.cookies["JSESSIONID"]
        jwt = reaper.get("jwt")
        printq("You are now authenticated to Reaper.")
        ReaperCLI.save_jwt(jwt)

        # remove the session id and set the auth header with the JWT
        COOKIES.pop("JSESSIONID", None)

    def ping(self):
        reaper, args = ReaperCLI.prepare_reaper(
            "ping",
            "Test connectivity to the Reaper service."
        )
        printq("# JWT: {}".format(os.environ.get("REAPER_JWT")))
        printq("# Sending PING to Reaper...")
        answer = reaper.get("ping")
        printq("# [Reply] {}".format(answer))
        printq("# Cassandra Reaper is answering in: {0}:{1}".format(args.reaper_host, args.reaper_port))

    def list_clusters(self):
        reaper, args = ReaperCLI.prepare_reaper(
            "list-clusters",
            "List all registered Cassandra clusters."
        )
        printq("# Listing all registered Cassandra clusters")
        cluster_names = json.loads(reaper.get("cluster"))
        if cluster_names:
            printq("# Found {0} clusters:".format(len(cluster_names)))
            for cluster_name in cluster_names:
                print(cluster_name)
        else:
            printq("# No registered clusters found")

    def list_runs(self):
        reaper, args = ReaperCLI.prepare_reaper(
            "list-runs",
            "List registered repair runs",
            extra_arguments=_arguments_for_list_runs
        )
        printq("# Listing repair runs")
        endpoint = 'repair_run'
        if args.state is not None:
            endpoint = '{0}?{1}'.format(endpoint, urllib.urlencode(
                {'state': args.state, 'cluster_name': args.cluster, 'keyspace_name': args.keyspace}))
        repair_runs = json.loads(reaper.get(endpoint))
        printq("# Found {0} repair runs".format(len(repair_runs)))
        print(json.dumps(repair_runs, indent=2, sort_keys=True))

    def list_segments(self):
        reaper, args = ReaperCLI.prepare_reaper(
            "list-segments",
            "List segments for a given repair run",
            extra_arguments=_arguments_for_list_segments
        )
        printq("# Listing segments for repair run '{0}'".format(args.run_id))
        segments = json.loads(reaper.get("repair_run/{0}/segments".format(args.run_id)))
        printq("# Found {0} segments".format(len(segments)))
        print(json.dumps(segments, indent=2, sort_keys=True))

    def list_schedules(self):
        reaper, args = ReaperCLI.prepare_reaper(
            "list-schedules",
            "List registered repair schedules",
            extra_arguments=_arguments_for_list_schedules
        )
        if args.cluster_name:
            printq("# Listing repair schedules for cluster '{0}'".format(args.cluster_name))
            data = json.loads(reaper.get("repair_schedule/cluster/{0}".format(args.cluster_name)))
        else:
            printq("# Listing repair schedules for all clusters")
            data = json.loads(reaper.get("repair_schedule"))
        printq("# Found {0} schedules:".format(len(data)))
        print(json.dumps(data, indent=2, sort_keys=True))

    def list_snapshots(self):
        reaper, args = ReaperCLI.prepare_reaper(
            "list-snapshots",
            "List snapshots for a given cluster or node",
            extra_arguments=_arguments_for_list_snapshots
        )
        if not args.node:
            printq("# Listing snapshots for cluster '{0}'".format(args.cluster))
            snapshots = json.loads(reaper.get("snapshot/cluster/{0}".format(args.cluster)))
            printq("# Found {0} snapshots".format(len(snapshots)))
            print(json.dumps(snapshots, indent=2, sort_keys=True))
        else:
            printq("# Listing snapshots for cluster '{0}' and node '{1}'".format(args.cluster, args.node))
            snapshots = json.loads(reaper.get("snapshot/{0}/{1}".format(args.cluster, args.node)))
            printq("# Found {0} snapshots".format(len(snapshots)))
            print(json.dumps(snapshots, indent=2, sort_keys=True))

    def take_snapshot(self):
        reaper, args = ReaperCLI.prepare_reaper(
            "take-snapshot",
            "Take a snapshot. You need to register a cluster "
            "into Reaper (add-cluster) before calling this.",
            extra_arguments=_arguments_for_create_snapshots
        )
        if not args.cluster_name:
            print("# Please specify a cluster")
            exit(1)
        if args.keyspace:
            if args.node:
                print("# Taking a snapshot on cluster '{0}', node '{1}' and keyspace '{2}', "
                      ).format(args.cluster_name, args.node, args.keyspace)
                reply = reaper.post("snapshot/{0}/{1}".format(args.cluster_name, args.node),
                                    keyspace=args.keyspace,
                                    owner=args.owner, cause=args.cause,
                                    snapshot_name=args.name)
            else:
                print("# Taking a snapshot on cluster '{0}' and keyspace '{1}', "
                      ).format(args.cluster_name, args.keyspace)
                reply = reaper.post("snapshot/cluster/{0}".format(args.cluster_name),
                                    keyspace=args.keyspace,
                                    owner=args.owner, cause=args.cause,
                                    snapshot_name=args.name)
        else:
            if args.node:
                print("# Taking a snapshot on cluster '{0}' and node '{1}', "
                      ).format(args.cluster_name, args.node)
                reply = reaper.post("snapshot/{0}/{1}".format(args.cluster_name, args.node),
                                    keyspace=args.keyspace,
                                    owner=args.owner, cause=args.cause,
                                    snapshot_name=args.name)
            else:
                print("# Taking a snapshot on cluster '{0}', ".format(args.cluster_name))
                reply = reaper.post("snapshot/cluster/{0}".format(args.cluster_name),
                                    keyspace=args.keyspace,
                                    owner=args.owner, cause=args.cause,
                                    snapshot_name=args.name)

        snapshot = json.loads(reply)
        printq("# Snapshot taken")
        print(json.dumps(snapshot, indent=2, sort_keys=True))

    def delete_snapshot(self):
        reaper, args = ReaperCLI.prepare_reaper(
            "delete-snapshot",
            "Delete a snapshot.",
            extra_arguments=_arguments_for_delete_snapshots
        )
        if not args.cluster_name or not args.snapshot_name:
            print("# Please specify a cluster and a snapshot name")
            exit(1)

        if args.node:
            print("# Deleting snapshot '{1}' on cluster '{0}' and node '{2}'"
                  ).format(args.snapshot_name, args.cluster_name, args.node)
            reply = reaper.delete("snapshot/{0}/{1}/{2}".format(args.cluster_name, args.node, args.snapshot_name))
        else:
            print("# Deleting snapshot '{1}' on cluster '{0}', "
                  ).format(args.cluster_name, args.snapshot_name)
            reply = reaper.delete("snapshot/cluster/{0}/{1}".format(args.cluster_name, args.snapshot_name))

        printq("# Snapshot deleted : {0}".format(reply))

    def status_cluster(self):
        reaper, args = ReaperCLI.prepare_reaper(
            "status-cluster",
            "Show status of a Cassandra cluster, and any existing repair runs for the cluster.",
            extra_arguments=_arguments_for_status_cluster
        )
        printq("# Cluster '{0}':".format(args.cluster_name))
        cluster_data = reaper.get("cluster/{0}".format(args.cluster_name))
        print(json.dumps(json.loads(cluster_data), indent=2, sort_keys=True))

    def status_keyspace(self):
        reaper, args = ReaperCLI.prepare_reaper(
            "status-keyspace",
            "Show status of a keyspace in a Cassandra cluster.",
            extra_arguments=_arguments_for_status_keyspace
        )
        printq("# Cluster '{0}', keyspace '{1}':".format(args.cluster_name, args.keyspace_name))
        keyspace_data = reaper.get("cluster/{0}/{1}".format(args.cluster_name, args.keyspace_name))
        print(json.dumps(json.loads(keyspace_data), indent=2, sort_keys=True))

    def status_repair(self):
        reaper, args = ReaperCLI.prepare_reaper(
            "status-repair",
            "Show status of a repair run.",
            extra_arguments=_arguments_for_status_repair
        )
        printq("# Repair run with id '{0}':".format(args.run_id))
        repair_run_data = reaper.get("repair_run/{0}".format(args.run_id))
        print(json.dumps(json.loads(repair_run_data), indent=2, sort_keys=True))

    def status_schedule(self):
        reaper, args = ReaperCLI.prepare_reaper(
            "status-schedule",
            "Show status of a repair schedule.",
            extra_arguments=_arguments_for_status_schedule
        )
        printq("# Repair schedule with id '{0}':".format(args.schedule_id))
        repair_schedule_data = reaper.get("repair_schedule/{0}".format(args.schedule_id))
        print(json.dumps(json.loads(repair_schedule_data), indent=2, sort_keys=True))

    def add_cluster(self):
        reaper, args = ReaperCLI.prepare_reaper(
            "add-cluster",
            "Register a cluster.",
            extra_arguments=_arguments_for_add_cluster
        )
        printq("# Registering Cassandra cluster with seed host: {0} and jmx port: {1}".format(args.seed_host,
                                                                                              args.jmx_port))
        payload = {'seedHost': args.seed_host,
                   'jmxPort': args.jmx_port,
                   'jmxUsername': args.jmx_username,
                   'jmxPassword': args.jmx_password}
        cluster_data = reaper.postFormData("cluster/auth", payload=payload)
        printq("# Registration succeeded:")
        print(json.dumps(json.loads(cluster_data), indent=2, sort_keys=True))

    def delete_cluster(self):
        reaper, args = ReaperCLI.prepare_reaper(
            "delete-cluster",
            "Delete a cluster.",
            extra_arguments=_arguments_for_delete_cluster
        )
        printq("# Removing Cassandra cluster {0} from Reaper".format(args.cluster_name))
        result = reaper.delete("cluster/{0}?force={1}".format(args.cluster_name, args.force))
        printq("# Removal succeeded")

    def repair(self):
        reaper, args = ReaperCLI.prepare_reaper(
            "repair",
            "Create a repair run, optionally starting it. You need to register a cluster "
            "into Reaper (add-cluster) before calling this.",
            extra_arguments=_arguments_for_repair
        )
        if not args.keyspace_name or not args.cluster_name:
            print("# Please specify a cluster, and a keyspace")
            exit(1)
        if args.tables:
            print("# Registering repair run for cluster '{0}', and keyspace '{1}', "
                  "targeting tables: {2}").format(args.cluster_name, args.keyspace_name,
                                                  ",".join(args.tables.split(',')))
            reply = reaper.post("repair_run", clusterName=args.cluster_name,
                                keyspace=args.keyspace_name, tables=args.tables,
                                owner=args.owner, cause=args.cause,
                                segmentCount=args.segment_count,
                                repairParallelism=args.repair_parallelism,
                                intensity=args.intensity,
                                incrementalRepair=args.incremental,
                                nodes=args.nodes,
                                datacenters=args.datacenters,
                                blacklistedTables=args.blacklisted_tables,
                                repairThreadCount=args.repair_threads)
        else:
            if args.blacklisted_tables:
                printq("# Registering repair run for cluster '{0}', and keyspace '{1}', "
                       "targeting all tables in the keyspace except : {2}".format(args.cluster_name,
                                                                                  args.keyspace_name,
                                                                                  ",".join(
                                                                                      args.blacklisted_tables.split(
                                                                                          ','))))
            else:
                printq("# Registering repair run for cluster '{0}', and keyspace '{1}', "
                       "targeting all tables in the keyspace".format(args.cluster_name, args.keyspace_name))

            reply = reaper.post("repair_run", clusterName=args.cluster_name,
                                keyspace=args.keyspace_name, owner=args.owner, cause=args.cause,
                                segmentCount=args.segment_count,
                                repairParallelism=args.repair_parallelism,
                                intensity=args.intensity,
                                incrementalRepair=args.incremental,
                                nodes=args.nodes,
                                datacenters=args.datacenters,
                                blacklistedTables=args.blacklisted_tables,
                                repairThreadCount=args.repair_threads)
        repair_run = json.loads(reply)
        printq("# Repair run with id={0} created".format(repair_run.get('id')))
        if not args.dont_start_repair:
            printq("# Starting repair run with id={0}".format(repair_run.get('id')))
            reply = reaper.put("repair_run/{0}/state/{1}".format(repair_run.get('id'), "RUNNING"))
            repair_run = json.loads(reply)
            print(json.dumps(repair_run, indent=2, sort_keys=True))

    def schedule(self):
        reaper, args = ReaperCLI.prepare_reaper(
            "schedule",
            "Create a repair schedule, choosing the first activation time and days between "
            "repair activations. Notice that continuous repairs are also supported with value 0."
            "You need to register a cluster into Reaper (add-cluster) before calling this.",
            extra_arguments=_arguments_for_scheduling
        )
        if not args.keyspace_name or not args.cluster_name:
            print("# Please specify a cluster, and a keyspace")
            exit(1)
        if args.schedule_days_between is None:
            print("# Please specify repair schedule by giving at least value for " \
                  "schedule-days-between parameter")
            exit(1)
        if args.tables:
            printq("# Registering repair schedule for cluster '{0}', and keyspace '{1}', "
                   "targeting tables: '{2}', with scheduled days between triggerings '{3}', "
                   "and triggering time: {4}".format(
                args.cluster_name, args.keyspace_name, ",".join(args.tables.split(',')),
                args.schedule_days_between, args.schedule_trigger_time))
            reply = reaper.post("repair_schedule", clusterName=args.cluster_name,
                                keyspace=args.keyspace_name, tables=args.tables,
                                owner=args.owner, segmentCountPerNode=args.segment_count,
                                repairParallelism=args.repair_parallelism,
                                intensity=args.intensity,
                                scheduleDaysBetween=args.schedule_days_between,
                                scheduleTriggerTime=args.schedule_trigger_time,
                                incrementalRepair=args.incremental,
                                nodes=args.nodes,
                                datacenters=args.datacenters,
                                blacklistedTables=args.blacklisted_tables,
                                repairThreadCount=args.repair_threads)
        else:
            printq("# Registering repair schedule for cluster '{0}', and keyspace '{1}', "
                   "targeting all tables in the keyspace, with scheduled days between "
                   "triggerings '{2}', and triggering time: {3}".format(
                args.cluster_name, args.keyspace_name,
                args.schedule_days_between, args.schedule_trigger_time))
            reply = reaper.post("repair_schedule", clusterName=args.cluster_name,
                                keyspace=args.keyspace_name, owner=args.owner,
                                segmentCountPerNode=args.segment_count,
                                repairParallelism=args.repair_parallelism,
                                intensity=args.intensity,
                                scheduleDaysBetween=args.schedule_days_between,
                                scheduleTriggerTime=args.schedule_trigger_time,
                                incrementalRepair=args.incremental,
                                nodes=args.nodes,
                                datacenters=args.datacenters,
                                blacklistedTables=args.blacklisted_tables,
                                repairThreadCount=args.repair_threads)
        repair_schedule = json.loads(reply)
        printq("# Repair schedule with id={0} created:".format(repair_schedule.get('id')))
        print(json.dumps(repair_schedule, indent=2, sort_keys=True))

    def resume_repair(self):
        reaper, args = ReaperCLI.prepare_reaper(
            "resume-repair",
            "Resume a paused, start a not started or reattempt a failed repair run.",
            extra_arguments=_arguments_for_resume_repair
        )
        printq("# Resuming a repair run with id: {0}".format(args.run_id))
        reaper.put("repair_run/{0}/state/{1}".format(args.run_id, "RUNNING"))
        printq("# Run '{0}' resumed".format(args.run_id))

    def pause_repair(self):
        reaper, args = ReaperCLI.prepare_reaper(
            "pause-repair",
            "Pause a running repair run.",
            extra_arguments=_arguments_for_pause_repair
        )
        printq("# Pausing a repair run with id: {0}".format(args.run_id))
        reaper.put("repair_run/{0}/state/{1}".format(args.run_id, "PAUSED"))
        printq("# Repair run '{0}' paused".format(args.run_id))

    def update_repair(self):
        reaper, args = ReaperCLI.prepare_reaper(
            "update-repair",
            "Update intensity to a paused repair run.",
            extra_arguments=_arguments_for_update_repair
        )
        printq("# Updating intensity to a repair run with id: {0}; to {1}".format(args.run_id, args.intensity))
        reaper.put("repair_run/{0}/intensity/{1}".format(args.run_id, args.intensity))
        printq("# Repair run '{0}' updated with new intensity {1}".format(args.run_id, args.intensity))

    def abort_repair(self):
        reaper, args = ReaperCLI.prepare_reaper(
            "abort-repair",
            "Abort a repair run.",
            extra_arguments=_arguments_for_abort_repair
        )
        printq("# Aborting a repair run with id: {0}".format(args.run_id))
        reaper.put("repair_run/{0}/state/{1}".format(args.run_id, "ABORTED"))
        printq("# Repair run '{0}' aborted".format(args.run_id))

    def abort_segment(self):
        reaper, args = ReaperCLI.prepare_reaper(
            "abort-segment",
            "Abort a segment.",
            extra_arguments=_arguments_for_abort_segment
        )
        printq("# Aborting a segment with run id: {0} and segment id {1}".format(args.run_id, args.segment_id))
        reaper.post("repair_run/{0}/segments/abort/{1}".format(args.run_id, args.segment_id))
        printq("# Segment '{0}' aborted".format(args.segment_id))

    def start_schedule(self):
        reaper, args = ReaperCLI.prepare_reaper(
            "start-schedule",
            "Resume a paused repair schedule.",
            extra_arguments=_arguments_for_start_schedule
        )
        printq("# Resuming a repair schedule with id: {0}".format(args.schedule_id))
        reaper.put("repair_schedule/{0}".format(args.schedule_id), state="ACTIVE")
        printq("# Schedule '{0}' active".format(args.schedule_id))

    def pause_schedule(self):
        reaper, args = ReaperCLI.prepare_reaper(
            "pause-schedule",
            "Pause a repair schedule.",
            extra_arguments=_arguments_for_pause_schedule
        )
        printq("# Pausing a repair schedule with id: {0}".format(args.schedule_id))
        reaper.put("repair_schedule/{0}".format(args.schedule_id), state="PAUSED")
        printq("# Repair schedule '{0}' paused".format(args.schedule_id))

    def delete_schedule(self):
        reaper, args = ReaperCLI.prepare_reaper(
            "delete-schedule",
            "Delete a repair schedule.",
            extra_arguments=_arguments_for_delete_schedule
        )
        printq("# Deleting a repair schedule with id: {0}".format(args.schedule_id))
        reaper.delete("repair_schedule/{0}".format(args.schedule_id), owner=args.owner)
        printq("# Repair schedule '{0}' deleted".format(args.schedule_id))


def printq(message):
    if not quiet:
        print(message)


if __name__ == '__main__':
    log.info("# ------------------------------------------------------------------------------------")
    log.info("# Report improvements/bugs at https://github.com/thelastpickle/cassandra-reaper/issues")
    log.info("# ------------------------------------------------------------------------------------")
    ReaperCLI()
