import json
import logging
from Queue import Queue
from threading import Thread

from flask import Flask, request, jsonify
from werkzeug.exceptions import abort

from pgpool.config import cfg_get
from pgpool.models import init_database, db_updater, Account, auto_release

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
    include_already_assigned = parse_bool(request.args.get('include_already_assigned'))
    banned_or_new = parse_bool(request.args.get('banned_or_new'))
    # lat = request.args.get('latitude')
    # lat = float(lat) if lat else lat
    # lng = request.args.get('longitude')
    # lng = float(lng) if lng else lng
    log.info(
        "System ID [{}] requested {} accounts level {}-{} from {}".format(system_id, count, min_level, max_level,
                                                                          request.remote_addr))
    accounts = Account.get_accounts(system_id, count, min_level, max_level, include_already_assigned, banned_or_new)
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


def run_server():
    app.run(threaded=True, port=cfg_get('port'))

# ---------------------------------------------------------------------------

log.info("PGPool starting up...")

db = init_database(app)

# DB Updates
db_updates_queue = Queue()

t = Thread(target=db_updater, name='db-updater',
           args=(db_updates_queue, db))
t.daemon = True
t.start()

if cfg_get('account_release_timeout') > 0:
    log.info(
        "Starting auto-release thread releasing accounts every {} minutes.".format(cfg_get('account_release_timeout')))
    t = Thread(target=auto_release, name='auto-release')
    t.daemon = True
    t.start()
else:
    log.info("Account auto-releasing DISABLED.")

run_server()
