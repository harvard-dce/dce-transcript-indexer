
import os
import json
import boto3
import shutil
from invoke import task, Exit
from os import getenv as env
from dotenv import load_dotenv
from os.path import join, dirname, exists
from random import choice

load_dotenv(join(dirname(__file__), '.env'))

aws_profile = env('AWS_DEFAULT_PROFILE')
if aws_profile is not None:
    boto3.setup_default_session(profile_name=aws_profile)


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


def existing_stack(ctx):
    cmd = ("aws {} cloudformation describe-stacks --stack-name {} " 
           "--query 'Stacks[0]'"
           ).format(profile_arg(), getenv('STACK_NAME'))
    res = ctx.run(cmd, hide=True, warn=True, echo=False)
    if res.exited == 0:
        return json.loads(res.stdout)


def s3_zipfile_exists(ctx):
    cmd = "aws {} s3 ls s3://{}/dce-transcript-indexer/{}-function.zip" \
        .format(
            profile_arg(),
            getenv('LAMBDA_CODE_BUCKET'),
            getenv('STACK_NAME')
        )
    res = ctx.run(cmd, hide=True, warn=True, echo=False)
    return res.exited == 0


def find_cidr_base(ctx):
    cmd = ("aws {} ec2 describe-vpcs "
           "--query 'Vpcs[].CidrBlockAssociationSet[].CidrBlock'"
           ).format(profile_arg())
    res = ctx.run(cmd, hide=True, echo=False)
    taken = set([x[:x.rindex('.')] for x in json.loads(res.stdout)])
    possible = set(["10.1.{}".format(x) for x in range(254)])
    return choice(list(possible.difference(taken))) + ".0/24"


@task
def package(ctx):
    """
    Package the function + dependencies into a zipfile and upload to s3 bucket created via `create-code-bucket`
    """

    build_path = join(dirname(__file__), 'dist')
    shutil.rmtree(build_path, ignore_errors=True)
    os.makedirs(build_path)

    print("installing dependencies to build path")
    req_file = join(dirname(__file__), 'function_requirements.txt')
    ctx.run("pip install -U -r {} -t {}".format(req_file, build_path))

    for asset in ['function.py', 'index_template.json']:
        asset_path = join(dirname(__file__), asset)
        ctx.run("ln -s -f {} {}".format(asset_path, build_path))

    print("packaging zip file")
    zip_path = join(dirname(__file__), 'function.zip')
    with ctx.cd(build_path):
        ctx.run("zip -r {} .".format(zip_path))

    print("uploading to s3")
    s3_file_name = "{}-function.zip".format(getenv('STACK_NAME'))
    ctx.run("aws {} s3 cp {} s3://{}/dce-transcript-indexer/{}".format(
        profile_arg(),
        zip_path,
        getenv("LAMBDA_CODE_BUCKET"),
        s3_file_name),
        echo=True
    )


@task
def update_function(ctx):
    """
    Update the function code with the latest packaged zipfile in s3. Note: this will publish a new Lambda version.
    """
    package(ctx)
    s3_file_name = "dce-transcript-indexer/{}-function.zip".format(getenv('STACK_NAME'))
    cmd = ("aws {} lambda update-function-code "
           "--function-name {}-function --publish --s3-bucket {} "
           "--s3-key {}"
           ).format(
        profile_arg(),
        getenv('STACK_NAME'),
        getenv('LAMBDA_CODE_BUCKET'),
        s3_file_name
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

    current_stack = existing_stack(ctx)

    if current_stack is None:
        create_or_update = "create"
        cidr_block = find_cidr_base(ctx)
    else:
        create_or_update = "update"
        try:
            cidr_block = next(
                x["OutputValue"] for x in current_stack["Outputs"]
                if x["OutputKey"] == "VpcCidrBlock"
            )
        except StopIteration:
            print("Existing stack doesn't have a cidr block?!?!")
            raise Exit(1)

    cmd = ("aws {} cloudformation {}-stack "
           "--stack-name {} "
           "--capabilities CAPABILITY_NAMED_IAM "
           "--template-body file://{} "
           "--tags Key=Project,Value=MH Key=OU,Value=DE Key=TranscriptIndexer,Value=1 "
           "--parameters "
           "ParameterKey=CidrBlock,ParameterValue='{}' "
           "ParameterKey=LambdaCodeBucket,ParameterValue='{}' "
           "ParameterKey=NotificationEmail,ParameterValue='{}' "
           "ParameterKey=ElasticsearchInstanceType,ParameterValue='{}' "
           "ParameterKey=LambdaTimeout,ParameterValue='{}' "
           "ParameterKey=LambdaMemory,ParameterValue='{}' "
           ).format(
        profile_arg(),
        create_or_update,
        getenv("STACK_NAME"),
        template_path,
        cidr_block,
        getenv('LAMBDA_CODE_BUCKET'),
        getenv('NOTIFICATION_EMAIL'),
        getenv('ES_INSTANCE_TYPE'),
        getenv('LAMBDA_TIMEOUT'),
        getenv('LAMBDA_MEMORY')
    )

    res = ctx.run(cmd, warn=True, hide=True)
    if res.exited != 0 and "No updates" in res.stderr:
        print("Stack is up-to-date!")
        return
    elif res.exited != 0:
        raise Exit(res.stderr)

    print("Waiting for deployment/update to complete...")
    cmd = ("aws --profile test cloudformation wait stack-{}-complete "
           "--stack-name {}").format(create_or_update, getenv('STACK_NAME'))
    ctx.run(cmd)
    print("Done")


@task
def delete(ctx):
    """
    Delete the CloudFormation stack
    """
    cmd = ("aws {} cloudformation delete-stack "
           "--stack-name {}").format(profile_arg(), getenv('STACK_NAME'))
    if input('are you sure? [y/N] ').lower().strip().startswith('y'):
        ctx.run(cmd)
        print("Waiting for deletion to complete...")
        cmd = ("aws --profile test cloudformation wait stack-delete-complete "
               "--stack-name {}").format(getenv('STACK_NAME'))
        ctx.run(cmd)
        print("Done")
    else:
        print("not deleting stack")


@task
def init_index_template(ctx):
    """
    Populate the Elasticsearch index template. Only has to happen once after initial stack creation.
    """
    event_payload = '{ "init_index_template": true }'
    cmd = ("aws {} lambda invoke --function-name {}-function "
           "--invocation-type RequestResponse --log-type None "
           "--payload '{}' /dev/null"
           ).format(profile_arg(), getenv("STACK_NAME"), event_payload)
    ctx.run(cmd)


@task
def ssh_tunnel(ctx, opsworks_stack):
    """
    Outputs an ssh command to establish a tunnel to the Elasticsearch instance.
    """
    cmd = ("aws {} ec2 describe-instances --output text "
           "--filters \"Name=tag:opsworks:stack,Values={}\" "
           "--query \"Reservations[].Instances[?Tags[?Key=='opsworks:instance' && contains(Value, 'admin1')]].PublicIpAddress\" "
           ).format(profile_arg(), opsworks_stack)
    instance_ip = ctx.run(cmd, hide=True).stdout.strip()
    # get ES endpoint
    cmd = ("aws {} cloudformation describe-stacks --stack-name {} "
           "--query \"Stacks[].Outputs[?OutputKey=='DomainEndpoint'].OutputValue\" "
           "--output text"
           ).format(profile_arg(), env('STACK_NAME'))
    es_endpoint = ctx.run(cmd, hide=True).stdout.strip()
    print("ssh -N -f -L 9200:{}:443 {}".format(es_endpoint, instance_ip))
