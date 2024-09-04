from typing import Tuple, Union
from urllib.parse import ParseResult, urlencode, urlunparse
import json
import redis
import sys
import os
import logging
import botocore.session # type: ignore
from botocore.model import ServiceId # type: ignore
from botocore.signers import RequestSigner # type: ignore
from cachetools import TTLCache, cached
import uuid

log = logging.getLogger()
log.setLevel(logging.DEBUG)

r = redis.Redis(decode_responses=True)

class ElastiCacheIAMProvider(redis.CredentialProvider):
    def __init__(self, user, cache_name, is_serverless=False, region="us-east-1"):
        self.user = user
        self.cache_name = cache_name
        self.is_serverless = is_serverless
        self.region = region

        session = botocore.session.get_session()
        log.debug("botocore.session.get_session")
        self.request_signer = RequestSigner(
            ServiceId("elasticache"),
            self.region,
            "elasticache",
            "v4",
            session.get_credentials(),
            session.get_component("event_emitter"),
        )
        log.debug("self.request_signer")

    # Generated IAM tokens are valid for 15 minutes
    @cached(cache=TTLCache(maxsize=128, ttl=900))
    def get_credentials(self) -> Union[Tuple[str], Tuple[str, str]]:
        query_params = {"Action": "connect", "User": self.user}
        if self.is_serverless:
            query_params["ResourceType"] = "ServerlessCache"
        url = urlunparse(
            ParseResult(
                scheme="https",
                netloc=self.cache_name,
                path="/",
                query=urlencode(query_params),
                params="",
                fragment="",
            )
        )
        log.debug("urlunparse")
        
        signed_url = self.request_signer.generate_presigned_url(
            {"method": "GET", "url": url, "body": {}, "headers": {}, "context": {}},
            operation_name="connect",
            expires_in=900,
            region_name=self.region,
        )
        log.debug("signed_url")
        
        # RequestSigner only seems to work if the URL has a protocol, but
        # Elasticache only accepts the URL without a protocol
        # So strip it off the signed URL before returning
        return (self.user, signed_url.removeprefix("https://"))

def lambda_handler(event, context):
  log.debug('event', event)
  redis_conn = None
  redis_endpoint = None
  redis_port = None
  redis_auth = None

  if "REDIS_HOST" in os.environ and "REDIS_AUTH" in os.environ and "REDIS_PORT" in os.environ and "REDIS_CACHE_NAME" in os.environ:
    redis_endpoint = os.environ["REDIS_HOST"]
    redis_port = os.environ["REDIS_PORT"]
    redis_auth = os.environ["REDIS_AUTH"]
    redis_cache_name = os.environ["REDIS_CACHE_NAME"]
    log.debug("redis: " + redis_endpoint)
  else:
    log.debug(" REDIS_HOST REDIS_PORT REDIS_AUTH REDIS_CACHE_NAME configuration not set !")
    return {
      'statusCode': 500
    }

  try:
    username = redis_auth # replace with your user id
    cache_name = redis_cache_name # replace with your cache name
    elasticache_endpoint = redis_endpoint # replace with your cache endpoint
    creds_provider = ElastiCacheIAMProvider(user=username, cache_name=cache_name, is_serverless=True)
    redis_client = redis.Redis(host=elasticache_endpoint, port=6379, credential_provider=creds_provider, ssl=False, ssl_cert_reqs="none")
    
    countries = []
    for i in range(100):
        countries.append({"name": f"country{i}", "capital": f"capital{i}"})
    cache_countries = redis_client.get('countries')
    if not cache_countries:
        cache_countries = json.dumps(countries)
        redis_client.set('countries', cache_countries)
    return {
        'statusCode': 200,
        'body': json.loads(cache_countries)
    }
  except Exception as err:
    log.debug("Error: " + str(err))
    return {
      'statusCode': 500
    }
  finally:
    del redis_conn
