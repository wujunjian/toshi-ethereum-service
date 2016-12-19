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

# Development notes

## Pending transaction monitoring

It's important to note that the pending transaction monitoring handled in
[/tokenbrowser/token-ethereum-service/blob/master/tokeneth/monitor.py](`monitor.py`)
only catches pending transactions that come from outside the connected node. That
is, any new transactions originating from our side do not get picked up by the
`eth_newPendingTransactionFilter`. This makes sense from the point of view of the
node, as the user of the node should already know that it's started broadcasting a
new transaction, but should be kept in mind that this means the notification needs
to be triggered manually when originating from our own services. Even more
consideration will be needed in the future if this evolves to run over multiple
processes connected to multiple different nodes.
