import jwt
import os
import requests
import unicodedata
from datetime import datetime, timedelta, UTC
from functools import lru_cache
from iqpilot.system.hardware.hw import Paths
from iqpilot.system.version import get_version

# name: jwt signature algorithm
KEYS = {"id_rsa": "RS256",
        "id_ecdsa": "ES256"}


@lru_cache(maxsize=4)
def load_signing_key(private_key: str):
  # PyJWT re-parses a PEM string on every encode; an RSA parse is ~40ms, so cache the key object
  try:
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    return load_pem_private_key(private_key.encode(), password=None)
  except Exception:
    return private_key


class BaseApi:
  def __init__(self, dongle_id, api_host, user_agent="openpilot-", *, transport=None):
    self.dongle_id = dongle_id
    self.api_host = api_host
    self.user_agent = user_agent
    self._transport = requests if transport is None else transport
    self.jwt_algorithm, self.private_key, _ = self.get_key_pair()

  def get(self, *args, **kwargs):
    return self.request('GET', *args, **kwargs)

  def post(self, *args, **kwargs):
    return self.request('POST', *args, **kwargs)

  def request_options(self, method, endpoint, *, timeout=None, access_token=None, json=None, **params):
    identity = unicodedata.normalize('NFD', f'{self.user_agent}{get_version()}')
    headers = {'User-Agent': ''.join(character for character in identity if 32 <= ord(character) <= 126)}
    if access_token is not None:
      headers['Authorization'] = f'JWT {access_token}'
    return {'method': method, 'url': f"{self.api_host.rstrip('/')}/{endpoint.lstrip('/')}",
            'timeout': timeout, 'headers': headers, 'json': json, 'params': params}

  def request(self, method, endpoint, timeout=None, access_token=None, *, session=None, json=None, **params):
    options = self.request_options(method, endpoint, timeout=timeout, access_token=access_token, json=json, **params)
    transport = self._transport if session is None else session
    return transport.request(**options)

  def _get_token(self, payload_extra=None, expiry_hours=1, **extra_payload):
    now = datetime.now(UTC).replace(tzinfo=None)
    payload = {
      'identity': self.dongle_id,
      'nbf': now,
      'iat': now,
      'exp': now + timedelta(hours=expiry_hours),
      **extra_payload
    }
    if payload_extra is not None:
      payload.update(payload_extra)
    key = load_signing_key(self.private_key) if self.private_key else self.private_key
    token = jwt.encode(payload, key, algorithm=self.jwt_algorithm)
    if isinstance(token, bytes):
      token = token.decode('utf8')
    return token

  def get_token(self, payload_extra=None, expiry_hours=1):
    return self._get_token(payload_extra, expiry_hours)

  def api_get(self, endpoint, method='GET', timeout=None, access_token=None, session=None, json=None, **params):
    return self.request(method, endpoint, timeout, access_token, session=session, json=json, **params)

  @staticmethod
  def get_key_pair() -> tuple[str, str, str] | tuple[None, None, None]:
    for key in KEYS:
      if os.path.isfile(Paths.persist_root() + f'/comma/{key}') and os.path.isfile(Paths.persist_root() + f'/comma/{key}.pub'):
        with open(Paths.persist_root() + f'/comma/{key}') as private, open(Paths.persist_root() + f'/comma/{key}.pub') as public:
          return KEYS[key], private.read(), public.read()
    return None, None, None
