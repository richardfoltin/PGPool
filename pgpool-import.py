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

usernames = []
passwords = []
auth_services = []
num_accounts = 0


def read_csv(filename):
    global usernames
    global passwords
    global auth_services
    global num_accounts

    # Giving num_fields something it would usually not get.
    num_fields = -1
    with open(filename, 'r') as f:
        for num, line in enumerate(f, 1):

            fields = []

            # First time around populate num_fields with current field
            # count.
            if num_fields < 0:
                num_fields = line.count(',') + 1

            csv_input = []
            csv_input.append('')
            csv_input.append('<username>')
            csv_input.append('<username>,<password>')
            csv_input.append('<ptc/google>,<username>,<password>')

            # If the number of fields is differend this is not a CSV.
            if num_fields != line.count(',') + 1:
                print(sys.argv[0] +
                      ": Error parsing CSV file on line " + str(num) +
                      ". Your file started with the following " +
                      "input, '" + csv_input[num_fields] +
                      "' but now you gave us '" +
                      csv_input[line.count(',') + 1] + "'.")
                sys.exit(1)

            field_error = ''
            line = line.strip()

            # Ignore blank lines and comment lines.
            if len(line) == 0 or line.startswith('#'):
                continue

            # If number of fields is more than 1 split the line into
            # fields and strip them.
            if num_fields > 1:
                fields = line.split(",")
                fields = map(str.strip, fields)

            # If the number of fields is one then assume this is
            # "username". As requested.
            if num_fields == 1:
                # Empty lines are already ignored.
                usernames.append(line)

            # If the number of fields is two then assume this is
            # "username,password". As requested.
            if num_fields == 2:
                # If field length is not longer than 0 something is
                # wrong!
                if len(fields[0]) > 0:
                    usernames.append(fields[0])
                else:
                    field_error = 'username'

                # If field length is not longer than 0 something is
                # wrong!
                if len(fields[1]) > 0:
                    passwords.append(fields[1])
                else:
                    field_error = 'password'

            # If the number of fields is three then assume this is
            # "ptc,username,password". As requested.
            if num_fields == 3:
                # If field 0 is not ptc or google something is wrong!
                if (fields[0].lower() == 'ptc' or
                            fields[0].lower() == 'google'):
                    auth_services.append(fields[0])
                else:
                    field_error = 'method'

                # If field length is not longer then 0 something is
                # wrong!
                if len(fields[1]) > 0:
                    usernames.append(fields[1])
                else:
                    field_error = 'username'

                # If field length is not longer then 0 something is
                # wrong!
                if len(fields[2]) > 0:
                    passwords.append(fields[2])
                else:
                    field_error = 'password'

            if num_fields > 3:
                print(('Too many fields in accounts file: max ' +
                       'supported are 3 fields. ' +
                       'Found {} fields').format(num_fields))
                sys.exit(1)

            # If something is wrong display error.
            if field_error != '':
                type_error = 'empty!'
                if field_error == 'method':
                    type_error = (
                        'not ptc or google instead we got \'' +
                        fields[0] + '\'!')
                print(sys.argv[0] +
                      ": Error parsing CSV file on line " + str(num) +
                      ". We found " + str(num_fields) + " fields, " +
                      "so your input should have looked like '" +
                      csv_input[num_fields] + "'\nBut you gave us '" +
                      line + "', your " + field_error +
                      " was " + type_error)
                sys.exit(1)

            num_accounts += 1

log.info("PGPool CSV Importer starting up...")

db = init_database(app)

filename = args.import_csv
if not os.path.isfile(filename):
    log.error("File {} does not exist.".format(filename))
    sys.exit(1)

read_csv(filename)
log.info("Found {} accounts in file {}.".format(num_accounts, filename))

num_skipped = 0
num_imported = 0

for i in range(0, num_accounts):
    username = usernames[i]
    password = passwords[i]
    auth_service = auth_services[i] if len(auth_services) == num_accounts else 'ptc'

    acc, created = Account.get_or_create(username=username)
    if created:
        acc.auth_service = auth_service
        acc.password = password
        acc.save()
        log.info("Added new account {} to pool.".format(username))
        num_imported += 1
    else:
        log.info("Account {} already known. Skipping.".format(username))
        num_skipped += 1

log.info("Done. Imported {} new accounts, skipped {} accounts.".format(num_imported, num_skipped))