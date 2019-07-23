import argparse
import ssl
import json
from botocore.vendored import requests
import logging
import aws_lambda_logging
from os import path, getenv as env
from datetime import datetime, timedelta
from elasticsearch import Elasticsearch
from elasticsearch.connection import create_ssl_context
import xml.etree.ElementTree as ET

import urllib3
urllib3.disable_warnings()

LOG_LEVEL = env('LOG_LEVEL', 'INFO')
BOTO_LOG_LEVEL = env('BOTO_LOG_LEVEL', 'INFO')
ES_HOST = env('ES_HOST', 'https://localhost:9200')
LAMBDA_TASK_ROOT = env('LAMBDA_TASK_ROOT')
CAPTIONS_XML_NS = {'ttaf1': 'http://www.w3.org/2006/04/ttaf1'}

logger = logging.getLogger()

def es_connection(host=ES_HOST):

    ssl_context = create_ssl_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE

    return Elasticsearch(
        [host],
        scheme="https",
        ssl_context=ssl_context,
        timeout=5
    )

es = es_connection()


def init_index_template():
    template_path = path.join(LAMBDA_TASK_ROOT, 'index_template.json')
    with open(template_path, 'r') as f:
        template_body = json.load(f)
    put_template_params = {
        "name": "transcripts",
        "create": True,
        "body": template_body
    }
    resp = es.indices.put_template(**put_template_params)
    logger.info("put template response: {}".format(resp))
    return resp

class InvalidTranscriptIndexName(Exception):
    pass

def handler(event, context):

    aws_lambda_logging.setup(LOG_LEVEL, boto_level=BOTO_LOG_LEVEL)

    # one-time index template setup handling
    if "init_index_template" in event:
        return init_index_template()

    index_name = event["indexName"]
    captions_url = event["captionsUrl"]
    mpid = event["mpid"]
    series_id = event["seriesId"]

    logger.info(event)

    if not index_name.endswith("-transcripts"):
        raise InvalidTranscriptIndexName("Index name must match *-transcrips")

    try:
        resp = requests.get(captions_url, timeout=5)
        resp.raise_for_status()
    except Exception as e:
        logger.exception("Error getting from {}: {}".format(captions_url, e))
        raise

    xml_str = resp.content
    root = ET.fromstring(xml_str)
    captions = root.findall('.//ttaf1:p', namespaces=CAPTIONS_XML_NS)

    doc_id = "{}-{}".format(mpid, series_id)
    doc = {
        "mpid": mpid,
        "series_id": series_id,
        "text": "",
        "captions": [],
        "index_ts": datetime.utcnow().isoformat()
    }

    for cap in captions:
        begin = cap.attrib['begin']
        (hours, minutes, seconds) = (float(x) for x in begin.split(':'))
        td = timedelta(hours=hours, minutes=minutes, seconds=seconds)
        doc['text'] += cap.text + " "
        doc['captions'].append({
            'text': cap.text,
            'begin': td.seconds
        })

    logger.debug(doc)

    try:
        resp = es.index(
            id=doc_id,
            index=index_name,
            doc_type="_doc",
            body=doc
        )
        logger.debug({'index response': resp})

    except Exception as e:
        logger.exception("Indexing to {} failed: {}".format(index_name, e))
        raise


if __name__ == '__main__':

    parser = argparse.ArgumentParser()
    parser.add_argument('--url', required=True)
    parser.add_argument('--mpid', required=True)
    parser.add_argument('--series-id', required=True)
    parser.add_argument('--index-name', required=True)
    args = parser.parse_args()

    class FakeContext:
        pass

    event_data = {
        "captionsUrl": args.url,
        "mpid": args.mpid,
        "seriesId": args.series_id,
        "indexName": args.index_name
    }
    handler(event_data, FakeContext())
