from __future__ import print_function

import os
import json
import boto3
import jmespath
import argparse
from datetime import datetime
from elasticsearch import Elasticsearch, helpers, RequestsHttpConnection
from botocore.vendored import requests

s3 = boto3.resource('s3')

def generate_docs(source_data, timestamp, es_index):

    query = 'results[*].results[*] | [].alternatives | []'
    for caption in jmespath.search(query, source_data):

        text = caption['transcript']
        confidence = caption['confidence']
        inpoint = int(caption['timestamps'][0][1])
        outpoint = int(caption['timestamps'][-1][2])

        yield {
            '_index': es_index,
            '_type': 'caption',
            'transcript_id': source_data['id'],
            'generated': timestamp,
            'mpid': source_data['user_token'],
            'text': text,
            'confidence': confidence,
            'inpoint': inpoint,
            'outpoint': outpoint
        }


def lambda_handler(event, context):

    index_date = datetime.utcnow().strftime('%Y-%d-%m')
    es_host = os.environ.get('ES_HOST', 'http://localhost:9200')
    es_index = os.environ.get('ES_INDEX', 'transcripts.' + index_date)
    es_index_pattern = os.environ.get('ES_INDEX_PATTERN', 'transcripts.*')
    es_http_auth = os.environ.get('ES_HTTP_AUTH')

    if 'results_file' in event: # for local testing
        with open(event['results_file'], 'r') as f:
            data = json.load(f)
    else:
        bucket_name = event['Records'][0]['s3']['bucket']['name']
        key = event['Records'][0]['s3']['object']['key']

        if not key.endswith('.json'):
            print("%s doesn't look like a transcript results file. Aborting." % key)
            return

        try:
            obj = s3.Object(bucket_name, key).get()
            data = json.load(obj['Body'])
        except Exception as e:
            print('Error getting object %s from bucket %s: %s' % (key, bucket_name, str(e)))
            raise

    mpid = data['user_token']
    timestamp = datetime.utcnow().isoformat()

    if es_http_auth is not None:
        es = Elasticsearch(
            [es_host],
            connection_class=RequestsHttpConnection,
            http_auth=tuple(es_http_auth.split(':')),
            use_ssl=True,
            verify_certs=False
       )
    else:
        es = Elasticsearch(es_host)

    try:
        res = helpers.bulk(es, generate_docs(data, timestamp, es_index))
        print("Indexed %d captions for mediapackage %s" % (res[0], mpid))
    except Exception, e:
        print("Indexing failure: %s" % str(e))

    # update the index alias for this mediapackage
    alias_filter = { "filter" : { "term": { "generated": timestamp } } }
    alias_actions = {
        "actions" : [
            { "remove" : { "index" : es_index_pattern, "alias" : mpid } },
            { "add" : {
                "index" : es_index_pattern,
                "alias" : mpid,
                "filter": alias_filter['filter']
            } }
        ]
    }
    if es.indices.exists_alias(name=mpid):
        print("Updating alias for %s" % mpid)
        es.indices.update_aliases(alias_actions)
    else:
        print("Creating alias for %s" % mpid)
        es.indices.put_alias(index=es_index_pattern, name=mpid, body=alias_filter)


if __name__ == '__main__':

    parser = argparse.ArgumentParser()
    parser.add_argument('--results-file', type=str, required=True)
    parser.add_argument('--es-host', type=str)
    parser.add_argument('--es-index', type=str)
    parser.add_argument('--es-index-pattern', type=str)
    args = parser.parse_args()

    for arg in ('es_host', 'es_index', 'es_index_pattern'):
        if getattr(args, arg) is not None:
            os.environ[arg.upper()] = getattr(args, arg)

    lambda_handler({'results_file': args.results_file}, None)
