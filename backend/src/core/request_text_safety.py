"""Refuses a NUL character anywhere in a request, instead of a 500 from Postgres.

Postgres cannot store or compare text containing a NUL character (``\\0``): the
column type accepts it over the wire, then the query fails server-side. A NUL
turns up without anyone meaning harm -- text pasted from some PDFs, or a field
from software that null-terminates strings -- and the API passed it straight
through to the database, turning an ordinary request into a 500.

Fixing this field by field would touch every schema that takes free text, searches
or filters. One check in a single place, wired in once as a global dependency
(see ``app_main.py``), covers all of them instead.
"""

import json

from fastapi import HTTPException, Request

NUL = "\x00"


def _contains_nul(value: object) -> bool:
    """Whether a NUL character appears anywhere in a JSON-decoded value."""
    if isinstance(value, str):
        return NUL in value
    if isinstance(value, dict):
        return any(_contains_nul(key) or _contains_nul(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_nul(item) for item in value)
    return False


async def reject_nul_characters(request: Request) -> None:
    """Refuse any query parameter or JSON body that contains a NUL character.

    Starlette URL-decodes query parameters before we ever see them, so a
    search of ``?search=%00`` already reads as the character ``\\0`` here.
    A JSON body carries it differently: a client serializing an embedded NUL
    writes the escape sequence ``\\u0000`` into the request bytes, never a raw
    0x00 byte, so a byte-level check on the body would miss it. Decoding the
    JSON first and checking the resulting strings catches both.

    Multipart uploads (the product CSV import) are not JSON and are skipped
    here on purpose -- that endpoint reads the file itself and checks each row,
    so one bad cell fails that row instead of the whole request.
    """
    for key, value in request.query_params.multi_items():
        if NUL in key or NUL in value:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Query parameter {key!r} contains a character that is not "
                    "allowed: NUL (\\u0000)."
                ),
            )

    content_type = request.headers.get("content-type", "")
    if content_type.split(";", 1)[0].strip().lower() != "application/json":
        return

    body = await request.body()
    if not body:
        return

    try:
        parsed = json.loads(body)
    except ValueError:
        # Malformed JSON: let FastAPI's own body parsing raise the usual 422.
        return

    if _contains_nul(parsed):
        raise HTTPException(
            status_code=422,
            detail="This request contains a character that is not allowed: NUL (\\u0000).",
        )
