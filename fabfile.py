
import json
from os import getenv as env
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
