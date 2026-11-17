"""Shared helpers: env loading + ArangoDB connection."""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

def env(name: str, default: str | None = None) -> str | None:
    """Read an env var, tolerating surrounding quotes.

    `python-dotenv` strips quotes when it parses `.env`; almost nothing else does.
    `docker run --env-file .env` passes `PASSWORD="s3cret"` through with the quote
    characters attached, and the platform injecting variables into a BYOC container
    makes its own choice. The failure is a bare `[HTTP 401] bad username/password`
    with nothing pointing at quoting, which is an expensive hour.

    A surrounding quote is never a legitimate part of an endpoint, user, password or
    database name here, so stripping one is safe and saves that hour.
    """
    v = os.environ.get(name, default)
    if isinstance(v, str):
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
    return v


DB_NAME = env("ARANGO_DATABASE", "gdelt_market")


def db(name: str | None = None):
    """Return a python-arango StandardDatabase for the target DB."""
    from arango import ArangoClient

    endpoint = env("ARANGO_ENDPOINT")
    if not endpoint:
        raise SystemExit("ARANGO_ENDPOINT is not set — in a container these arrive "
                         "as environment variables, not from a .env file")
    password = env("ARANGO_PASSWORD")
    if not password:
        raise SystemExit("ARANGO_PASSWORD is not set")
    verify = (env("ARANGO_VERIFY_SSL", "true") or "true").lower() == "true"
    client = ArangoClient(hosts=endpoint.rstrip("/"), request_timeout=120)
    return client.db(
        name or DB_NAME,
        username=env("ARANGO_USER", "root"),
        password=password,
        verify=verify,
    )


# Physical schema (see docs/schema.md) — single source of truth for build_graph.py.
NODE_COLLECTIONS = [
    "Event", "Actor", "Location", "Theme", "Source",
    "Company", "Sector", "Index", "Commodity", "MacroVariable",
    "Holding", "Portfolio", "Exposure",
]
EDGE_COLLECTIONS = [
    "mentions", "taggedWith", "locatedIn", "partOf", "holds",   # structural
    "precedes", "coOccursWith",                        # temporal
    "similarTo", "correlatesWith", "affects",          # inferred
]
GRAPH_NAME = "gdelt_market"
