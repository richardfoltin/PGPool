import json
import logging
from Queue import Queue
from threading import Thread

from flask import Flask, request, jsonify
from werkzeug.exceptions import abort

from pgpool.config import cfg_get
from pgpool.models import init_database, db_updater, Account, db_cleanup

# ---------------------------------------------------------------------------
from pgpool.utils import parse_bool

logging.basicConfig(level=logging.INFO,
    format='%(asctime)s [%(threadName)16s][%(module)14s][%(levelname)8s] %(message)s')

# Silence some loggers
logging.getLogger('werkzeug').setLevel(logging.WARNING)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------

app = Flask(__name__)



@app.route('/', methods=['GET'])
def index():
    return "PGPool running!"


@app.route('/account/request', methods=['GET'])
def get_accounts():
    system_id = request.args.get('system_id')
    if not system_id:
        log.error("Request from {} is missing system_id".format(request.remote_addr))
        abort(400)

    count = int(request.args.get('count', 1))
    min_level = int(request.args.get('min_level', 1))
    max_level = int(request.args.get('max_level', 40))
    lat = request.args.get('latitude')
    lat = float(lat) if lat else lat
    lng = request.args.get('longitude')
    lng = float(lng) if lng else lng
    include_already_assigned = parse_bool(request.args.get('include_already_assigned'))
    log.info(
        "System ID [{}] requested {} accounts level {}-{} from {}".format(system_id, count, min_level, max_level,
                                                                          request.remote_addr))
    accounts = Account.get_accounts(system_id, count, min_level, max_level, lat, lng, include_already_assigned)
    if len(accounts) < count:
        log.warning("Could only deliver {} accounts.".format(len(accounts)))
    return jsonify(accounts[0] if accounts and count == 1 else accounts)


@app.route('/account/release', methods=['POST'])
def release_accounts():
    data = json.loads(request.data)
    if isinstance(data, list):
        for update in data:
            update['system_id'] = None
            db_updates_queue.put(update)
    else:
        data['system_id'] = None
        db_updates_queue.put(data)
    return 'ok'



@app.route('/account/update', methods=['POST'])
def accounts_update():
    data = json.loads(request.data)
    if isinstance(data, list):
        for update in data:
            db_updates_queue.put(update)
    else:
        db_updates_queue.put(data)
    return 'ok'


# @app.route('/account/csvimport', methods=['POST'])
# def import_csv_accounts():
#     log.debug("new_accounts request received from {}.".format(request.remote_addr))
#     num_accounts = 0
#     for file_id in request.files:
#         file = request.files[file_id]
#         content = file.read()
#         file.close()
#         for line in content.splitlines():
#             fields = line.split(",")
#             fields = map(str.strip, fields)
#             auth_service = fields[0]
#             username = fields[1]
#             password = fields[2]
#             acc, created = Account.get_or_create(username=username,
#                                                  defaults={
#                                                      'auth_service': auth_service,
#                                                      'password': password
#                                                  })
#             if created:
#                 log.info("Imported new {} account {}".format(auth_service, username))
#                 num_accounts += 1
#     return "{} new accounts imported.".format(num_accounts)


def run_server():
    app.run(threaded=True, port=cfg_get('port'))

# ---------------------------------------------------------------------------

db = init_database(app)

# DB Updates
db_updates_queue = Queue()

t = Thread(target=db_updater, name='db-updater',
           args=(db_updates_queue, db))
t.daemon = True
t.start()

t = Thread(target=db_cleanup, name='db-cleanup')
t.daemon = True
t.start()

log.info("PGPool starting up...")
run_server()
