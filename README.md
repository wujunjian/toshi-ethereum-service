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
DATABASE_URL=postgres://<postgres-dsn> REDIS_URL=redis://<redis-dsn> ETHERERUM_NODE_URL=<jsonrpc-url> env/bin/python -m toshieth
```

## Running on heroku

### Add heroku git

```
heroku git:remote -a <heroku-project-name> -r <remote-name>
```

### Config

NOTE: if you have multiple deploys you need to append
`--app <heroku-project-name>` to all the following commands.

#### Addons

```
heroku addons:create heroku-postgresql:hobby-basic
heroku addons:create heroku-redis:hobby-dev
```

#### Buildpacks

```
heroku buildpacks:add https://github.com/debitoor/ssh-private-key-buildpack.git
heroku buildpacks:add https://github.com/weibeld/heroku-buildpack-run.git
heroku buildpacks:add https://github.com/tristan/heroku-buildpack-pgsql-stunnel.git
heroku buildpacks:add heroku/python

heroku config:set SSH_KEY=$(cat path/to/your/keys/id_rsa | base64)
heroku config:set BUILDPACK_RUN=configure_environment.sh
```

#### Extra Config variables

```
heroku config:set PUSH_URL=<toshi-push-service-url>
heroku config:set PUSH_USERNAME=<toshi-push-service-username>
heroku config:set PUSH_PASSWORD=<toshi-push-service-password>
heroku config:set PGSQL_STUNNEL_ENABLED=1
heroku config:set ETHEREUM_NODE_URL=<jsonrpc-url>
```

Optional:

```
heroku config:set MONITOR_ETHEREUM_NODE_URL=<jsonrpc-url>
heroku config:set SLACK_LOG_URL=<slack-webhook-url>
heroku config:set SLACK_LOG_USERNAME="toshi-eth-log-bot"
```

The `Procfile` and `runtime.txt` files required for running on heroku
are provided.

### Start

```
heroku ps:scale web:1
```

## Running tests

Install external software dependencies

```
brew install postgresql
brew install redis
brew tap ethcore/ethcore
brew install parity --beta
brew tap ethereum/ethereum
brew install cpp-ethereum
```

A convinience script exists to run all tests:
```
./run_tests.sh
```

To run a single test, use:

```
env/bin/python -m tornado.testing toshieth.test.<test-package>
```

# Structure

- Push notification sender
- websocket handler
- transaction queue manager

### manager.py

Transaction queue manager

- managing the tx queue
- updating tx state
- triggering state change notifications

### push_service.py

- sending push notifications

### monitor.py

Ethereum Node monitor

- monitoring new blocks
- monitoring new pending transactions
- monitoring newly confirmed transaction

### websocket.py : SubscriptionManager

- sending notifications to websocket clients
