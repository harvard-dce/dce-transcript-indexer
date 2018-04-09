# dce-transcript-indexer

AWS cloudformation stack to provide an Opencast transcript indexing/searching service

This project sets up a Cloudformatoin stack containing the following:

* VPC containing one private subnet and one security group
* Single-node ElasticSearch instance within the VPC
* python 3.6 Lamda function that accepts incoming transcript xml documents and indexes them
* a set of [pyinvoke](http://www.pyinvoke.org/) commandline tasks for packaing, deploying and configuring

### Getting started

`pip install requirements.txt` followed by `invoke -l` to confirm setup and show the available commands.

Copy `example.env` to `.env` and update the settings. See inline comments. If you're working with an existing stack
you'll need to update `.env` with the values the stack was built with. 

### Commands

These operations are executed via the `invoke` tool. Run `invoke -l` to list in the terminal.

##### `invoke package`

Create a `function.zip` archive of the lambda python function code along with all it's package dependencies.

##### `invoke deploy`

You must run `invoke package` before running this command if building a new stack.

This will create a new Cloudformation stack or update an existing one. See the `template.yml` for a full picture of
the resources created.

##### `invoke init-index-template`

You must run this after the initial deploy. It invokes a special mode of the lambda function to create an index 
template entry in Elasticsearch. 

You can also run this after making changes to the index template definition, but you'll
need to run `invoke update-function` first as the index template definition is packaged and uploaded as part of the
lambda function.

##### `invoke update-function`

Repackage, upload and update the existing lambda function. Note, this deploys a new version of the lamba function code.

##### `invoke delete`

Deletes the Cloudformation stack.

### Indexing details

To be filled in.
