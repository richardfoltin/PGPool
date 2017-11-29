import json
import os

# Configuration with default values
import configargparse

cfg = {
    'host': '127.0.0.1',
    'port': 4242,
    'db_host': 'localhost',
    'db_port': 3306,
    'db_name': '',
    'db_user': '',
    'db_pass': '',
    'db_max_connections': 20,
    'log_updates': True,
    'account_release_timeout': 120,     # Accounts are being released automatically after this many minutes from last update
    'max_queue_size': 50                # Block update requests if queue already has this many items
}


def cfg_get(key, default=None):
    return cfg.get(key, default)

# ===========================================================================

parser = configargparse.ArgParser()
parser.add_argument('-c', '--config',
                    help=('Specify different config file. Default: config.json'),
                    default='config.json')
parser.add_argument('-i', '--import-csv',
                    help=('Filename of a CSV file to import accounts from.'),
                    default=None)
parser.add_argument('-l', '--level',
                    help=('Trainer level of imported accounts.'),
                    type=int, default=None)
args = parser.parse_args()

with open(args.config, 'r') as f:
    user_cfg = json.loads(f.read())
    cfg.update(user_cfg)
