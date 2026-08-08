import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

DEFAULT_HTTP_TIMEOUT = (5.0, 30.0)
RETRY_STATUS_CODES = (429, 500, 502, 503, 504)


def create_retrying_session() -> requests.Session:
    retry = Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=RETRY_STATUS_CODES,
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session
