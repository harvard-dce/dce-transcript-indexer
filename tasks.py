
import json
import boto3
import arrow
from time import sleep
from invoke import task, Exit
from os import getenv as env
from dotenv import load_dotenv
from os.path import join, dirname, exists

load_dotenv(join(dirname(__file__), '.env'))

AWS_PROFILE = env('AWS_PROFILE')

if AWS_PROFILE is not None:
    boto3.setup_default_session(profile_name=AWS_PROFILE)


def getenv(var, required=True):
    val = env(var)
    if required and val is None:
        raise Exit("{} not defined".format(var))
    return val


def profile_arg():
    profile = getenv("AWS_PROFILE", False)
    if profile is not None:
        return "--profile {}".format(profile)
    return ""


def stack_exists(ctx):
    cmd = "aws {} cloudformation describe-stacks --stack-name {}" \
        .format(profile_arg(), getenv('STACK_NAME'))
    res = ctx.run(cmd, hide=True, warn=True, echo=False)
    return res.exited == 0


def s3_zipfile_exists(ctx):
    cmd = "aws {} s3 ls s3://{}/{}-function.zip" \
        .format(
            profile_arg(),
            getenv('LAMBDA_CODE_BUCKET'),
            getenv('STACK_NAME')
        )
    res = ctx.run(cmd, hide=True, warn=True, echo=False)
    return res.exited == 0


def vpc_components(ctx):

    vpc_id = getenv("VPC_ID")

    cmd = ("aws {} ec2 describe-subnets --filters "
           "'Name=vpc-id,Values={}' "
           "'Name=tag:aws:cloudformation:logical-id,Values=PrivateSubnet'") \
        .format(profile_arg(), vpc_id)

    res = ctx.run(cmd, hide=1)
    subnet_data = json.loads(res.stdout)
    subnet_id = subnet_data['Subnets'][0]['SubnetId']

    cmd = ("aws {} ec2 describe-security-groups --filters "
           "'Name=vpc-id,Values={}' "
           "'Name=tag:aws:cloudformation:logical-id,Values=OpsworksLayerSecurityGroupCommon'") \
        .format(profile_arg(), vpc_id)
    res = ctx.run(cmd, hide=1)
    sg_data = json.loads(res.stdout)
    sg_id = sg_data['SecurityGroups'][0]['GroupId']

    return subnet_id, sg_id


@task
def create_code_bucket(ctx):
    """
    Create the s3 bucket for storing packaged lambda code
    """
    code_bucket = getenv('LAMBDA_CODE_BUCKET')
    cmd = "aws {} s3 ls {}".format(profile_arg(), code_bucket)
    exists = ctx.run(cmd, hide=True, warn=True)
    if exists.ok:
        print("Bucket exists!")
    else:
        cmd = "aws {} s3 mb s3://{}".format(profile_arg(), code_bucket)
        ctx.run(cmd)


@task
def package(ctx):
    """
    Package the function + dependencies into a zipfile and upload to s3 bucket created via `create-code-bucket`
    """

    build_path = join(dirname(__file__), 'dist')
    function_path = join(dirname(__file__), 'function.py')
    zip_path = join(dirname(__file__), 'function.zip')
    req_file = join(dirname(__file__), 'requirements.txt')
    ctx.run("pip install -U -r {} -t {}".format(req_file, build_path))
    ctx.run("ln -s -f -r -t {} {}".format(build_path, function_path))
    with ctx.cd(build_path):
        ctx.run("zip -r {} .".format(zip_path))
    s3_file_name = "{}-function.zip".format(getenv('STACK_NAME'))
    ctx.run("aws {} s3 cp {} s3://{}/{}".format(
        profile_arg(),
        zip_path,
        getenv("LAMBDA_CODE_BUCKET"),
        s3_file_name)
    )


@task
def update_function(ctx):
    """
    Update the function code with the latest packaged zipfile in s3. Note: this will publish a new Lambda version.
    """
    cmd = ("aws {} lambda update-function-code "
           "--function-name {} --publish --s3-bucket {} --s3-key {}-function.zip"
           ).format(
        profile_arg(),
        getenv('STACK_NAME'),
        getenv('LAMBDA_CODE_BUCKET'),
        getenv('STACK_NAME')
    )
    ctx.run(cmd)


