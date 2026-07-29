"""Key management from the command line.

Deliberately not an HTTP endpoint. A self-service key API needs its own
authentication, an account model and a UI — none of which exist. Until they
do, issuing keys is an operator action.

    uv run python -m fpl_api.cli create --name "my app" --type publishable
    uv run python -m fpl_api.cli list
    uv run python -m fpl_api.cli revoke 3
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from fpl_api import db
from fpl_api.auth import KeyType, generate


async def create(name: str, key_type: str, email: str | None, origins: list[str] | None) -> int:
    raw, key_hash, prefix = generate(KeyType(key_type))

    await db.execute(
        """
        insert into app.api_key
            (key_hash, key_prefix, key_type, name, owner_email, allowed_origins)
        values ($1, $2, $3, $4, $5, $6)
        """,
        key_hash,
        prefix,
        key_type,
        name,
        email,
        origins,
    )

    print(f"\n  {raw}\n")
    print("Shown once. Only the hash is stored — there is no way to recover")
    print("this later, and no way for anyone with database access to read it.\n")
    return 0


async def list_keys() -> int:
    rows = await db.fetch(
        """
        select id, key_prefix, key_type, name, created_at, last_used_at, revoked_at
        from app.api_key order by id
        """
    )
    for r in rows:
        state = "revoked" if r["revoked_at"] else "active"
        used = r["last_used_at"].strftime("%Y-%m-%d") if r["last_used_at"] else "never"
        print(
            f"{r['id']:>4}  {r['key_prefix']:<12} {r['key_type']:<12} "
            f"{state:<8} used={used:<12} {r['name']}"
        )
    return 0


async def revoke(key_id: int) -> int:
    await db.execute("update app.api_key set revoked_at = now() where id = $1", key_id)
    print(f"revoked key {key_id}")
    return 0


async def main() -> int:
    ap = argparse.ArgumentParser(prog="fpl_api.cli")
    sub = ap.add_subparsers(dest="command", required=True)

    c = sub.add_parser("create")
    c.add_argument("--name", required=True)
    c.add_argument("--type", choices=["publishable", "secret"], required=True)
    c.add_argument("--email")
    c.add_argument("--origin", action="append", dest="origins")

    sub.add_parser("list")

    r = sub.add_parser("revoke")
    r.add_argument("id", type=int)

    args = ap.parse_args()

    await db.connect()
    try:
        match args.command:
            case "create":
                return await create(args.name, args.type, args.email, args.origins)
            case "list":
                return await list_keys()
            case "revoke":
                return await revoke(args.id)
        return 1
    finally:
        await db.disconnect()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
