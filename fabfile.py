
import json
from os import path, getenv as env
from fabric.api import local, task
from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv())

def _lambda_uploader(alias, alias_desc, upload=True):
    variables = {
        "ES_HOST": env('ES_HOST'),
        "ES_HTTP_AUTH": env('ES_HTTP_AUTH'),
        "ES_INDEX_PREFIX": env('ES_INDEX_PREFIX')
    }
    cmd = ('lambda-uploader -V --profile ${AWS_DEFAULT_PROFILE} '
          '--role ${TRANSCRIPT_INDEXER_LAMBDA_ROLE} '
          '--variables \'' + json.dumps(variables) + '\' '
          '--alias %s --alias-description "%s"') % (alias, alias_desc)
    if not upload:
        cmd += ' --no-upload'
    local(cmd)

@task
def upload_dev():
    _lambda_uploader('dev', 'dev/testing')

@task
def upload_release():
    _lambda_uploader('release', 'stable release')

@task
def package_dev():
    _lambda_uploader('dev', 'dev/testing', False)

@task
def package_release():
    _lambda_uploader('release', 'stable release', False)

@task
def put_template():
    from function import es_connection
    es = es_connection()
    index_prefix = env('ES_INDEX_PREFIX', 'transcripts')
    template_name = 'dce-' + index_prefix
    with open(path.join(path.abspath(path.dirname(__file__)), 'index_template.json')) as f:
        template_body = json.load(f)
    template_body['template'] = index_prefix + '.*'
    es.indices.put_template(template_name, body=template_body, create=True)
