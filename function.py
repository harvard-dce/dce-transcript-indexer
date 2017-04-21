from __future__ import print_function

import os
import json
import boto3
import jmespath
import argparse
from os import getenv as env
from datetime import datetime
from elasticsearch import Elasticsearch, helpers, RequestsHttpConnection

def generate_docs(source_data, timestamp, index_name):

    query = 'results[*].results[*] | [].alternatives | []'
    for caption in jmespath.search(query, source_data):

        text = caption['transcript']
        confidence = caption['confidence']
        inpoint = caption['timestamps'][0][1]
        outpoint = caption['timestamps'][-1][2]

        hesitations = [x for x in caption['timestamps'] if x[0] == '%HESITATION']
        hesitation_length = round(sum([x[2] - x[1] for x in hesitations]), 2)

        yield {
            '_index': index_name,
            '_type': 'caption',
            'transcript_id': source_data['id'],
            'generated': timestamp,
            'mpid': source_data['user_token'],
            'text': text,
            'confidence': confidence,
            'inpoint': inpoint,
            'outpoint': outpoint,
            'length': round(outpoint - inpoint, 2),
            'hesitations': len(hesitations),
            'hesitation_length': hesitation_length
        }


def es_connection():

    es_host = env('ES_HOST', 'http://localhost:9200')
    es_http_auth = env('ES_HTTP_AUTH')

    if es_http_auth is not None:
        return Elasticsearch(
            [es_host],
            connection_class=RequestsHttpConnection,
            http_auth=tuple(es_http_auth.split(':')),
            use_ssl=True,
            verify_certs=False
        )
    else:
        return Elasticsearch(es_host)


def lambda_handler(event, context):

    es_index_prefix = env('ES_INDEX_PREFIX', 'transcripts')

    if 'results_file' in event: # for local testing
        with open(event['results_file'], 'r') as f:
            data = json.load(f)
    else:
        s3 = boto3.resource('s3')
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

    es = es_connection()

    try:
        mpid = data['user_token']
        doc_timestamp = datetime.utcnow().isoformat()
        index_date = datetime.utcnow().strftime('%Y-%m-%d')
        index_name = es_index_prefix + '.' + index_date

        res = helpers.bulk(es, generate_docs(data, doc_timestamp, index_name))
        print("Indexed %d captions for mediapackage %s" % (res[0], mpid))
    except Exception as e:
        if isinstance(e, KeyError) and 'user_token' in str(e):
            print("Results object %s is missing the 'user_token'" % data['id'])
        else:
            print("Indexing of results object %s failed: %s" % (data['id'], str(e)))
        raise

    # update the index alias for this mediapackage
    alias_index_pattern = es_index_prefix + '.*'
    alias_filter = { "filter" : { "term": { "generated": doc_timestamp } } }
    alias_actions = {
        "actions" : [
            { "remove" : { "index" : alias_index_pattern, "alias" : mpid } },
            { "add" : {
                "index" : alias_index_pattern,
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
        es.indices.put_alias(index=alias_index_pattern, name=mpid, body=alias_filter)


if __name__ == '__main__':

    parser = argparse.ArgumentParser()
    parser.add_argument('--results-file', type=str, required=True)
    parser.add_argument('--es-host', type=str)
    parser.add_argument('--es-http-auth', type=str)
    parser.add_argument('--es-index-prefix', type=str)
    args = parser.parse_args()

    for arg in ('es_host', 'es_index_prefix', 'es_http_auth'):
        if getattr(args, arg) is not None:
            os.environ[arg.upper()] = getattr(args, arg)

    lambda_handler({'results_file': args.results_file}, None)
