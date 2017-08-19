import copy
import logging
import time
from datetime import datetime, timedelta
from threading import Lock

from peewee import DateTimeField, CharField, SmallIntegerField, IntegerField, \
    DoubleField, BooleanField
from playhouse.flask_utils import FlaskDB
from playhouse.pool import PooledMySQLDatabase
from playhouse.shortcuts import RetryOperationalError, model_to_dict

from pgpool.config import cfg_get

log = logging.getLogger(__name__)

flaskDb = FlaskDB()

request_lock = Lock()


class MyRetryDB(RetryOperationalError, PooledMySQLDatabase):
    pass


class Account(flaskDb.Model):
    auth_service = CharField(max_length=6, default='ptc')
    username = CharField(primary_key=True)
    password = CharField(null=True)
    email = CharField(null=True)
    last_modified = DateTimeField(index=True, default=datetime.utcnow)
    system_id = CharField(index=True, null=True)  # system which uses the account
    latitude = DoubleField(null=True)
    longitude = DoubleField(null=True)
    # from player_stats
    level = SmallIntegerField(index=True, null=True)
    xp = IntegerField(null=True)
    encounters = IntegerField(null=True)
    balls_thrown = IntegerField(null=True)
    captures = IntegerField(null=True)
    spins = IntegerField(null=True)
    walked = DoubleField(null=True)
    # from get_inbox
    team = CharField(max_length=16, null=True)
    coins = IntegerField(null=True)
    stardust = IntegerField(null=True)
    # account health
    warn = BooleanField(null=True)
    banned = BooleanField(index=True, null=True)
    ban_flag = BooleanField(null=True)
    tutorial_state = CharField(null=True)  # a CSV-list of tutorial steps completed
    captcha = BooleanField(index=True, null=True)
    rareless_scans = IntegerField(index=True, null=True)
    shadowbanned = BooleanField(index=True, null=True)
    # inventory info
    balls = SmallIntegerField(null=True)
    total_items = SmallIntegerField(null=True)
    pokemon = SmallIntegerField(null=True)
    eggs = SmallIntegerField(null=True)
    incubators = SmallIntegerField(null=True)

    # @staticmethod
    # def db_format(data):
    #     return {
    #         'auth_service': data.get('auth_service'),
    #         'username': data.get('username'),
    #         'password': data.get('password'),
    #         'email': data.get('email'),
    #         'last_modified': datetime.utcnow(),
    #         'system_id': data.get('system_id'),
    #         'latitude': data.get('latitude'),
    #         'longitude': data.get('longitude'),
    #         'level': data.get('level'),
    #         'xp': data.get('xp'),
    #         'encounters': data.get('encounters'),
    #         'balls_thrown': data.get('balls_thrown'),
    #         'captures': data.get('captures'),
    #         'spins': data.get('spins'),
    #         'walked': data.get('walked'),
    #         'team': data.get('team'),
    #         'coins': data.get('coins'),
    #         'stardust': data.get('stardust'),
    #         'warn': data.get('warn'),
    #         'banned': data.get('banned'),
    #         'ban_flag': data.get('ban_flag'),
    #         'tutorial_state': data.get('tutorial_state'),
    #         'captcha': data.get('captcha'),
    #         'rareless_scans': data.get('rareless_scans'),
    #         'shadowbanned': data.get('shadowbanned'),
    #         'balls': data.get('balls'),
    #         'total_items': data.get('total_items'),
    #         'pokemon': data.get('pokemon'),
    #         'eggs': data.get('eggs'),
    #         'incubators': data.get('incubators')
    #     }

    @staticmethod
    def get_accounts(system_id, count=1, min_level=1, max_level=40, lat=None, lng=None, include_already_assigned=False):
        # Only one client can request accounts at a time
        request_lock.acquire()

        not_banned = Account.banned.is_null(True) | (Account.banned == False)
        not_shadowbanned = Account.shadowbanned.is_null(True) | (Account.shadowbanned == False)

        queries = []
        if include_already_assigned:
            # Look for good accounts for same system_id
            queries.append(Account.select().where((Account.system_id == system_id) & not_banned & not_shadowbanned))
        # Look for good accounts that are unused
        queries.append(Account.select().where(Account.system_id.is_null(True) & not_banned & not_shadowbanned))

        accounts = []
        for query in queries:
            if count > 0:
                # Additional conditions
                if min_level > 1:
                    query = query.where(Account.level >= min_level)
                if max_level < 40:
                    query = query.where(Account.level <= max_level)
                # TODO: Add filter for nearby location

                # Limitations and order
                query = query.limit(count).order_by(Account.last_modified)

                for account in query:
                    old_system_id = account.system_id
                    account.system_id = system_id
                    account.last_modified = datetime.now()
                    account.save()
                    if old_system_id != system_id:
                        new_account_event(account, "Got assigned to [{}]".format(system_id))
                    data = model_to_dict(account)
                    accounts.append({
                        'auth_service': data.get('auth_service'),
                        'username': data.get('username'),
                        'password': data.get('password'),
                        'latitude': data.get('latitude'),
                        'longitude': data.get('longitude'),
                        'rareless_scans': data.get('rareless_scans'),
                        'shadowbanned': data.get('shadowbanned'),
                        'last_modified': data.get('last_modified')
                    })
                    count -= 1

        request_lock.release()
        return accounts


class Event(flaskDb.Model):
    timestamp = DateTimeField(default=datetime.now, index=True)
    type = CharField(max_length=16)
    entity_id = CharField(index=True)
    description = CharField()


# ===========================================================================