@task
def deploy(ctx):
    """
    Create or update the CloudFormation stack. Note: you must run `package` first.
    """
    template_path = join(dirname(__file__), 'template.yml')

    if not s3_zipfile_exists(ctx):
        print("No zipfile found in s3!")
        print("Did you run the `package` command?")
        raise Exit(1)

    create_or_update = stack_exists(ctx) and 'update' or 'create'
    subnet_id, sg_id = vpc_components(ctx)

    cmd = ("aws {} cloudformation {}-stack "
           "--stack-name {} "
           "--capabilities CAPABILITY_NAMED_IAM "
           "--template-body file://{} "
           "--tags Key=Project,Value=MH Key=OU,Value=DE "
           "--parameters "
           "ParameterKey=VpcSubnetId,ParameterValue='{}' "
           "ParameterKey=VpcSecurityGroupId,ParameterValue='{}' "
           "ParameterKey=LambdaCodeBucket,ParameterValue='{}' "
           "ParameterKey=ElasticsearchHost,ParameterValue='{}' "
           "ParameterKey=ElasticsearchTranscriptIndex,ParameterValue='{}' "
           "ParameterKey=NotificationEmail,ParameterValue='{}' "
           "ParameterKey=TranscriptBucket,ParameterValue='{}' "
           ).format(
        profile_arg(),
        create_or_update,
        getenv("STACK_NAME"),
        template_path,
        subnet_id,
        sg_id,
        getenv('LAMBDA_CODE_BUCKET'),
        getenv('ES_HOST'),
        getenv('ES_TRANSCRIPT_INDEX'),
        getenv('NOTIFICATION_EMAIL'),
        getenv('TRANSCRIPT_BUCKET')
    )
    ctx.run(cmd)


@task
def delete(ctx):
    """
    Delete the CloudFormation stack
    """
    cmd = ("aws {} cloudformation delete-stack "
           "--stack-name {}").format(profile_arg(), getenv('STACK_NAME'))
    if input('are you sure? [y/N] ').lower().strip().startswith('y'):
        ctx.run(cmd)
    else:
        print("not deleting stack")


@task
def setup_bucket_notifications(ctx):
    """
    Create the bucket notification configuration that allows the s3
    transcripts bucket to invoke our lambda function for new objects
    """
    cmd = "aws {} lambda get-function --function-name {}" \
        .format(profile_arg(), getenv('STACK_NAME'))
    res = ctx.run(cmd, hide=1)
    function_data = json.loads(res.stdout)
    function_arn = function_data['Configuration']['FunctionArn']

    s3 = boto3.client('s3')

    config = {
        'Id': "{}-bucket-notifications".format(getenv('STACK_NAME')),
        'LambdaFunctionArn': function_arn,
        'Events': ['s3:ObjectCreated'],
        'Filter': {
            'Key': {
                'FilterRules': [
                    {
                        'Name': 'suffix',
                        'Value': 'json'
                    }

                ]
            }
        }
    }


def create_event(key):
    return json.dumps({
      "Records": [
        {
          "s3": {
            "bucket": {
              "name": getenv('TRANSCRIPT_BUCKET')
            },
            "object": {
              "key": key
            }
          }
        }
      ]
    })


@task
def reindex(ctx, key=None, start=None, end=None):

    def generate_events(start, end):
        s3 = boto3.client('s3')

        params = {
            'Bucket': getenv('TRANSCRIPT_BUCKET'),
            'PaginationConfig': {
                'MaxKeys': 10000,
                'PageSize': 100
            }
        }
        paginator = s3.get_paginator('list_objects')
        resp_itr = paginator.paginate(**params)

        for resp in resp_itr:
            for obj in resp['Contents']:
                if not obj['Key'].endswith('.json'):
                    continue
                if start is not None and obj['LastModified'] < start:
                    continue
                if end is not None and obj['LastModified'] > end:
                    continue
                yield create_event(obj['Key'])

    if key is not None:
        events = [create_event(key)]
    else:

        if start is not None:
            start = arrow.get(start).datetime
        if end is not None:
            end = arrow.get(end).datetime
        else:
            end = arrow.utcnow().datetime

        events = generate_events(start, end)

    for event in events:
        cmd = "aws {} lambda invoke --function-name {} " "--payload '{}' reindex.out" \
            .format(profile_arg(), getenv('STACK_NAME'), event)
        ctx.run(cmd)
        sleep(1)

