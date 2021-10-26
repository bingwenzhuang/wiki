#!/usr/bin/env python
# -*- coding: UTF-8 -*-

import boto3
import json
import fire
import os

'''
更改endpoint命令行
aws rds modify-db-instance --db-instance-identifier jiangmysql --new-db-instance-identifier jiangmysql2 --apply-immediately --profile bowenz 

'''


class AWSRDSOper(object):
    def __init__(self, profilename):
        boto3.setup_default_session(profile_name=profilename)
        self.rds = boto3.client("rds")

    def updateEndpoint(self, oldname, newname):
        '''
        [oldname]: old endpoint name
        [newname]: new endpoint name
        '''
        response = self.rds.modify_db_instance(DBInstanceIdentifier=oldname, ApplyImmediately=True,
                                               NewDBInstanceIdentifier=newname)
        if response["ResponseMetadata"]["HTTPStatusCode"]:
            print ("Modified Successfull")
        else:
            print(response)

    def loadParas(self, groupname, filename):
        '''
        [groupname]: standard-mysql56
        [filename]: standard-mysql56.json
        '''
        paras_list = []
        paras_pages = self.rds.get_paginator("describe_db_parameters")
        for page in paras_pages.paginate(DBParameterGroupName=groupname):
            paras_list += page["Parameters"]

        paras = json.dumps(paras_list, sort_keys=True, indent=4, separators=(',', ': '))
        with open(filename, 'w') as json_file:
            json_file.write(paras)

        print ("parametername %s load completed,please see %s" % (groupname, filename))

    def updateParas(self, groupname, filename):
        '''
        [groupname]: standard-mysql56
        [filename]: standard-mysql56.json
        '''
        with open(filename) as json_file:
            parameters = json.load(json_file)

        for parameter in parameters:
            if parameter["IsModifiable"] and parameter.has_key("ParameterValue") and parameter[
                "ApplyMethod"]:  ##ApplyMethod is immediate or pending-reboot
                response = self.rds.modify_db_parameter_group(DBParameterGroupName=groupname, Parameters=[parameter])
                print ("parameter name: %s , parameter status: %s " % (
                    parameter["ParameterName"], response["ResponseMetadata"]["HTTPStatusCode"]))

    def compareParas(self, filenameOne, filenameTwo):
        '''
        [filenameOne]: standard-mysql56.json
        [filenameTwo]: standard-mysql57.json
        '''

        with open(filenameOne) as json_file:
            parametersOne = json.load(json_file)

        one_dict = {}
        for parameter in parametersOne:
            if parameter["IsModifiable"] and parameter.has_key("ParameterValue"):
                one_dict[parameter["ParameterName"]] = (parameter["ParameterValue"], parameter["ApplyType"])

        with open(filenameTwo) as json_file:
            parametersTwo = json.load(json_file)

        two_dict = {}
        for parameter in parametersTwo:
            if parameter["IsModifiable"] and parameter.has_key("ParameterValue"):
                two_dict[parameter["ParameterName"]] = (parameter["ParameterValue"], parameter["ApplyType"])

        print ("%-40s %-32s %-32s %-10s" % (
            "==parameter name==", "==" + os.path.basename(filenameOne) + "==",
            "==" + os.path.basename(filenameTwo) + "==", "==ApplyType=="))
        for k, v in self.__dict_compare__(one_dict, two_dict).items():
            print ("%-40s %-32s %-32s %-10s" % (k, v[0], v[1], v[2]))

    def __dict_compare__(self, d1, d2):
        d1_keys = set(d1.keys())
        d2_keys = set(d2.keys())
        intersect_keys = d1_keys.intersection(d2_keys)
        added = {o: (d1[o][0], None, d1[o][1]) for o in d1_keys - d2_keys}
        removed = {o: (None, d2[o][0], d2[o][1]) for o in d2_keys - d1_keys}
        modified = {o: (d1[o][0], d2[o][0], d1[o][1]) for o in intersect_keys if d1[o][0] != d2[o][0]}
        # same = set(o for o in intersect_keys if d1[o] == d2[o])
        return dict(added.items() + removed.items() + modified.items())


if __name__ == '__main__':
    fire.Fire(AWSRDSOper)
