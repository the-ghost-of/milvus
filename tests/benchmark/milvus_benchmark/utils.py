# -*- coding: utf-8 -*-
import time
import logging
import string
import random
import requests
import json
import os
from yaml.representer import SafeRepresenter
# from yaml import full_load, dump
import yaml
import tableprint as tp
import config

logger = logging.getLogger("milvus_benchmark.utils")


def timestr_to_int(time_str):
    """ Parse the test time set in the yaml configuration file and convert it to int type """
    if isinstance(time_str, int) or time_str.isdigit():
        time_int = int(time_str)
    elif time_str.endswith("s"):
        time_int = int(time_str.split("s")[0])
    elif time_str.endswith("m"):
        time_int = int(time_str.split("m")[0]) * 60
    elif time_str.endswith("h"):
        time_int = int(time_str.split("h")[0]) * 60 * 60
    else:
        raise Exception(f"{time_str} not support")
    return time_int


class literal_str(str): pass


def change_style(style, representer):
    def new_representer(dumper, data):
        scalar = representer(dumper, data)
        scalar.style = style
        return scalar

    return new_representer


# from yaml.representer import SafeRepresenter

# represent_str does handle some corner cases, so use that
# instead of calling represent_scalar directly
represent_literal_str = change_style('|', SafeRepresenter.represent_str)

yaml.add_representer(literal_str, represent_literal_str)


def retry(times):
    """
    This decorator prints the execution time for the decorated function.
    """
    def wrapper(func):
        def newfn(*args, **kwargs):
            attempt = 0
            while attempt < times:
                try:
                    result = func(*args, **kwargs)
                    if result:
                        break
                    else:
                        raise Exception("Result false")
                except Exception as e:
                    logger.info(str(e))
                    time.sleep(3)
                    attempt += 1
            return result
        return newfn
    return wrapper


def convert_nested(dct):
    def insert(dct, lst):
        for x in lst[:-2]:
            dct[x] = dct = dct.get(x, {})
        dct.update({lst[-2]: lst[-1]})

            # empty dict to store the result


    result = {}

    # create an iterator of lists  
    # representing nested or hierarchial flow 
    lsts = ([*k.split("."), v] for k, v in dct.items())

    # insert each list into the result 
    for lst in lsts:
        insert(result, lst)
    return result


def get_unique_name(prefix=None):
    if prefix is None:
        prefix = "distributed-benchmark-test-"
    return prefix + "".join(random.choice(string.ascii_letters + string.digits) for _ in range(8)).lower()


def get_current_time():
    """ Return current time"""
    return time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())


def print_table(headers, columns, data):
    bodys = []
    for index, value in enumerate(columns):
        tmp = [value]
        tmp.extend(data[index])
        bodys.append(tmp)
    tp.table(bodys, headers)


def get_deploy_mode(deploy_params):
    """
    Get the server deployment mode set in the yaml configuration file
    single, cluster, cluster_3rd
    """
    deploy_mode = None
    if deploy_params:
        milvus_params = deploy_params["milvus"] if "milvus" in deploy_params else None
        if not milvus_params:
            deploy_mode = config.DEFUALT_DEPLOY_MODE
        elif "deploy_mode" in milvus_params:
            deploy_mode = milvus_params["deploy_mode"]
            if deploy_mode not in [config.SINGLE_DEPLOY_MODE, config.CLUSTER_DEPLOY_MODE, config.CLUSTER_3RD_DEPLOY_MODE]:
                raise Exception(f"Invalid deploy mode: {deploy_mode}")
    return deploy_mode


def get_server_tag(deploy_params):
    """
    Get service deployment configuration
    e.g.:
        server:
          server_tag: "8c16m"
    """
    server_tag = ""
    if deploy_params and "server" in deploy_params:
        server = deploy_params["server"]
        server_tag = server["server_tag"] if "server_tag" in server else ""
    return server_tag


def get_server_resource(deploy_params):
    return (
        deploy_params["server_resource"]
        if deploy_params and "server_resource" in deploy_params
        else {}
    )


def dict_update(source, target):
    for key, value in source.items():
        if isinstance(value, dict) and key in target:
            dict_update(source[key], target[key])
        else:
            target[key] = value
    return target


def update_dict_value(server_resource, values_dict):
    if not isinstance(server_resource, dict) or not isinstance(values_dict, dict):
        return values_dict

    return dict_update(server_resource, values_dict)


