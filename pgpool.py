import json
import logging
from Queue import Queue
from threading import Thread

from flask import Flask, request, jsonify
from werkzeug.exceptions import abort

from pgpool.config import cfg_get
from pgpool.console import print_status, stats_conditions
from pgpool.models import init_database, db_updater, Account, auto_release, flaskDb, set_webhook_queue

# ---------------------------------------------------------------------------
from pgpool.utils import parse_bool, rss_mem_size
from pgpool.webhook import wh_updater, load_filters

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

@app.route('/status', methods=['GET'])
def status():

    headers = ["Condition", "L1-29", "L30+", "unknown", "TOTAL"]
    lines = "<style> th,td { padding-left: 10px; padding-right: 10px; border: 1px solid #ddd; } table { border-collapse: collapse } td { text-align:center }</style>"
    lines += "Mem Usage: {} | DB Queue Size: {} <br><br>".format(rss_mem_size(), db_updates_queue.qsize())

    lines += "<table><tr>"
    for h in headers:
        lines += "<th>{}</th>".format(h)

    for c in stats_conditions:
        cursor = flaskDb.database.execute_sql('''
            select (case when level < 30 then "low" when level >= 30 then "high" else "unknown" end) as category, count(*) from account
            where {}
            group by category
        '''.format(c[1]))

        low = 0
        high = 0
        unknown = 0
        for row in cursor.fetchall():
            if row[0] == 'low':
                low = row[1]
            elif row[0] == 'high':
                high = row[1]
            elif row[0] == 'unknown':
                unknown = row[1]

        lines += "<tr>"
        lines += "<td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td>".format(c[0], low, high, unknown, low + high + unknown)
        lines += "</tr>"

    lines += "</table>"
    return lines

@app.route('/account/request', methods=['GET'])
def get_accounts():
    system_id = request.args.get('system_id')
    if not system_id:
        log.error("Request from {} is missing system_id".format(request.remote_addr))
        abort(400)

    count = int(request.args.get('count', 1))
    min_level = int(request.args.get('min_level', 1))
    max_level = int(request.args.get('max_level', 40))
    reuse = parse_bool(request.args.get('reuse')) or parse_bool(request.args.get('include_already_assigned'))
    banned_or_new = parse_bool(request.args.get('banned_or_new'))
    # lat = request.args.get('latitude')
    # lat = float(lat) if lat else lat
    # lng = request.args.get('longitude')
    # lng = float(lng) if lng else lng
    log.info(
        "System ID [{}] requested {} accounts level {}-{} from {}".format(system_id, count, min_level, max_level,
                                                                          request.remote_addr))
    accounts = Account.get_accounts(system_id, count, min_level, max_level, reuse, banned_or_new)
    if len(accounts) < count:
        log.warning("Could only deliver {} accounts.".format(len(accounts)))
    return jsonify(accounts[0] if accounts and count == 1 else accounts)

@app.route('/account/requestLure', methods=['GET'])
def get_LureAccounts():
    count = int(request.args.get('count', 1))
    min_level = int(request.args.get('min_level', 1))
    max_level = int(request.args.get('max_level', 40))
    log.info(
        "Lure scout requested {} accounts level {}-{} from {}".format(count, min_level, max_level,
                                                                          request.remote_addr))
    accounts = Account.get_LureAccounts(count, min_level, max_level)
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
    if db_updates_queue.qsize() >= cfg_get('max_queue_size'):
        msg = "DB update queue full ({} items). Ignoring update.".format(db_updates_queue.qsize())
        log.warning(msg)
        return msg, 503

    data = json.loads(request.data)
    if isinstance(data, list):
        for update in data:
            db_updates_queue.put(update)
    else:
        db_updates_queue.put(data)
    return 'ok'

