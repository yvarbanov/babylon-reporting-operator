#!/usr/bin/env python3

import json
import kopf
import kubernetes
import os
import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
from base64 import b64decode
import utils
from ipa_ldap import GPTEIpaLdap
from users import Users
from catalog_items import CatalogItems
from provisions import Provisions

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

resource_uuid = None
current_state = None
desired_state = None
username = None


def handle_no_action(logger, anarchy_subject):
    logger.info(f"Ignore action for the state '{current_state}'")


def handle_action_provision_pending(logger, anarchy_subject):
    logger.info(f"Handle action provision pending for {resource_uuid}.")

    utils.provision_lifecycle(resource_uuid, current_state, username)


def handle_action_provisioning(logger, anarchy_subject):
    logger.info(f"Handle action provisioning for {resource_uuid}.")

    provision = prepare(anarchy_subject, logger)

    if provision:
        user_name = provision.get('username')
        provision['user'] = search_ipa_user(user_name, logger)
        provision['user_db'] = populate_user(provision, logger)
        provision['catalog_id'] = populate_catalog(provision, logger)

        prov = Provisions(logger, provision)
        prov.populate_provisions()

    utils.provision_lifecycle(resource_uuid, current_state, username)


def handle_action_provision_failed(logger, anarchy_subject):
    logger.info(f"Handle action provision failed for {resource_uuid}.")

    last_action = utils.last_lifecycle(resource_uuid)

    # Update provision_results if the last action was provision
    if last_action.startswith('provision'):
        logger.info("Last action was provision, needs to update provision_results")
        query = f"UPDATE provisions SET provision_results = 'failure' WHERE uuid = '{resource_uuid}' RETURNING uuid;"

        utils.execute_query(query, autocommit=True)

    utils.provision_lifecycle(resource_uuid, current_state, username)


def handle_action_provision_complete(logger, anarchy_subject):
    logger.info(f"Handle action provision complete for {resource_uuid}.")

    utils.provision_lifecycle(resource_uuid, current_state, username)


def handle_action_started(logger, anarchy_subject):
    logger.info(f"Handle action started for {resource_uuid}.")

    provision_exists = utils.check_provision_exists(resource_uuid)

    if provision_exists == -1:
        handle_action_provisioning(logger, anarchy_subject)

    utils.provision_lifecycle(resource_uuid, current_state, username)


def handle_action_start_pending(logger, anarchy_subject):
    logger.info(f"Handle action start pending for {resource_uuid}.")

    provision_exists = utils.check_provision_exists(resource_uuid)

    if provision_exists == -1:
        handle_action_provisioning(logger, anarchy_subject)

    utils.provision_lifecycle(resource_uuid, current_state, username)


def handle_action_starting(logger, anarchy_subject):
    logger.info(f"Handle action starting for {resource_uuid}.")

    provision_exists = utils.check_provision_exists(resource_uuid)
    if provision_exists == -1:
        handle_action_provisioning(logger, anarchy_subject)

    utils.provision_lifecycle(resource_uuid, current_state, username)


def handle_action_start_failed(logger, anarchy_subject):
    logger.info(f"Handle action start failed for {resource_uuid}.")

    provision_exists = utils.check_provision_exists(resource_uuid)

    if provision_exists == -1:
        handle_action_provisioning(logger, anarchy_subject)

    last_action = utils.last_lifecycle(resource_uuid)

    # if last action was provision we have to update provision_results
    if last_action.startswith('provision'):
        logger.info("Last action was provision, needs to update provision_results")
        query = f"UPDATE provisions SET provision_results = 'failure' WHERE uuid = '{resource_uuid}' RETURNING uuid;"
        utils.execute_query(query, autocommit=True)

    utils.provision_lifecycle(resource_uuid, current_state, username)


def handle_action_stop_pending(logger, anarchy_subject):
    logger.info(f"Handle action stop pending for {resource_uuid}.")

    provision_exists = utils.check_provision_exists(resource_uuid)

    if provision_exists == -1:
        handle_action_provisioning(logger, anarchy_subject)

    utils.provision_lifecycle(resource_uuid, current_state, username)


def handle_action_stopping(logger, anarchy_subject):
    logger.info(f"Handle action stopping for {resource_uuid}.")

    provision_exists = utils.check_provision_exists(resource_uuid)

    if provision_exists == -1:
        handle_action_provisioning(logger, anarchy_subject)

    utils.provision_lifecycle(resource_uuid, current_state, username)


def handle_action_stop_failed(logger, anarchy_subject):
    logger.info(f"Handle action stop failed for {resource_uuid}.")

    provision_exists = utils.check_provision_exists(resource_uuid)

    if provision_exists == -1:
        handle_action_provisioning(logger, anarchy_subject)

    utils.provision_lifecycle(resource_uuid, current_state, username)


def handle_action_stopped(logger, anarchy_subject):
    logger.info(f"Handle action stopped for {resource_uuid}.")

    provision_exists = utils.check_provision_exists(resource_uuid)

    if provision_exists == -1:
        handle_action_provisioning(logger, anarchy_subject)

    utils.provision_lifecycle(resource_uuid, current_state, username)