def init_database(app):
    log.info('Connecting to MySQL database on %s:%i...',
             cfg_get('db_host'), cfg_get('db_port'))
    db = MyRetryDB(
        cfg_get('db_name'),
        user=cfg_get('db_user'),
        password=cfg_get('db_pass'),
        host=cfg_get('db_host'),
        port=cfg_get('db_port'),
        max_connections=cfg_get('db_max_connections'),
        stale_timeout=300,
        charset='utf8')
    app.config['DATABASE'] = db
    flaskDb.init_app(app)
    create_tables(db)
    return db


def db_updater(q, db):
    # The forever loop.
    while True:
        try:

            while True:
                try:
                    flaskDb.connect_db()
                    break
                except Exception as e:
                    log.warning('%s... Retrying...', repr(e))
                    time.sleep(5)

            # Loop the queue.
            while True:
                data = q.get()
                update_account(data, db)
                q.task_done()

                # Helping out the GC.
                del data

                if q.qsize() > 50:
                    log.warning(
                        "DB queue is > 50 (@%d); try increasing --db-threads.",
                        q.qsize())

        except Exception as e:
            log.exception('Exception in db_updater: %s', repr(e))
            time.sleep(5)


def new_account_event(acc, description):
    evt = Event(type='account', entity_id=acc.username, description=description)
    evt.save()
    log.info("Event for account {}: {}".format(acc.username, description))


def cmp_bool(b1, b2):
    if b1 is None or b2 is None:
        return None
    if not b1 and b2:
        return True
    elif b1 and not b2:
        return False
    else:
        return None


def eval_acc_state_changes(acc_prev, acc_curr):
    level_prev = acc_prev.level
    level_curr = acc_curr.level
    if level_prev is not None and level_curr is not None and level_prev < level_curr:
        new_account_event(acc_curr, "Level {} reached".format(level_curr))

    got_true = cmp_bool(acc_prev.warn, acc_curr.warn)
    if got_true is not None:
        new_account_event(acc_curr, "Got warn flag :-/") if got_true else new_account_event(acc_curr,
                                                                                            "Warn flag lifted :-)")

    got_true = cmp_bool(acc_prev.shadowbanned, acc_curr.shadowbanned)
    if got_true is not None:
        new_account_event(acc_curr, "Got shadowban flag :-(") if got_true else new_account_event(acc_curr,
                                                                                                 "Shadowban flag lifted :-)")

    got_true = cmp_bool(acc_prev.banned, acc_curr.banned)
    if got_true is not None:
        new_account_event(acc_curr, "Got banned :-(((") if got_true else new_account_event(acc_curr, "Ban lifted :-)))")

    got_true = cmp_bool(acc_prev.ban_flag, acc_curr.ban_flag)
    if got_true is not None:
        new_account_event(acc_curr, "Got ban flag :-X") if got_true else new_account_event(acc_curr,
                                                                                           "Ban flag lifted :-O")

    got_true = cmp_bool(acc_prev.captcha, acc_curr.captcha)
    if got_true is not None:
        new_account_event(acc_curr, "Got CAPTCHA'd :-|") if got_true else new_account_event(acc_curr,
                                                                                            "CAPTCHA solved :-)")

    if acc_prev.system_id is not None and acc_curr.system_id is None:
        new_account_event(acc_curr, "Got released from [{}]".format(acc_prev.system_id))

        # if acc_prev.rareless_scans == 0 and acc_curr.rareless_scans > 0:
        #     new_account_event(acc_curr, "Started seeing only commons :-/")
        # if acc_prev.rareless_scans > 0 and acc_curr.rareless_scans == 0:
        #     new_account_event(acc_curr, "Saw rares again :-)")


def update_account(data, db):
    with db.atomic():
        try:
            acc, created = Account.get_or_create(username=data['username'])
            acc_previous = copy.deepcopy(acc)
            for key, value in data.items():
                setattr(acc, key, value)
            acc.last_modified = datetime.now()
            eval_acc_state_changes(acc_previous, acc)
            acc.save()
            log.info("Processed update for {}".format(acc.username))
        except Exception as e:
            # If there is a DB table constraint error, dump the data and
            # don't retry.
            #
            # Unrecoverable error strings:
            unrecoverable = ['constraint', 'has no attribute',
                             'peewee.IntegerField object at']
            has_unrecoverable = filter(
                lambda x: x in str(e), unrecoverable)
            if has_unrecoverable:
                log.warning('%s. Data is:', repr(e))
                log.warning(data.items())
            else:
                log.warning('%s... Retrying...', repr(e))
                time.sleep(1)


def db_cleanup():
    release_timeout = cfg_get('account_release_timeout')
    while True:
        try:
            pastdate = datetime.now() - timedelta(minutes=release_timeout)
            accounts = Account.select().where(
                Account.system_id.is_null(False) & (Account.last_modified <= pastdate))
            if len(accounts) > 0:
                log.info("Releasing {} accounts that haven't been updated in the last {} minutes.".format(len(accounts),
                                                                                                         release_timeout))
            for acc in accounts:
                new_account_event(acc, "Auto-releasing from [{}]".format(acc.system_id))
                acc.system_id = None
                acc.last_modified = datetime.now()
                acc.save()
        except Exception as e:
            log.error(e)

        time.sleep(60)


def create_tables(db):
    db.connect()
    tables = [Account, Event]
    for table in tables:
        if not table.table_exists():
            log.info('Creating table: %s', table.__name__)
            db.create_tables([table], safe=True)
        else:
            log.debug('Skipping table %s, it already exists.', table.__name__)
    db.close()
