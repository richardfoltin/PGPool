import logging
import configargparse
import urllib2
import json
import time

from flask import Flask
from pgpool.config import args
from pgpool.models import init_database, Account

logging.basicConfig(level=logging.INFO,
    format='%(asctime)s [%(threadName)16s][%(module)14s][%(levelname)8s] %(message)s')
log = logging.getLogger(__name__)

app = Flask(__name__)

def load_accounts(api, number):
    while True:        
        accounts = [] # init account list
        log.info("Loading accounts from Lemon2Go service")
        #load accounts from API
        webURL = urllib2.urlopen('http://ptc.shuffletanker.com/Lemon/GetLevel30?extraction_API=' + api + "&amount=" + str(number))
        response = webURL.read()
        data = json.loads(response)
        #process response
        if data['ok']:
            for s in data['message'].split(';'):
                auth = usr = pwd = None
                fields = s.split(':');
                if (len(fields) == 2):
                    auth = 'ptc'
                    usr = fields[0].strip()
                    pwd = fields[1].strip()
                if (auth is not None):
                    accounts.append({
                        'auth_service': auth,
                        'username': usr,
                        'password': pwd
                    })
        else:
            log.error("Lemon API excution error: " + data['message']);
        if len(accounts) == 0:
            log.error("Could not load any accounts. Error: " + data['message'] + "\nRetry in 10 mins")
            time.sleep(600)
        else:
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

log.info("PGPool Lemon2Go Importer starting up...")

db = init_database(app)

while True:
    accounts = load_accounts(args.api, args.number);
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
    log.info("Waiting 11 hours for the next batch.")
    time.sleep(39600)