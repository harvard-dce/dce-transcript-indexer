import re
import sys
import ssl
import time
import json
import boto3
import signal
import argparse
import logging
import aws_lambda_logging
import webvtt
from urllib.parse import urlparse
from os import path, getenv as env
from datetime import datetime, timedelta
from elasticsearch import Elasticsearch
from elasticsearch.connection import create_ssl_context
import xml.etree.ElementTree as ET
from contextlib import ContextDecorator
from io import StringIO

LOG_LEVEL = env('LOG_LEVEL', 'INFO')
BOTO_LOG_LEVEL = env('BOTO_LOG_LEVEL', 'INFO')
ES_HOST = env('ES_HOST', 'https://localhost:9200')
LAMBDA_TASK_ROOT = env('LAMBDA_TASK_ROOT')
CAPTION_REQUEST_RETRIES = env('CAPTION_REQUEST_RETRIES', 3)
CAPTIONS_XML_NS = {'ttaf1': 'http://www.w3.org/2006/04/ttaf1'}
TAB_NEWLINE_REPLACE = re.compile("[\\n\\t]+")

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
s3 = boto3.resource('s3')


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

class time_this(ContextDecorator):
    def __init__(self, label):
        self.label = label

    def __enter__(self):
        self.t0 = time.time()

    def __exit__(self, *exc):
        t1 = time.time()
        logger.info("{} took {} seconds".format(self.label, t1 - self.t0))

def timeout_handler(_signal, _frame):
    '''Handle SIGALRM'''
    raise Exception('Time exceeded')

def append_to_doc(doc, begin, text):
    (hours, minutes, seconds) = (float(x) for x in begin.split(':'))
    td = timedelta(hours=hours, minutes=minutes, seconds=seconds)
    doc['text'] += text + " "
    doc['captions'].append({
        'text': text.strip(),
        'begin': td.seconds
    })
    return doc

signal.signal(signal.SIGALRM, timeout_handler)

def handler(event, context):

    aws_lambda_logging.setup(
        LOG_LEVEL,
        boto_level=BOTO_LOG_LEVEL,
        aws_request_id=context.aws_request_id
    )

    # Setup alarm for remaining runtime minus a second
    time_remaining = int(context.get_remaining_time_in_millis() / 1000) - 1
    logger.info("Setting SIGALRM handler for {}s".format(time_remaining))
    signal.alarm(int(context.get_remaining_time_in_millis() / 1000) - 1)

    # one-time index template setup handling
    if "init_index_template" in event:
        return init_index_template()

    index_name = event["indexName"]
    captions_url = event["captionsUrl"]
    mpid = event["mpid"]
    series_id = event["seriesId"]
    format = event["format"] if "format" in event and event["format"] else "dfxp"

    logger.info(event)

    if not index_name.endswith("-transcripts"):
        raise InvalidTranscriptIndexName("Index name must match *-transcrips")

    doc_id = "{}-{}".format(mpid, series_id)
    doc = {
        "mpid": mpid,
        "series_id": series_id,
        "text": "",
        "captions": [],
        "index_ts": datetime.utcnow().isoformat()
    }

    with time_this("caption request"):
        try:
            parsed_url = urlparse(captions_url)
            bucket = parsed_url.netloc.split('.')[0]
            key = parsed_url.path[1:]
            obj = s3.Object(bucket, key).get()
            captions_str = obj['Body'].read()
            logger.info("Caption request successful")
        except Exception as e:
            logger.exception("Error getting from {}: {}".format(captions_url, e))
            raise

    if format == "webvtt":
        # WebVtt
        with time_this("webvtt caption generation"):
            buffer = StringIO(captions_str.decode())
            for cap in webvtt.read_buffer(buffer):
                if cap.text is None:
                    continue
                append_to_doc(doc, cap.start, cap.text)    

    else:
        # Dfxp
        with time_this("xml caption parsing"):
            captions_str = TAB_NEWLINE_REPLACE.sub(" ", captions_str.decode())
            root = ET.fromstring(captions_str)
            captions = root.findall('.//ttaf1:p', namespaces=CAPTIONS_XML_NS)

        with time_this("xml doc generation"):
            for cap in captions:
                if cap.text is None:
                    continue
                append_to_doc(doc, cap.attrib['begin'], cap.text)

    with time_this("index request"):
        try:
            logger.info("Indexing doc id: {}".format(doc_id))
            resp = es.index(
                id=doc_id,
                index=index_name,
                doc_type="_doc",
                body=doc
            )
        except Exception as e:
            logger.exception("Indexing to {} failed: {}".format(index_name, e))
            raise


if __name__ == '__main__':

    parser = argparse.ArgumentParser()
    parser.add_argument('--url', required=True)
    parser.add_argument('--mpid', required=True)
    parser.add_argument('--series-id', required=True)
    parser.add_argument('--index-name', required=True)
    parser.add_argument('--format', required=False)
    args = parser.parse_args()

    stdout_handler = logging.StreamHandler(sys.stdout)
    logger.addHandler(stdout_handler)
    logger.info("args: {}".format(args))

    class FakeContext:
        aws_request_id = "testing invocation"

        def get_remaining_time_in_millis(self):
            return 30000

    event_data = {
        "captionsUrl": args.url,
        "mpid": args.mpid,
        "seriesId": args.series_id,
        "indexName": args.index_name,
        "format": args.format
    }
    handler(event_data, FakeContext())
