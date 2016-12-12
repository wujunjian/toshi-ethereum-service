# eth-node

A light service that sits ontop of a standard ethereum node and provides helper functions for creating and sending transactions.

## Running

### Setup env

```
python3 -m virtualenv env
env/bin/pip install -r requirements.txt
```

### Running

```
DATABASE_URL=postgres://<postgres-dsn> REDIS_URL=redis://<redis-dsn> ETHERERUM_NODE_URL=<jsonrpc-url> env/bin/python -m tokeneth
```

## Running on heroku

### Config

#### Buildpacks

```
heroku buildpacks:add https://github.com/debitoor/ssh-private-key-buildpack.git
heroku buildpacks:add https://github.com/tristan/heroku-buildpack-pgsql-stunnel.git
heroku buildpacks:add heroku/python

heroku config:set SSH_KEY=$(cat path/to/your/keys/id_rsa | base64)
```

#### Extra Config variables

```
PGSQL_STUNNEL_ENABLED=1
ETHEREUM_NODE_URL=<jsonrpc-url>
```

The `Procfile` and `runtime.txt` files required for running on heroku
are provided.

### Start

```
heroku ps:scale web:1
```

## Running tests

A convinience script exists to run all tests:
```
./run_tests.sh
```

To run a single test, use:

```
env/bin/python -m tornado.testing tokeneth.test.<test-package>
```
