import json
import logging

import copy
import requests
import threading

from timeit import default_timer
from Queue import Empty
# from cachetools import LFUCache

from requests_futures.sessions import FuturesSession
from requests.packages.urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter

from pgpool.config import cfg_get

log = logging.getLogger(__name__)

wh_lock = threading.Lock()

wh_retries = 3
wh_timeout = 2
wh_backoff_factor = 0.25
wh_concurrency = 25
wh_lfu_size = 1000
wh_frame_interval = 500

filters = []


def send_to_webhooks(session, message_frame):

    req_timeout = wh_timeout

    for hooks in message_frame:
        try:
            # Disable keep-alive and set streaming to True, so we can skip
            # the response content.
            session.post(hooks.get('webhook'), json=hooks.get('message'),
                         timeout=(None, req_timeout),
                         background_callback=__wh_completed,
                         headers={'Connection': 'close'},
                         stream=True)
        except requests.exceptions.ReadTimeout:
            log.exception('Response timeout on webhook endpoint %s.', hooks.get('webhook'))
        except requests.exceptions.RequestException as e:
            log.exception(e)


def wh_updater(queue):
    wh_threshold_timer = default_timer()
    wh_over_threshold = False

    # Set up one session to use for all requests.
    # Requests to the same host will reuse the underlying TCP
    # connection, giving a performance increase.
    session = get_async_requests_session(
        wh_retries,
        wh_backoff_factor,
        wh_concurrency)


    # Prepare to send data per timed message frames instead of per object.
    frame_interval_sec = (wh_frame_interval / 1000)
    frame_first_message_time_sec = default_timer()
    frame_messages = []
    first_message = True

    # How low do we want the queue size to stay?
    wh_warning_threshold = 100
    # How long can it be over the threshold, in seconds?
    # Default: 5 seconds per 100 in threshold + frame_interval_sec.
    wh_threshold_lifetime = int(5 * (wh_warning_threshold / 100.0))
    wh_threshold_timer += frame_interval_sec

    # The forever loop.
    while True:
        try:
            # Loop the queue.
            try:
                timeout = frame_interval_sec if len(
                    frame_messages) > 0 else None
                whtype, message = queue.get(True, timeout)
            except Empty:
                pass
            else:
                for f in filters:           #Check filters that match
                    if f.check(message):
                        frame_message = {'webhook': f.get_webhook_url(), 'message': f.format_webhook(message)}
                        frame_messages.append(frame_message)
                queue.task_done()
            # Store the time when we added the first message instead of the
            # time when we last cleared the messages, so we more accurately
            # measure time spent getting messages from our queue.
            now = default_timer()
            num_messages = len(frame_messages)

            if num_messages == 1 and first_message:
                frame_first_message_time_sec = now
                first_message = False

            # If enough time has passed, send the message frame.
            time_passed_sec = now - frame_first_message_time_sec

            if num_messages > 0 and (time_passed_sec >
                                     frame_interval_sec):
                log.debug('Sending %d items webhooks.',
                          num_messages)
                send_to_webhooks(session, frame_messages)

                frame_messages = []
                first_message = True

            # Webhook queue moving too slow.
            if (not wh_over_threshold) and (
                    queue.qsize() > wh_warning_threshold):
                wh_over_threshold = True
                wh_threshold_timer = default_timer()
            elif wh_over_threshold:
                if queue.qsize() < wh_warning_threshold:
                    wh_over_threshold = False
                else:
                    timediff_sec = default_timer() - wh_threshold_timer

                    if timediff_sec > wh_threshold_lifetime:
                        log.warning('Webhook queue has been > %d (@%d);'
                                    + ' for over %d seconds,'
                                    + ' try increasing --wh-threads.',
                                    wh_warning_threshold,
                                    queue.qsize(),
                                    wh_threshold_lifetime)

        except Exception as e:
            log.exception('Exception in wh_updater: %s.', e)


def load_webhook_template(file):
    with open(file, 'r') as template:
        temp = json.loads(template.read(), 'utf-8')
        template.close()
        return temp


# Helpers
# Background handler for completed webhook requests.
def __wh_completed(sess, resp):
    # Instantly close the response to release the connection back to the pool.
    resp.close()


