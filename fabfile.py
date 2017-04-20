
import os
import json
from fabric.api import local

def _lambda_uploader(alias, alias_desc, upload=True):
    variables = {
        "ES_HOST": os.environ['ES_HOST'],
        "ES_HTTP_AUTH": os.environ['ES_HTTP_AUTH']
    }
    cmd = ('lambda-uploader -V --profile ${AWS_DEFAULT_PROFILE} '
          '--role ${TRANSCRIPT_INDEXER_LAMBDA_ROLE} '
          '--variables \'' + json.dumps(variables) + '\' '
          '--alias %s --alias-description "%s"') % (alias, alias_desc)
    if not upload:
        cmd += ' --no-upload'
    local(cmd)

def upload_dev():
    _lambda_uploader('dev', 'dev/testing')

def upload_release():
    _lambda_uploader('release', 'stable release')

def package_dev():
    _lambda_uploader('dev', 'dev/testing', False)

def package_release():
    _lambda_uploader('release', 'stable release', False)
