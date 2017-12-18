import codecs
import logging
import os

import sys
from flask import Flask

from pgpool.config import args
from pgpool.models import init_database, Account

logging.basicConfig(level=logging.INFO,
    format='%(asctime)s [%(threadName)16s][%(module)14s][%(levelname)8s] %(message)s')
log = logging.getLogger(__name__)

app = Flask(__name__)


def load_accounts_file(filename):
    accounts = []
    log.info("Loading accounts from file {}.".format(filename))
    with codecs.open(filename, mode='r', encoding='utf-8') as f:
        for line in f:
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
    if len(accounts) == 0:
        log.error("Could not load any accounts. Nothing to do. Exiting.")
        sys.exit(1)
    return accounts


def force_account_condition(account):
    account.ban_flag = 0
    if args.condition == 'good':
        account.banned = 0
        account.shadowbanned = 0
        account.captcha = 0
    elif args.condition == 'banned':
        account.banned = 1
        account.shadowbanned = 0
        account.captcha = 0
    elif args.condition == 'blind':
        account.banned = 0
        account.shadowbanned = 1
        account.captcha = 0
    elif args.condition == 'captcha':
        account.banned = 0
        account.shadowbanned = 0
        account.captcha = 1


# ---------------------------------------------------------------------------

log.info("PGPool CSV Importer starting up...")

db = init_database(app)

filename = args.import_csv
if not os.path.isfile(filename):
    log.error("File {} does not exist.".format(filename))
    sys.exit(1)

accounts = load_accounts_file(filename)
num_accounts = len(accounts)
log.info("Found {} accounts.".format(num_accounts))

num_skipped = 0
num_imported = 0

for acc in accounts:
    username = acc['username']
    account, created = Account.get_or_create(username=username)
    if created:
        account.auth_service = acc['auth_service']
        account.password = acc['password']
        account.level = args.level
        account.system_id = args.system
        if args.condition != 'unknown':
            force_account_condition(account)
        account.save()
        addl_logmsg = ""
        if args.level:
            addl_logmsg += " | Forced trainer level: {}".format(args.level)
        addl_logmsg += " | Initial condition: {}".format(args.condition)
        log.info("Added account {}{}".format(username, addl_logmsg))
        num_imported += 1
    else:
        log.info("Account {} already known. Skipping.".format(username))
        num_skipped += 1

log.info("Done. Imported {} new accounts, skipped {} accounts.".format(num_imported, num_skipped))