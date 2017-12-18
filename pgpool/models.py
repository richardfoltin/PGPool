import copy
import logging
import time
from datetime import datetime, timedelta
from threading import Lock

from peewee import DateTimeField, CharField, SmallIntegerField, IntegerField, \
    DoubleField, BooleanField, InsertQuery
from playhouse.flask_utils import FlaskDB
from playhouse.migrate import migrate, MySQLMigrator
from playhouse.pool import PooledMySQLDatabase
from playhouse.shortcuts import RetryOperationalError

from pgpool.config import cfg_get
from pgpool.utils import cmp_bool

log = logging.getLogger(__name__)

flaskDb = FlaskDB()

request_lock = Lock()

db_schema_version = 2

class MyRetryDB(RetryOperationalError, PooledMySQLDatabase):
    pass


# Reduction of CharField to fit max length inside 767 bytes for utf8mb4 charset
class Utf8mb4CharField(CharField):
    def __init__(self, max_length=191, *args, **kwargs):
        self.max_length = max_length
        super(CharField, self).__init__(*args, **kwargs)


class Version(flaskDb.Model):
    key = Utf8mb4CharField()
    val = SmallIntegerField()

    class Meta:
        primary_key = False


class Account(flaskDb.Model):
    auth_service = Utf8mb4CharField(max_length=6, default='ptc')
    username = Utf8mb4CharField(primary_key=True)
    password = Utf8mb4CharField(null=True)
    email = Utf8mb4CharField(null=True)
    last_modified = DateTimeField(index=True, default=datetime.now)
    system_id = Utf8mb4CharField(max_length=64, index=True, null=True)  # system which uses the account
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
    team = Utf8mb4CharField(max_length=16, null=True)
    coins = IntegerField(null=True)
    stardust = IntegerField(null=True)
    # account health
    warn = BooleanField(null=True)
    banned = BooleanField(index=True, null=True)
    ban_flag = BooleanField(null=True)
    tutorial_state = Utf8mb4CharField(null=True)  # a CSV-list of tutorial steps completed
    captcha = BooleanField(index=True, null=True)
    rareless_scans = IntegerField(index=True, null=True)
    shadowbanned = BooleanField(index=True, null=True)
    # inventory info
    balls = SmallIntegerField(null=True)
    total_items = SmallIntegerField(null=True)
    pokemon = SmallIntegerField(null=True)
    eggs = SmallIntegerField(null=True)
    incubators = SmallIntegerField(null=True)
    lures = SmallIntegerField(null=True)

    @staticmethod
    def get_accounts(system_id, count=1, min_level=1, max_level=40, reuse=False, banned_or_new=False):
        # Only one client can request accounts at a time
        request_lock.acquire()

        main_condition = None
        if banned_or_new:
            main_condition = Account.banned.is_null(True) | (Account.banned == True) | (Account.shadowbanned == True)
            reuse = False
        else:
            main_condition = (Account.banned == False) & (Account.shadowbanned == False)

        queries = []
        if reuse:
            # Look for good accounts for same system_id
            queries.append(Account.select().where((Account.system_id == system_id) & main_condition))
        # Look for good accounts that are unused
        queries.append(Account.select().where(Account.system_id.is_null(True) & main_condition))

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
                    accounts.append({
                        'auth_service': account.auth_service,
                        'username': account.username,
                        'password': account.password,
                        'latitude': account.latitude,
                        'longitude': account.longitude,
                        'rareless_scans': account.rareless_scans,
                        'shadowbanned': account.shadowbanned,
                        'last_modified': account.last_modified,
                        'lures': account.lures
                    })

                    old_system_id = account.system_id
                    account.system_id = system_id
                    account.last_modified = datetime.now()
                    account.save()

                    if old_system_id != system_id:
                        new_account_event(account, "Got assigned to [{}]".format(system_id))

                    count -= 1

        request_lock.release()
        return accounts


class Event(flaskDb.Model):
    timestamp = DateTimeField(default=datetime.now, index=True)
    entity_type = Utf8mb4CharField(max_length=16)
    entity_id = Utf8mb4CharField(index=True)
    description = Utf8mb4CharField()


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
        charset='utf8mb4')
    app.config['DATABASE'] = db
    flaskDb.init_app(app)
    db.connect()

    if not Account.table_exists():
        create_tables(db)
        InsertQuery(Version, {Version.key: 'schema_version',
                              Version.val: db_schema_version}).execute()
        old_schema_version = db_schema_version
    elif not Version.table_exists():
        old_schema_version = 1
    else:
        old_schema_version = Version.get(Version.key == 'schema_version').val
    if old_schema_version < db_schema_version:
        migrate_database(db, old_schema_version)

    # Last, fix database encoding
    verify_table_encoding(db)

    return db


