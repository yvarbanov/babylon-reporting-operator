#!/usr/bin/env python3

import json
import kopf
import kubernetes
import os
import requests

from base64 import b64decode

anarchy_domain = os.environ.get('ANARCHY_DOMAIN', 'anarchy.gpte.redhat.com')
anarchy_api_version = os.environ.get('ANARCHY_API_VERSION', 'v1')
babylon_domain = os.environ.get('BABYLON_DOMAIN', 'babylon.gpte.redhat.com')
babylon_api_version = os.environ.get('BABYLON_API_VERSION', 'v1')
poolboy_domain = os.environ.get('POOLBOY_DOMAIN', 'poolboy.gpte.redhat.com')
poolboy_api_version = os.environ.get('POOLBOY_API_VERSION', 'v1')

if os.path.exists('/run/secrets/kubernetes.io/serviceaccount'):
    kubernetes.config.load_incluster_config()
else:
    kubernetes.config.load_kube_config()

core_v1_api = kubernetes.client.CoreV1Api()
custom_objects_api = kubernetes.client.CustomObjectsApi()
namespaces = {}

@kopf.on.startup()
def configure(settings: kopf.OperatorSettings, **_):
    global ansible_tower_hostname, ansible_tower_password, ansible_tower_user

    # Disable scanning for CustomResourceDefinitions
    settings.scanning.disabled = True

    # Get the tower secret. This may change in the future if there are
    # multiple ansible tower deployments
    ansible_tower_secret = core_v1_api.read_namespaced_secret('babylon-tower', 'anarchy-operator')
    ansible_tower_hostname = b64decode(ansible_tower_secret.data['hostname']).decode('utf8')
    ansible_tower_password = b64decode(ansible_tower_secret.data['password']).decode('utf8')
    ansible_tower_user = b64decode(ansible_tower_secret.data['user']).decode('utf8')

@kopf.on.event(
    'namespaces',
)
def namespace_event(event, logger, **_):
    namespace = event.get('object')

    # Only respond to events that include Namespace data.
    if not namespace \
    or namespace.get('kind') != 'Namespace':
        logger.warning(event)
        return

    name = namespace['metadata']['name']
    namespaces[name] = namespace

@kopf.on.event(
    anarchy_domain, anarchy_api_version, 'anarchysubjects',
)
def anarchysubject_event(event, logger, **_):
    anarchy_subject = event.get('object')

    # Only respond to events that include AnarchySubject data.
    if not anarchy_subject \
    or anarchy_subject.get('kind') != 'AnarchySubject':
        logger.warning(event)
        return

    anarchy_subject_metadata = anarchy_subject['metadata']
    anarchy_subject_spec = anarchy_subject['spec']
    anarchy_subject_status = anarchy_subject.get('status', {})
    anarchy_subject_name = anarchy_subject_metadata['name']
    anarchy_subject_namespace = anarchy_subject_metadata['namespace']
    anarchy_subject_deletion_timestamp = anarchy_subject_metadata.get('deletionTimestamp')
    anarchy_subject_annotations = anarchy_subject_metadata.get('annotations')
    anarchy_subject_vars = anarchy_subject_spec.get('vars', {})
    anarchy_subject_job_vars = anarchy_subject_vars.get('job_vars', {})
    provision_data = anarchy_subject_vars.get('provision_data', {})

    resource_claim_name = anarchy_subject_annotations.get(f"{poolboy_domain}/resource-claim-name")
    resource_claim_namespace = anarchy_subject_annotations.get(f"{poolboy_domain}/resource-claim-namespace")
    resource_handle_name = anarchy_subject_annotations.get(f"{poolboy_domain}/resource-handle-name")
    resource_handle_namespace = anarchy_subject_annotations.get(f"{poolboy_domain}/resource-handle-namespace")
    resource_index = anarchy_subject_annotations.get(f"{poolboy_domain}/resource-index")
    resource_provider_name = anarchy_subject_annotations.get(f"{poolboy_domain}/resource-provider-name")
    resource_provider_namespace = anarchy_subject_annotations.get(f"{poolboy_domain}/resource-provider-namespace")

    resource_claim_namespace_definition = None
    resource_claim_namespace_metadata = None
    resource_claim_namespace_annotations = None
    resource_claim_namespace_user = None
    if resource_claim_namespace:
        resource_claim_namespace_definition = namespaces.get(resource_claim_namespace)
        if resource_claim_namespace_definition:
            resource_claim_namespace_metadata = resource_claim_namespace_definition['metadata']
            resource_claim_namespace_annotations = resource_claim_namespace_metadata.get('annotations', {})
            resource_claim_namespace_user = resource_claim_namespace_annotations.get('openshift.io/requester')
        elif not anarchy_subject_deletion_timestamp:
            # By chance the ResourceClaim was detected before its namespace was discovered.
            # Raise temporary error to trigger retry.
            raise kopf.TemporaryError(f"Namespace pending discovery")

    current_state = anarchy_subject_vars.get('current_state')
    desired_state = anarchy_subject_vars.get('desired_state')

    aws_sandbox_account = anarchy_subject_job_vars.get('sandbox_account')
    aws_sandbox_name = anarchy_subject_job_vars.get('sandbox_name')

    ibm_sandbox_account = provision_data.get('ibm_sandbox_account')
    ibm_sandbox_name = provision_data.get('ibm_sandbox_name')

    tower_jobs = anarchy_subject_status.get('towerJobs', {})
    destroy_job = tower_jobs.get('destroy', {})
    destroy_job_id = destroy_job.get('deployerJob')
    destroy_job_start_timestamp = destroy_job.get('startTimestamp')
    destroy_job_complete_timestamp = destroy_job.get('completeTimestamp')
    provision_job = tower_jobs.get('provision', {})
    provision_job_id = provision_job.get('deployerJob')
    provision_job_start_timestamp = provision_job.get('startTimestamp')
    provision_job_complete_timestamp = provision_job.get('completeTimestamp')
    logger.info(tower_jobs)

    # This is overkill, probably just need to check if the database already has the vars or not...
    if provision_job_id:
        resp = requests.get(
            f"https://{ansible_tower_hostname}/api/v2/jobs/{provision_job_id}",
            auth=(ansible_tower_user, ansible_tower_password),
            # We really need to fix the tower certs!
            verify=False,
        )
        provision_tower_job = resp.json()
        provision_job_vars = json.loads(provision_tower_job.get('extra_vars', '{}'))
        aws_region = provision_job_vars.get('aws_region')
        logger.info(aws_region)
