import re
import ssl
import time
import json
import argparse
import logging
import requests
import aws_lambda_logging
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
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
CAPTION_REQUEST_RETRIES = env('CAPTION_REQUEST_RETRIES', 3)
CAPTIONS_XML_NS = {'ttaf1': 'http://www.w3.org/2006/04/ttaf1'}
TAB_NEWLINE_REPLACE = re.compile("[\\n\\t]+")

logger = logging.getLogger()

http_session = requests.Session()
retry = Retry(
    total=CAPTION_REQUEST_RETRIES,
    read=CAPTION_REQUEST_RETRIES,
    connect=CAPTION_REQUEST_RETRIES,
    backoff_factor=0.3
)
adapter = HTTPAdapter(max_retries=retry)
http_session.mount("https://", adapter)

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

    aws_lambda_logging.setup(
        LOG_LEVEL,
        boto_level=BOTO_LOG_LEVEL,
        aws_request_id=context.aws_request_id
    )

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

    t0 = time.time()
    try:
        resp = http_session.get(captions_url, timeout=5)
        resp.raise_for_status()
    except Exception as e:
        logger.exception("Error getting from {}: {}".format(captions_url, e))
        raise
    else:
        logger.info("Caption request successful: {}".format(resp.status_code))
    finally:
        t1 = time.time()
        logger.info("Caption request took {} seconds".format(t1 - t0))

    xml_str = resp.text
    xml_str = TAB_NEWLINE_REPLACE.sub(" ", xml_str)
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
        if cap.text is None:
            continue
        begin = cap.attrib['begin']
        (hours, minutes, seconds) = (float(x) for x in begin.split(':'))
        td = timedelta(hours=hours, minutes=minutes, seconds=seconds)
        doc['text'] += cap.text + " "
        doc['captions'].append({
            'text': cap.text.strip(),
            'begin': td.seconds
        })

    logger.debug(doc)

    t0 = time.time()
    try:
        logger.info("Indexing doc id: {}".format(doc_id))
        resp = es.index(
            id=doc_id,
            index=index_name,
            doc_type="_doc",
            body=doc,
            timeout=5
        )
    except Exception as e:
        logger.exception("Indexing to {} failed: {}".format(index_name, e))
        raise
    finally:
        t1 = time.time()
        logger.info("Indexing request took {} seconds".format(t1 - t0))


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
