# PGPool
A simple webservice for account management.

* Keeps a list of unused / in use accounts and which tools/instances they are being used in.
* Stores all relevant account information like level, XP, team, coins, dust, last location, captcha / ban / shadowban status and many more.
* Allows updates to these account information via REST API.
* Logs certain events like when the account gets assigned to a system, when warning / ban flags occur the first time and when they disappear.
* Instantly usable with every software that utilizes the [MrMime pgoapi wrapper library](https://github.com/sLoPPydrive/MrMime)

**PGPool does NOT use any 3rd party API to actually log in to the accounts. It is purely a management software and therefore safe to use.**

PGPool was designed to allow continuous execution of other tools that need to swap accounts every now and then. A typical scenario is a tool that requests a certain number of accounts to operate with, posting account detail updates to PGPool on a regular basis and if they fail release them to the pool and pick a new one. So instead of having a CSV file as account pool you're having a database with tons of more details and account history.

# Support the Author [![Donate](https://img.shields.io/badge/Donate-PayPal-green.svg)](https://www.paypal.me/slop)
If you like PGPool (or my other tools) and feel the need to thank me, just drop me one or more **level 30 accounts** or buy me a **[PokeHash key](https://talk.pogodev.org/d/51-api-hashing-service-by-pokefarmer)**. Seriously, that would be so awesome! :-D You can find me on various Pokémon related Discords as "sLoPPydrive".


# Quick Start

_Again, be aware that PGPool currently only makes sense if you use any product using the [MrMime pgoapi wrapper library](https://github.com/sLoPPydrive/MrMime) (e.g. PGScout, PGNumbra, my RocketMap branch MIX_SCYTHER)._

The only thing you need is a MySQL database set up and the usual `pip install -r requirements.txt`.

## Setting up PGPool

1. Copy `config.json.sample` to `config.json`.
2. Adjust settings for listening host:port and your database in `config.json`.
3. Run PGPool with `python pgpool.py`.

Some words about the non-obvious options in `config.json`:

* `account_release_timeout` defines the time in **minutes** after which accounts that are still assigned (e.g. have not been released properly) to a system but have not been updated in this time will be released to the pool again. Default value is 120 minutes (2 hours). You can set it to 0 to fully disable auto-releasing.

## Importing Accounts

With PGPool comes a tool called `pgpool-import.py` which takes a CSV file and creates new account records in the database. These accounts don't have any account details yet (most important ban/shadowban status), so they won't get picked if a client requests any accounts. For them to become *active* you need to check them first, e.g. by running [PGNumbra Tools](https://github.com/sLoPPydrive/PGNumbra) on them. Commandline options of `pgpool-import.py` are:
```
  -i IMPORT_CSV, --import-csv IMPORT_CSV
             Filename of a CSV file to import accounts from.
  -l LEVEL, --level LEVEL
             Trainer level of imported accounts.
```
The format of the CSV file should be one (and **only** one, don't mix them up!) of:

* `auth,username,password` where `auth` is either `ptc` or `google`
* `username,password` where `auth` will be assumed as `ptc`


# API

Let's assume PGPool runs at the default URL `http://localhost:4242`. Then the following requests are possible:

## Requesting Accounts

**URL:** `http://localhost:4242/account/request`
**Method: GET**

Parameter | Required | Default | Description
--------- | -------- | ------- | -----------
`system_id` | yes | none | Tells PGPool which system is requesting the accounts. Cannot be empty.
`count` | no | 1 | The number of accounts to request
`min_level` | no | 1 | Minimum number of trainer level being requested. If you want reliable IV encounter data your accounts should be at least level 30
`max_level` | no | 40 | Maximum number of trainer level. Maybe you want to reserve level 30 accounts for other tools.
`include_already_assigned` | no | false | If set to yes the client will also receive good accounts that were previously assigned to the given `system_id`. Useful on client startup to reuse accounts.

Returns a JSON object or a list of JSON objects representing accounts. These records do not contain every account detail because the client usually logs in to the accounts and get these details directly from the POGO servers:
```
{
    "auth_service": "ptc",
    "username": "myuser",
    "password": "mypass",
    "latitude": 51.93978239,
    "longitude": 6.98748234,
    "rareless_scans": 10,
    "shadowbanned": true,
    "last_modified": 1503574917
}
```

## Updating Accounts

**URL:** `http://localhost:4242/account/update`
**Method: POST**

Request data is either a single JSON object or a list of JSON objects that contain one or more attributes to set on the account.

Attribute | Description
--------- | -----------
`auth_service` | Either `ptc` or `google`
`username` | Self-explanatory
`password` | Self-explanatory
`email` | Email address being specified on account creation
`last_modified` | Date and time of last update
`system_id` | Identifier of system the account is assigned to
`latitude` | Last location a request was performed
`longitude` | Last location a request was performed
`level` | Trainer-level
`xp` | Experience points
`encounters` | Number of Pokemon encounters (not catches!)
`balls_thrown` | Number of balls thrown at Pokemon
`captures` | Number of Pokemon captures
`spins` | Number of Pokestop spins
`walked` | Number of km walked
`team` | One of `UNSET`, `TEAM_YELLOW`, `TEAM_BLUE` ir `TEAM_RED`
`coins` | Amount of Pokecoins
`stardust` | Amount of Stardust
`warn` | `true` or `false` whether the account has a 3rd party app warning or not
`banned` | `true` or `false`
`ban_flag` | `true` or `false` 
`tutorial_state` | currently unused
`captcha` | `true` or `false` 
`rareless_scans` | Number of consecutive location scans with no rare Pokemon sightings
`shadowbanned` | `true` or `false` - client decision!
`balls` | Number of Pokeballs (or other balls) in inventory
`total_items` | Number of total items in inventory
`pokemon` | Number of Pokemon in bag
`eggs` | Number of eggs
`incubators` | Number of incubators

## Releasing Accounts

**URL:** `http://localhost:4242/account/release`
**Method: POST**

Same as updating accounts (they get updated when they are being released) but the `system_id` is also set to **NULL**.

# Setting up 3rd Party Apps

In your application that utilizes the [MrMime pgoapi wrapper library](https://github.com/sLoPPydrive/MrMime) and that should be linked to PGPool to update account details create or edit `mrmime_config.json` and set at least the following options:

```
{
    "pgpool_url": "http://localhost:4242",
    "pgpool_system_id": "name_of_system"
}

```

Note that this only enables the updating of account details. If your application needs to request and release accounts from and to the pool you will need a modified version of your application (read below for RocketMap integration). Maybe a fully automatic mode will be integrated into MrMime some day.

Other options are possible, their default values are:
```
{
    "pgpool_auto_update": true,         
    "pgpool_update_interval": 60
}
```

* `pgpool_url`: The URL where PGPool is reachable. Must not end with "/".
* `pgpool_system_id`: If the app requests or updates accounts they get marked as being in use by this `system_id`.
* `pgpool_auto_update`: Whether MrMime updates PGPool account details automatically.
* `pgpool_update_interval`: Update account details in PGPool after next API call if this many seconds have passed.


## Special Case: RocketMap

I integrated PGPool into my own [RocketMap fork (branch MIX_SCYTHER)](https://github.com/sLoPPydrive/RocketMap/tree/MIX_SCYTHER) which fully uses all features of PGPool. To use it, download my fork and set the following config parameter:
```
  --pgpool-url <url>
```
Now specifying accounts or CSV files will be ignored. Only the number of `--workers` has to be set. RocketMap also automatically sets the `system_id` for PGPool to the RocketMap `status_name`, so you don't have to worry about that either.

Note that RocketMap-PGPool-support for high-level accounts is not yet done. Use [PGScout](https://github.com/sLoPPydrive/PGScout) instead to scan for IV/CP/moves.

## Special Case: PGNumbra

PGNumbra uses MrMime, so it works with PGPool out of the box. The difference is that PGNumbras `shadowcheck.py` tool never sets a `system_id` because checked accounts are instantly released after being checked.