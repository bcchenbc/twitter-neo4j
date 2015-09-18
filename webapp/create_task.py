import boto3
import time
import pprint
import socket
import traceback
from retrying import retry
import logging
from logging.handlers import SysLogHandler
import time

TASK_REVISION = '5'
RUN_TASK_RETRIES = 3
RUN_TASK_WAIT_SECS = 2
TASK_INFO_RETRIES = 7
TASK_INFO_WAIT_SECS = 1
DESCRIBE_INSTANCE_WAIT_SECS = 1
DESCRIBE_INSTANCE_RETRIES = 3
CONNECT_RETRIES = 15
CONNECT_WAIT_SECS = 1

class ContextFilter(logging.Filter):
  hostname = socket.gethostname()

  def filter(self, record):
    record.hostname = ContextFilter.hostname
    return True

logger = logging.getLogger()
logger.setLevel(logging.INFO)

f = ContextFilter()
logger.addFilter(f)

syslog = SysLogHandler(address=('logs3.papertrailapp.com', 16315))
formatter = logging.Formatter('%(asctime)s twitter.dockerexec: %(message).60s', datefmt='%b %d %H:%M:%S')

syslog.setFormatter(formatter)
logger.addHandler(syslog)


@retry(stop_max_attempt_number=RUN_TASK_RETRIES, wait_fixed=(RUN_TASK_WAIT_SECS * 1000))
def run_task(ecs, twitter_user, consumer_key, consumer_secret, user_key, user_secret):
    response =  ecs.run_task(
      cluster='default',
      taskDefinition='neo4j-twitter:%s' % TASK_REVISION,
      overrides={
        'containerOverrides': [
            {
                'name': 'neo4j-twitter',
                'environment': [
                    {
                        'name': 'TWITTER_USER',
                        'value': twitter_user
                    },
                    {
                        'name': 'TWITTER_CONSUMER_KEY',
                        'value': consumer_key
                    },
                    {
                        'name': 'TWITTER_CONSUMER_SECRET',
                        'value': consumer_secret
                    },
                    {
                        'name': 'TWITTER_USER_KEY',
                        'value': user_key
                    },
                    {
                        'name': 'TWITTER_USER_SECRET',
                        'value': user_secret
                    },
                    {
                        'name': 'TIME_STARTED',
                        'value': str(time.time())
                    },
                ]
            },
        ]
      },
      count=1,
    )

    try:
      task_arn = response['tasks'][0]['taskArn']
    except IndexError:
      raise Exception('Did not find task in response: %s' % response)

    return task_arn


@retry(stop_max_attempt_number=TASK_INFO_RETRIES, wait_fixed=(TASK_INFO_WAIT_SECS * 1000))
def get_task_info(ecs, task_arn):
    task_info = {}

    desc = ecs.describe_tasks(tasks=[task_arn])

    try:
      networkBindings = desc['tasks'][0]['containers'][0]['networkBindings']
      containerInstanceArn = desc['tasks'][0]['containerInstanceArn']
    except:
      raise Exception('did not find network and container info for task: %s' % (desc))

    try:
      containerDesc = ecs.describe_container_instances(containerInstances=[containerInstanceArn])
      ec2InstanceId = containerDesc['containerInstances'][0]['ec2InstanceId']
    except:
      raise Exception('did not find ec2 instance ID from container: %s' % (desc))

    task_info['instanceId'] = ec2InstanceId

    for index, binding in enumerate(networkBindings):
      if binding['containerPort'] == 7474:
        task_info['port'] = binding['hostPort']

    if 'instanceId' in task_info.keys() and 'port' in task_info.keys():
      return task_info
    else:
      raise Exception('did not find mapped port for task %s' % task_arn)

@retry(stop_max_attempt_number=DESCRIBE_INSTANCE_RETRIES, wait_fixed=(DESCRIBE_INSTANCE_WAIT_SECS * 1000))
def get_connection_ip(ec2, instance_id):
    ec2_instance = ec2.describe_instances(InstanceIds=[instance_id])
    ip_address = ec2_instance['Reservations'][0]['Instances'][0]['PublicIpAddress']
    return ip_address

@retry(stop_max_attempt_number=CONNECT_RETRIES, wait_fixed=(CONNECT_WAIT_SECS * 1000))
def try_connecting_neo4j(ip_address, port):
    try:
      s = socket.socket()
      s.connect((ip_address, port))
    except:
      raise Exception('could not connect to Neo4j browser on %s:%s' % (ip_address, port))

    return True

def create_task(screen_name, consumer_key, consumer_secret, user_key, user_secret):
    ecs = boto3.client('ecs')
    ec2 = boto3.client('ec2')

    try:
      task_arn = run_task(ecs, screen_name, consumer_key, consumer_secret, user_key, user_secret)
      task_info = get_task_info(ecs, task_arn)
      ip_address = get_connection_ip(ec2, task_info['instanceId'])
      try_connecting_neo4j(ip_address, task_info['port'])
      logger.info('Created instance for tw:%s at %s:%s' % (screen_name,ip_address,task_info['port']))
    except Exception as e:
      logger.exception(e)
      logger.error('Error creating docker image for: tu:%s' % screen_name)
      print(traceback.format_exc())
      print(e)
      raise e

    return 'http://%s:%s' % (ip_address, task_info['port'])
     
