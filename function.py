import argparse
import json
import boto3
import jmespath
import logging
import aws_lambda_logging
from os import getenv as env
from datetime import datetime
from elasticsearch import Elasticsearch, helpers, RequestsHttpConnection

LOG_LEVEL = env('LOG_LEVEL', 'INFO')
BOTO_LOG_LEVEL = env('BOTO_LOG_LEVEL', 'INFO')
ES_HOST = env('ES_HOST', 'http://localhost:9200')
ES_TRANSCRIPT_INDEX = env('ES_TRANSCRIPT_INDEX', 'transcripts')

logger = logging.getLogger()
s3 = boto3.resource('s3')


def generate_docs(source_data, timestamp, index_name):

    query = 'results[*].results[*] | [].alternatives | []'
    for caption in jmespath.search(query, source_data):

        text = caption['transcript']
        confidence = caption['confidence']
        inpoint = caption['timestamps'][0][1]
        outpoint = caption['timestamps'][-1][2]
        word_count = len([x for x in caption['timestamps'] if x[0] != '%HESITATION'])

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
            'hesitation_length': hesitation_length,
            'word_count': word_count
        }


def es_connection():

    use_ssl = ES_HOST.startswith('https')

    return Elasticsearch(
        [ES_HOST],
        use_ssl=use_ssl,
        verify_certs=False,
        timeout=30
    )


def handler(event, context):

    aws_lambda_logging.setup(LOG_LEVEL, boto_level=BOTO_LOG_LEVEL)

    logger.info(event)
    logger.info(context.__dict__)

    bucket_name = event['Records'][0]['s3']['bucket']['name']
    key = event['Records'][0]['s3']['object']['key']

    if not key.endswith('.json'):
        logger.info("%s doesn't look like a transcript results file. Aborting." % key)
        return

    try:
        obj = s3.Object(bucket_name, key).get()
        data = json.load(obj['Body'])
    except Exception as e:
        logger.info('Error getting object %s from bucket %s: %s' % (key, bucket_name, str(e)))
        raise

    es = es_connection()

    try:
        mpid = data['user_token']
        doc_timestamp = datetime.utcnow().isoformat()

        res = helpers.bulk(es, generate_docs(data, doc_timestamp, ES_TRANSCRIPT_INDEX))
        logger.info("Indexed %d captions for mediapackage %s" % (res[0], mpid))
    except Exception as e:
        if isinstance(e, KeyError) and 'user_token' in str(e):
            logger.error("Results object %s is missing the 'user_token'" % data['id'])
        else:
            logger.exception("Indexing of results object %s failed" % data['id'])
        raise

    # update the index alias for this mediapackage
    alias_name = mpid
    alias_filter = {
        "filter" : {
            'bool': {
                "must": [
                    { "term": { "generated": doc_timestamp } },
                    { "term": { "mpid": mpid } }
                ]
            }
        }
    }
    alias_actions = {
        "actions" : [
            { "remove" : { "index" : ES_TRANSCRIPT_INDEX, "alias" : alias_name } },
            { "add" : {
                "index" : ES_TRANSCRIPT_INDEX,
                "alias" : alias_name,
                "filter": alias_filter['filter']
            } }
        ]
    }
    if es.indices.exists_alias(name=alias_name):
        logger.info("Updating alias %s" % alias_name)
        es.indices.update_aliases(alias_actions)
    else:
        logger.info("Creating alias %s" % alias_name)
        es.indices.put_alias(index=ES_TRANSCRIPT_INDEX, name=alias_name, body=alias_filter)


if __name__ == '__main__':

    parser = argparse.ArgumentParser()
    parser.add_argument('--event', type=argparse.FileType('r'), required=True)
    args = parser.parse_args()

    class FakeContext:
        pass

    event_data = json.load(args.event)
    handler(event_data, FakeContext())