def search_param_analysis(vector_query, filter_query):
    """ Search parameter adjustment, applicable pymilvus version >= 2.0.0rc7.dev24 """

    if "vector" in vector_query:
        vector = vector_query["vector"]
    else:
        logger.error("[search_param_analysis] vector not in vector_query")
        return False

    data = []
    anns_field = ""
    param = {}
    limit = 1
    if isinstance(vector, dict) and len(vector) == 1:
        for key in vector:
            anns_field = key
            data = vector[key]["query"]
            param = {"metric_type": vector[key]["metric_type"],
                     "params": vector[key]["params"]}
            limit = vector[key]["topk"]
    else:
        logger.error(
            f"[search_param_analysis] vector not dict or len != 1: {str(vector)}"
        )
        return False

    expression = None
    if isinstance(filter_query, list) and len(filter_query) != 0 and "range" in filter_query[0]:
        filter_range = filter_query[0]["range"]
        if isinstance(filter_range, dict) and len(filter_range) == 1:
            for key in filter_range:
                field_name = filter_range[key]
                expression = None
                if 'GT' in filter_range[key]:
                    exp1 = f"{field_name} > {str(filter_range[key]['GT'])}"
                    expression = exp1
                if 'LT' in filter_range[key]:
                    exp2 = f"{field_name} < {str(filter_range[key]['LT'])}"
                    expression = f'{expression} && {exp2}' if expression else exp2
        else:
            logger.error(
                f"[search_param_analysis] filter_range not dict or len != 1: {str(filter_range)}"
            )
            return False
    return {
        "data": data,
        "anns_field": anns_field,
        "param": param,
        "limit": limit,
        "expression": expression,
    }


def modify_file(file_path_list, is_modify=False, input_content=""):
    """
    file_path_list : file list -> list[<file_path>]
    is_modify : does the file need to be reset
    input_content ：the content that need to insert to the file
    """
    if not isinstance(file_path_list, list):
        print("[modify_file] file is not a list.")

    for file_path in file_path_list:
        folder_path, file_name = os.path.split(file_path)
        if not os.path.isdir(folder_path):
            print(f"[modify_file] folder({folder_path}) is not exist.")
            os.makedirs(folder_path)

        if not os.path.isfile(file_path):
            print(f"[modify_file] file({file_path}) is not exist.")
            os.mknod(file_path)
        elif is_modify is True:
            print(f"[modify_file] start modifying file({file_path})...")
            with open(file_path, "r+") as f:
                f.seek(0)
                f.truncate()
                f.write(input_content)
                f.close()
            print(f"[modify_file] file({file_path_list}) modification is complete.")


def read_json_file(file_name):
    """ Return content of json file """
    with open(file_name) as f:
        file_dict = json.load(f)
    return file_dict


def get_token(url):
    """ get the request token and return the value """
    rep = requests.get(url)
    data = json.loads(rep.text)
    if 'token' in data:
        token = data['token']
    else:
        token = ''
        print("Can not get token.")
    return token


def get_tags(url, token):
    headers = {
        'Content-type': "application/json",
        "charset": "UTF-8",
        "Accept": "application/vnd.docker.distribution.manifest.v2+json",
        "Authorization": f"Bearer {token}",
    }
    try:
        rep = requests.get(url, headers=headers)
        data = json.loads(rep.text)

        tags = []
        if 'tags' in data:
            tags = data["tags"]
        else:
            print("Can not get the tag list")
        return tags
    except:
        print("Can not get the tag list")
        return []


def get_master_tags(tags_list):
    _list = []
    tag_name = "master"

    if not isinstance(tags_list, list):
        print("tags_list is not a list.")
        return _list

    _list.extend(
        tag
        for tag in tags_list
        if tag_name in tag and tag != f"{tag_name}-latest"
    )
    return _list


def get_config_digest(url, token):
    headers = {
        'Content-type': "application/json",
        "charset": "UTF-8",
        "Accept": "application/vnd.docker.distribution.manifest.v2+json",
        "Authorization": f"Bearer {token}",
    }
    try:
        rep = requests.get(url, headers=headers)
        data = json.loads(rep.text)

        digest = ''
        if 'config' in data and 'digest' in data["config"]:
            digest = data["config"]["digest"]
        else:
            print("Can not get the digest")
        return digest
    except:
        print("Can not get the digest")
        return ""


def get_latest_tag(limit=200):
    """ get the latest tag of master """

    auth_url = ""
    tags_url = ""
    tag_url = ""
    master_latest = "master-latest"

    master_latest_digest = get_config_digest(tag_url + master_latest, get_token(auth_url))
    tags = get_tags(tags_url, get_token(auth_url))
    tag_list = get_master_tags(tags)

    latest_tag = ""
    for i in range(1, len(tag_list) + 1):
        tag_name = str(tag_list[-i])
        tag_digest = get_config_digest(tag_url + tag_name, get_token(auth_url))
        if tag_digest == master_latest_digest:
            latest_tag = tag_name
            break
        if i > limit:
            break

    if latest_tag == "":
        raise print("Can't find the latest image name")
    print(f"The image name used is {str(latest_tag)}")
    return latest_tag


def get_image_tag():
    url = ""
    headers = {"accept": "application/json"}
    try:
        rep = requests.get(url, headers=headers)
        data = json.loads(rep.text)
        tag_name = data[0]["tags"][0]["name"]
        print(f"[benchmark update] The image name used is {str(tag_name)}")
        return tag_name
    except:
        print("Can not get the tag list")
        return "master-latest"


def dict_recursive_key(_dict, key=None):
    if isinstance(_dict, dict):
        key_list = list(_dict.keys())

        for k in key_list:
            if isinstance(_dict[k], dict):
                dict_recursive_key(_dict[k], key)

            if (
                key is None
                and _dict[k] is key
                or key is not None
                and _dict[k] == key
            ):
                del _dict[k]
    return _dict
