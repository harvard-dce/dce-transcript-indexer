# dce-transcript-indexer
AWS Lambda function for indexing IBM Watson auto-generated transcripts in Elasticsearch

This project consists of a Python 3.6 AWS Lambda function and some associated tooling. The function itself
is defined by `function.lambda_handler`. It expects to be triggered by an object PUT event in a S3 bucket, but can 
also be executed (triggered) locally via command-line args.

To get started, copy `example.env` to `.env` and update the settings described below.

### Runtime Settings

The function's runtime settings are all controlled via environment variables. These should be set
in the AWS Lambda function definition.

* **ES_HOST** - the url (including http/https) of the Elasticsearch endpoint, e.g. `http://localhost:9200`
* **ES_INDEX_PREFIX** - the prefix part of the index name. The default is *"transcripts"* which will result in daily
  indexes like *transcripts.2017-04-24*.
* **ES_HTTP_AUTH** - (optional) If the Elasticsearch endpoint is protected by http auth, this should specify the *user:pass*
  combo to use.
  
### Upload Settings

These additional settings control how the AWS Lambda function is uploaded and configured.

* **AWS_DEFAULT_PROFILE**
* **TRANSCRIPT_INDEXER_LAMBDA_ROLE** - the full arn value of the AWS IAM role that the function 
  will be executed with
* **ES_INDEX_PREFIX** - this runtime setting is also used by the `fab put_template` command to determine
  the name and index matching pattern of the Elasticsearch index template.
  
### Fabric commands

These operations are executed via the `fab` command. Run `fab -l` to list in the terminal.

##### `fab put_template`

This will create or update an Elasticsearch index template to be used on the resulting transcript indexes.

##### `fab package_dev|package_release`

This will package the function and any dependent libraries into a zipfile that can then be uploaded to AWS Lambda.

##### `fab upload_dev|upload_release`

This will package the function and dependent libraries and then upload the code and function configuration via the Lambda API, including the runtime environment variable settings mentioned above. `dev` vs `release` simply controls how the uploaded version will be tagged; "dev/testing" vs. "stable release".

### Indexing details

Each transcript result from IBM Watson contains a set of transcribed chunks ("captions") and these are each indexed individually. So a standard lecture transcript will result in anywhere from 10 - 1000+ caption documents. 

Each doc looks like:

```json
    {
      "transcript_id": "7219fbc0-1b18-11e7-a459-59d387992251",
      "generated": "2017-04-24T14:27:44.114438",
      "mpid": "24c8213b-bf97-46cd-b75e-c36d7d33dcba",
      "text": "you might be wondering what does this mean you like the students the staff the teachers ",
      "confidence": 0.783,
      "inpoint": 14.83,
      "outpoint": 19.27,
      "length": 4.44,
      "hesitations": 0,
      "hesitation_length": 0,
      "word_count": 16
    }
```

Document indexes use a rolling, daily name strategy, e.g. *transcripts.2017-04-24*.  It is a assumed that all captions for a particular mediapackage are contain in a single transcript result file. In other words, the corpus of captions for a mediapackage is not cumulative.

