import json
import os
import traceback
from typing import Optional

import boto3
import botocore
from math import ceil

resource_to_quota_code = {
    'notebook-instance/ml.t3.medium': 'L-E17566B7'
}

service_quota_client = boto3.client('service-quotas')
region = os.getenv('AWS_REGION')


def has_at_least_n_requests_in_template(n: Optional[int] = 10) -> bool:
    paginator = service_quota_client.get_paginator('list_service_quota_increase_requests_in_template')
    count = 0
    for response in paginator.paginate():
        count += len(response['ServiceQuotaIncreaseRequestInTemplateList'])
    return count >= n


def parse_resource_name_from_message(message):
    dimensions = message['Trigger']['Metrics'][0]['MetricStat']['Metric']['Dimensions']

    resource = [x for x in dimensions if x['name'] == 'Resource'][0]['value']

    return resource


def process_record(record):
    message = json.loads(record['Sns']['Message'])

    resource = parse_resource_name_from_message(message)

    if resource not in resource_to_quota_code:
        raise ValueError(f'Unknown quota code for resource {resource}')

    quota_code = resource_to_quota_code[resource]

    get_service_quota_resp = service_quota_client.get_service_quota(
        ServiceCode='sagemaker',
        QuotaCode=quota_code
    )

    quota_value = get_service_quota_resp['Quota']['Value']
    quota_value_desired = ceil(quota_value * 1.5)

    print(f'Requesting quota increase {quota_code} for resource {resource} from {quota_value} to {quota_value_desired}')

    try:
        if not has_at_least_n_requests_in_template(n=10):
            service_quota_client.put_service_quota_increase_request_into_template(
                QuotaCode=quota_code,
                ServiceCode='sagemaker',
                AwsRegion=region,
                DesiredValue=quota_value_desired
            )
            service_quota_client.associate_service_quota_template()
    except botocore.exceptions.ClientError as error:
        if error.response['Error']['Code'] == 'NoAvailableOrganizationException':
            print('Not using quota template because no organization was available')
            pass
        else:
            print('Error trying to add quota request to template.')
            traceback.print_exc()

    try:
        service_quota_client.request_service_quota_increase(
            ServiceCode='sagemaker',
            QuotaCode=quota_code,
            DesiredValue=quota_value_desired
        )
    except botocore.exceptions.ClientError as error:
        if error.response['Error']['Code'] == 'ResourceAlreadyExistsException':
            pass
        else:
            raise


def lambda_handler(event, context):
    print(event)

    error_count = 0
    for record in event['Records']:
        try:
            process_record(record)
        except Exception:
            error_count += 1
            traceback.print_exc()
    if error_count > 0:
        raise Exception('At least one record failed.')