def handle_action_destroying(logger, anarchy_subject):
    logger.info(f"Handle action destroying for {resource_uuid}.")

    provision_exists = utils.check_provision_exists(resource_uuid)

    if provision_exists == -1:
        handle_action_provisioning(logger, anarchy_subject)

    utils.provision_lifecycle(resource_uuid, current_state, username)


def handle_action_destroy_failed(logger, anarchy_subject):
    logger.info(f"Handle action destroy failed for {resource_uuid}.")

    provision_exists = utils.check_provision_exists(resource_uuid)

    if provision_exists == -1:
        handle_action_provisioning(logger, anarchy_subject)

    utils.provision_lifecycle(resource_uuid, current_state, username)


resource_states = {
    'None': handle_no_action,
    'new': handle_no_action,
    'provision-pending': handle_action_provision_pending,
    'provisioning': handle_action_provisioning,
    'provision-failed': handle_action_provision_failed,
    'started': handle_action_started,
    'start-pending': handle_action_start_pending,
    'starting': handle_action_starting,
    'start-failed': handle_action_start_failed,
    'stop-pending': handle_action_stop_pending,
    'stopping': handle_action_stopping,
    'stop-failed': handle_action_stop_failed,
    'stopped': handle_action_stopped,
    'destroying': handle_action_destroying,
    'destroy-failed': handle_action_destroy_failed
}


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
    global resource_uuid, current_state, username
    anarchy_subject = event.get('object')

    # Only respond to events that include AnarchySubject data.
    if not anarchy_subject \
    or anarchy_subject.get('kind') != 'AnarchySubject':
        logger.warning(event)
        return

    anarchy_subject_spec = anarchy_subject['spec']
    anarchy_subject_spec_vars = anarchy_subject_spec['vars']
    anarchy_subject_job_vars = anarchy_subject_spec_vars.get('job_vars', {})
    anarchy_subject_metadata = anarchy_subject['metadata']
    anarchy_subject_annotations = anarchy_subject_metadata['annotations']

    current_state = anarchy_subject_spec_vars.get('current_state')
    resource_uuid = anarchy_subject_job_vars.get('uuid',
                                                 anarchy_subject_annotations.get(
                                                     f"{poolboy_domain}/resource-handle-uid")
                                                 )
    # TODO: Try to get username from resource_claim
    username = anarchy_subject_annotations.get(
        f"{poolboy_domain}/resource-requester-preferred-username")

    logger.info(f"Current State: {current_state} for provision uuid {resource_uuid}")
    if current_state in resource_states:
        resource_states[current_state](logger, anarchy_subject)
    else:
        logger.warning(f"Current state '{current_state}' not found")
        return

    if not current_state or current_state in ('new', 'provision-pending'):
        logger.warning(f"Current State '{anarchy_subject_spec_vars.get('current_state')}'. We have to ignore it!")
        return

    # TODO: Working with JK to create a way to store the destroy action and read it from
    if event['type'] == 'DELETED' and current_state == 'destroying':
        logger.info(f"Set retirement date for provision {resource_uuid}")
        query = f"UPDATE provisions SET retired_at = timezone('utc', NOW()) \n" \
                f"WHERE uuid = '{resource_uuid}' RETURNING uuid;"
        utils.execute_query(query, autocommit=True)
        utils.provision_lifecycle(resource_uuid, 'destroy-completed', username)
        return


def populate_catalog(provision, logger):
    catalog = CatalogItems(logger, provision)
    results = catalog.populate_catalog_items()
    return results


def populate_user(provision, logger):
    users = Users(logger, provision)
    results = users.populate_users()
    return results


def search_ipa_user(user_name, logger):
    ipa_user = GPTEIpaLdap(logger)
    results = ipa_user.search_ipa_user(user_name)
    return results


