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

### Running on heroku

#### Config

```
heroku buildpacks:add python
heroku buildpacks:add https://github.com/tristan/heroku-buildpack-pgsql-stunnel.git
```

The `Procfile` and `runtime.txt` files required for running on heroku
are provided.

The heroku instance needs to have a `CONFIGFILE` Config Variable set
with the filename of the `config-*.ini` file to use.

`config-heroku.ini` is currently the test environment and is hardcoded
to run on the heroku domain toshi-app.herokuapp.com.

`config-production.ini` is the current production environment and is
hardcoded to run on the heroku domain token-service.herokuapp.com.

To run on other domains, the app must have access to a postgresql
instance (generally set up via heroku add-ons), and a `config-*.ini`
file adjusted to point to the respective services, or a `DATABASE_URL`
environment variable set

#### Start

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