def verify_table_encoding(db):
    with db.execution_context():
        cmd_sql = '''
            SELECT table_name FROM information_schema.tables WHERE
            table_collation != "utf8mb4_unicode_ci"
            AND table_schema = "{}";
            '''.format(cfg_get('db_name'))
        change_tables = db.execute_sql(cmd_sql)

        if change_tables.rowcount > 0:
            log.info('Changing collation and charset on database.')
            cmd_sql = "ALTER DATABASE {} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci".format(cfg_get('db_name'))
            db.execute_sql(cmd_sql)

            log.info('Changing collation and charset on {} tables.'.format(change_tables.rowcount))
            db.execute_sql('SET FOREIGN_KEY_CHECKS=0;')
            for table in change_tables:
                log.debug('Changing collation and charset on table {}.'.format(table[0]))
                cmd_sql = '''ALTER TABLE {} CONVERT TO CHARACTER SET utf8mb4
                            COLLATE utf8mb4_unicode_ci;'''.format(str(table[0]))
                db.execute_sql(cmd_sql)
            db.execute_sql('SET FOREIGN_KEY_CHECKS=1;')


def migrate_database(db, old_ver):
    log.info('Detected database version {}, updating to {}...'.format(old_ver, db_schema_version))
    migrator = MySQLMigrator(db)

    if old_ver < 2:
        migrate_varchar_columns(db, Account.username, Account.password, Account.email, Account.system_id,
                                Account.tutorial_state)
        migrate_varchar_columns(db, Event.entity_id, Event.description)

        db.create_table(Version)
        InsertQuery(Version, {Version.key: 'schema_version',
                              Version.val: 1}).execute()

        migrate(
            migrator.add_column('account', 'lures',
                                SmallIntegerField(null=True)),
            migrator.rename_column('event', 'type', 'entity_type')
        )

    Version.update(val=db_schema_version).where(
        Version.key == 'schema_version').execute()
    log.info("Done migrating database.")


def migrate_varchar_columns(db, *fields):
    stmts = []
    cols = []
    table = None
    for field in fields:
        if isinstance(field, Utf8mb4CharField):
            if table == None:
                table = field.model_class._meta.db_table
            elif table != field.model_class._meta.db_table:
                log.error("Can only migrate varchar columns of same table: {} vs. {}".format(table,
                                                                                             field.model_class._meta.db_table))
            column = field.db_column
            cols.append(column)
            max_length = field.max_length
            stmt = "CHANGE COLUMN {} {} VARCHAR({}) ".format(column, column, max_length)
            stmt += "DEFAULT NULL" if field.null else "NOT NULL"
            stmts.append(stmt)

    log.info("Converting VARCHAR columns {} on table {}".format(', '.join(cols), table))
    db.execute_sql("ALTER TABLE {} {};".format(table, ', '.join(stmts)))


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

        except Exception as e:
            log.exception('Exception in db_updater: %s', repr(e))
            time.sleep(5)


def new_account_event(acc, description):
    description = (description[:189] + '..') if len(description) > 189 else description
    evt = Event(entity_type='account', entity_id=acc.username, description=description)
    evt.save()
    log.info("Event for account {}: {}".format(acc.username, description))


def eval_acc_state_changes(acc_prev, acc_curr, metadata):
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
        new_account_event(acc_curr, "Got released from [{}]: {}".format(acc_prev.system_id,
                                                                        metadata.get('_release_reason',
                                                                                     'unknown reason')))

        # if acc_prev.rareless_scans == 0 and acc_curr.rareless_scans > 0:
        #     new_account_event(acc_curr, "Started seeing only commons :-/")
        # if acc_prev.rareless_scans > 0 and acc_curr.rareless_scans == 0:
        #     new_account_event(acc_curr, "Saw rares again :-)")


def update_account(data, db):
    with db.atomic():
        try:
            acc, created = Account.get_or_create(username=data['username'])
            acc_previous = copy.deepcopy(acc)
            metadata = {}
            for key, value in data.items():
                if not key.startswith('_'):
                    setattr(acc, key, value)
                else:
                    metadata[key] = value
            acc.last_modified = datetime.now()
            eval_acc_state_changes(acc_previous, acc, metadata)
            acc.save()
            if cfg_get('log_updates'):
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


def auto_release():
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

    tables = [Account, Event, Version]
    for table in tables:
        if not table.table_exists():
            log.info('Creating table: %s', table.__name__)
            db.create_tables([table], safe=True)
        else:
            log.debug('Skipping table %s, it already exists.', table.__name__)
    db.close()