def prepare(anarchy_subject, logger):
    anarchy_subject_spec = anarchy_subject['spec']
    anarchy_subject_spec_vars = anarchy_subject_spec['vars']
    anarchy_subject_metadata = anarchy_subject['metadata']
    anarchy_subject_annotations = anarchy_subject_metadata['annotations']
    anarchy_subject_labels = anarchy_subject_metadata['labels']
    provision_data = anarchy_subject_spec_vars.get('provision_data', {})
    anarchy_subject_job_vars = anarchy_subject_spec_vars.get('job_vars', {})
    anarchy_subject_status = anarchy_subject.get('status', {})
    tower_jobs = anarchy_subject_status.get('towerJobs', {})
    provision_job = tower_jobs.get('provision', {})
    provision_job_id = provision_job.get('deployerJob')

    # This is the resource claim namespace
    as_resource_claim_name = anarchy_subject_annotations.get(f"{poolboy_domain}/resource-claim-name")
    resource_claim_namespace = anarchy_subject_annotations.get(f"{poolboy_domain}/resource-claim-namespace")

    # This is the resource UUID
    resource_claim_uuid = anarchy_subject_job_vars.get('uuid',
                                                       anarchy_subject_annotations.get(
                                                           f"{poolboy_domain}/resource-handle-uid")
                                                       )
    logger.info(f"Resource claim UUID: {resource_claim_uuid}")

    # This is the resource requester
    resource_claim_requester = anarchy_subject_annotations.get(
        f"{poolboy_domain}/resource-requester-preferred-username")

    resource_label_governor = anarchy_subject_spec.get('governor')

    # TODO: If deployed using CloudForms, we have to return... I need help from Johnathan to remember where is
    #  the annotation

    resource_current_state = anarchy_subject_spec_vars.get('current_state')
    resource_desired_state = anarchy_subject_spec_vars.get('desired_state')
    logger.info(f"Resource Current State: {resource_current_state} - Resource Desired State: {resource_desired_state}")

    # I've to wait until I have all data from Tower and all variables used
    if resource_current_state and 'provision-pending' in resource_current_state:
        logger.info(f"Waiting for provisioning state for {resource_claim_uuid}")
        return

    catalog_display_name = resource_label_governor
    catalog_item_display_name = resource_label_governor
    if resource_current_state in ('provision', 'provision-completed'):
        resource_claim = custom_objects_api.get_namespaced_custom_object(
            poolboy_domain, poolboy_api_version,
            resource_claim_namespace, 'resourceclaims', as_resource_claim_name
        )
        resource_claim_metadata = resource_claim['metadata']
        resource_claim_annotations = resource_claim_metadata['annotations']
        resource_claim_labels = resource_claim_metadata['labels']


        # if babylon/catalogDisplayName get it from labels/{babylon_domain}/catalogItemName
        catalog_display_name = resource_claim_annotations.get(f"{babylon_domain}/catalogDisplayName",
                                                  resource_claim_labels.get(f"{babylon_domain}/catalogItemName", None))
        catalog_item_display_name = resource_claim_annotations.get(f"{babylon_domain}/catalogItemDisplayName",
                                                       resource_claim_labels.get(f"{babylon_domain}/catalogItemName", None))
        logger.info(
            f"catalog_display_name: {catalog_display_name} - catalog_item_display_name: {catalog_item_display_name}")

    provision_job_start_timestamp = utils.timestamp_to_utc(provision_job.get('startTimestamp'))
    provision_job_complete_timestamp = utils.timestamp_to_utc(provision_job.get('completeTimestamp'))
    logger.info(f"provision_job_start_timestamp: {provision_job_start_timestamp} - "
                f"provision_job_complete_timestamp: {provision_job_complete_timestamp}")
    provision_time = None

    provision_job_vars = {}
    if provision_job_id:
        resp = requests.get(
            f"https://{ansible_tower_hostname}/api/v2/jobs/{provision_job_id}",
            auth=(ansible_tower_user, ansible_tower_password),
            # We really need to fix the tower certs!
            verify=False,
        )
        provision_tower_job = resp.json()
        provision_job_vars = json.loads(provision_tower_job.get('extra_vars', '{}'))

    class_list = resource_label_governor.split('.')
    class_name = f"{class_list[2]}_{class_list[1].replace('-', '_')}".upper()

    sandbox_account = anarchy_subject_job_vars.get('sandbox_account', provision_data.get('ibm_sandbox_account'))
    sandbox_name = anarchy_subject_job_vars.get('sandbox_name', provision_data.get('ibm_sandbox_name'))

    workshop_users = provision_job_vars.get('num_users', provision_job_vars.get('user_count', 1))
    # This is used by user experiences
    if workshop_users == 0:
        workshop_users = 1

    # Define a dictionary with all information from provisions
    provision = {
        'provisioned_at': provision_job_start_timestamp,
        'job_start_timestamp': provision_job_start_timestamp,
        'job_complete_timestamp': provision_job_complete_timestamp,
        'provision_time': provision_time,
        'uuid': resource_claim_uuid,
        'username': resource_claim_requester,
        'catalog_id': resource_label_governor,
        'catalog_name': catalog_display_name,
        'catalog_item': catalog_item_display_name,
        'current_state': resource_current_state,
        'desired_state': resource_desired_state,
        'babylon_guid': provision_job_vars.get('guid', anarchy_subject_job_vars.get('guid')),
        'cloud_region': provision_job_vars.get('region', anarchy_subject_job_vars.get('region')),
        'cloud': provision_job_vars.get('cloud_provider', 'test'),
        'env_type': provision_job_vars.get('env_type', 'tests'),
        'platform': provision_job_vars.get('platform', 'tests'),
        'environment': class_list[2],
        'account': class_list[0],
        'class_name': class_name,
        'sandbox': sandbox_account,
        'sandbox_name': sandbox_name,
        'provision_vars': provision_job_vars,
        'manager_chargeback': 'default',
        'check_headcount': False,
        'opportunity': 'default',
        'workshop_users': workshop_users
    }

    return provision