# Get a future_requests FuturesSession that supports asynchronous workers
# and retrying requests on failure.
# Setting up a persistent session that is re-used by multiple requests can
# speed up requests to the same host, as it'll re-use the underlying TCP
# connection.
def get_async_requests_session(num_retries, backoff_factor, pool_size,
                               status_forcelist=None):
    # Use requests & urllib3 to auto-retry.
    # If the backoff_factor is 0.1, then sleep() will sleep for [0.1s, 0.2s,
    # 0.4s, ...] between retries. It will also force a retry if the status
    # code returned is in status_forcelist.
    if status_forcelist is None:
        status_forcelist = [500, 502, 503, 504]
    session = FuturesSession(max_workers=pool_size)

    # If any regular response is generated, no retry is done. Without using
    # the status_forcelist, even a response with status 500 will not be
    # retried.
    retries = Retry(total=num_retries, backoff_factor=backoff_factor,
                    status_forcelist=status_forcelist)

    # Mount handler on both HTTP & HTTPS.
    session.mount('http://', HTTPAdapter(max_retries=retries,
                                         pool_connections=pool_size,
                                         pool_maxsize=pool_size))
    session.mount('https://', HTTPAdapter(max_retries=retries,
                                          pool_connections=pool_size,
                                          pool_maxsize=pool_size))

    return session


def load_filters(filter_file):

    with open(filter_file, 'r') as f:
        filt_file = json.loads(f.read(), 'utf-8')
        f.close()
        if type(filt_file) is not dict:
            log.error("Filters must be a proper JSON file!")
            return False
        for filt_key in filt_file:
            settings = filt_file[filt_key]
            if 'webhook' in settings and 'filter' in settings:
                filt = Filter(filt_key, settings)
                if filt.enabled:
                    check, msg = filt.validate()
                    if check:
                        filters.append(filt)
                        log.debug('Added filter %s', filt_key)
                    else:
                        log.error(msg)
                        return False
            else:
                log.error("Webhook filters must contain webhook and filter!")
    log.debug("Successfully loaded %d filters.", len(filt_file))
    return True


class Filter:

    def __init__(self, filt_name, filt_settings):
        self.name = filt_name
        if 'webhook' in filt_settings:
            self.webhook = filt_settings.pop('webhook')
        if 'filter' in filt_settings:
            self.filter = filt_settings.pop('filter')
        self.enabled = filt_settings.get('enabled', True)

    def check(self, data):
        if 'types' in self.filter:
            if data.get('type') not in self.filter['types']:
                return False
        if 'min_lvl' in self.filter:
            if data.get('level', 0) < self.filter['min_lvl']:
                return False
        if 'max_lvl' in self.filter:
            if data.get('level', 1000) > self.filter['max_lvl']:
                return False
        if 'system_id' in self.filter:
            if not data.get('system_id') or data.get('system_id') not in self.filter['system_id']:
                return False
        if 'low_lvl_threshold' in self.filter:
            if data.get('good_low_level', 2^31) > self.filter['low_lvl_threshold']:
                return False
        if 'high_lvl_threshold' in self.filter:
            if data.get('good_high_level', 2^31) > self.filter['high_lvl_threshold']:
                return False

        return True

    def validate(self):
        if not isinstance(self.webhook, dict):
            return False, "Webhook must be a dict. Please refer to the example."
        if not self.webhook.get('url') or not self.webhook.get('data'):
            return False, "Webhook must have a url and data set!"
        if not isinstance(self.filter, dict):
            return False, "Filter must be a dict. Please refer to the example."
        if 'types' in self.filter and not isinstance(self.filter['types'], list):
            return False, "Filter types must be a list!"
        if 'system_id' in self.filter and not isinstance(self.filter['system_id'], list):
            return False, "Filter system_id must be a list!"
        if 'min_lvl' in self.filter and not str(self.filter['min_lvl']).isdigit():
            return False, "Filter min_lvl must be an integer!"
        if 'max_lvl' in self.filter and not str(self.filter['max_lvl']).isdigit():
            return False, "Filter max_lvl must be an integer!"
        if 'low_lvl_threshold' in self.filter and not str(self.filter['low_lvl_threshold']).isdigit():
            return False, "Filter low_lvl_theshold must be an integer!"
        if 'high_lvl_threshold' in self.filter and not str(self.filter['high_lvl_threshold']).isdigit():
            return False, "Filter high_lvl_theshold must be an integer!"
        return True, ""

    def get_webhook_url(self):
        return self.webhook.get('url')

    def format_webhook(self, data_in):
        wh_data = copy.deepcopy(self.webhook.get('data', {}))
        for key in wh_data:
            for repl in data_in:
                wh_data[key] = wh_data[key].replace('<{}>'.format(repl), str(data_in[repl]))
        return wh_data