@app.route('/account/add', methods=['GET','POST'])
def account_add():

    def load_accounts(s):
        accounts = []
        for line in s.splitlines():
            if line.strip() == "":
                continue
            auth = usr = pwd = None
            fields = line.split(",")
            if len(fields) == 3:
                auth = fields[0].strip()
                usr = fields[1].strip()
                pwd = fields[2].strip()
            elif len(fields) == 2:
                auth = 'ptc'
                usr = fields[0].strip()
                pwd = fields[1].strip()
            elif len(fields) == 1:
                fields = line.split(":")
                auth = 'ptc'
                usr = fields[0].strip()
                pwd = fields[1].strip()
            if auth is not None:
                accounts.append({
                    'auth_service': auth,
                    'username': usr,
                    'password': pwd
                })
        return accounts

    def force_account_condition(account, condition):
        account.ban_flag = 0
        if condition == 'good':
            account.banned = 0
            account.shadowbanned = 0
            account.captcha = 0
        elif condition == 'banned':
            account.banned = 1
            account.shadowbanned = 0
            account.captcha = 0
        elif condition == 'blind':
            account.banned = 0
            account.shadowbanned = 1
            account.captcha = 0
        elif condition == 'captcha':
            account.banned = 0
            account.shadowbanned = 0
            account.captcha = 1

    def add_account(a):
        account, created = Account.get_or_create(username=a.get('username'))
        account.auth_service = a.get('auth_service', 'ptc')
        account.password = a['password']
        account.level = data.get('level', 1)
        if data.get('condition', 'unknown') != 'unknown':
            force_account_condition(account, data['condition'])
        account.save()
        return True

    if request.method == 'POST':
        if 'accounts' in request.form:
            data = request.form
            accounts = load_accounts(data.get('accounts'))
        elif 'accounts' in request.args:
            data = request.args
            accounts = load_accounts(data.get('accounts'))
        else:
            data = request.get_json()
            if data:
                accounts = data.get('accounts', [])
            else:
                accounts = []

        if data is None or len(accounts) == 0:
            msg = "No accounts provided, or data not parseable"
            log.warning(msg)
            return msg, 503

        if isinstance(accounts, list):
            n = 0
            for acc in accounts:
                if add_account(acc):
                    n += 1
            return "Successfully added {} accounts.".format(n)
        else:
            add_account(accounts)
            return "Successfully added 1 account."
    else:
        page = """<form method=POST>
                   <table>
                   <tr>
                   <td style='padding: 10px'>Level: <input type='number' name='level' min='0' max='40' style='width: 4em'></td>
                   <td>Condition:
                   <select name='condition'>
                        <option value='unknown'></option>
                        <option value='good'>Good</option>
                        <option value='blind'>Blind</option>
                        <option value='banned'>Banned</option>
                        <option value='captcha'>Captcha</option>
                   </select></td>
                   </tr>
                   <tr> </tr>
                   <tr>
                   <td colspan=2><textarea name='accounts' rows='15' style='width: 100%' placeholder='auth,username,password'></textarea></td>
                   </tr>
                   <tr>
                   <td><input type='submit' value='Submit'></td>
                   </tr>
                   </table>
                   </form>
               """
        return page


def run_server():
    app.run(threaded=True, host=cfg_get('host'), port=cfg_get('port'))

# ---------------------------------------------------------------------------

log.info("PGPool starting up...")

# WH updates queue
wh_updates_queue = None
if not cfg_get('wh_filter'):
    log.info('Webhook disabled.')
else:
    log.info('Webhook enabled for events; loading filters from %s',
             cfg_get('wh_filter'))
    if not load_filters(cfg_get('wh_filter')):
        log.warning("Unable to load webhook filters from {}. Exiting...".format(cfg_get('wh_filter')))
        raise SystemExit
    wh_updates_queue = Queue()
    set_webhook_queue(wh_updates_queue)
    # Thread to process webhook updates.
    for i in range(cfg_get('wh_threads')):
        log.debug('Starting wh-updater worker thread %d', i)
        t = Thread(target=wh_updater, name='wh-updater-{}'.format(i), args=(wh_updates_queue,))
        t.daemon = True
        t.start()

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

# Start thread to print current status and get user input.
t = Thread(target=print_status,
           name='status_printer', args=('logs', db_updates_queue))
t.daemon = True
t.start()

run_server()
