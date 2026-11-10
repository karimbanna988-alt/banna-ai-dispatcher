"""Path parameters reach handlers decoded.

Every id in the route table that is not numeric is a WhatsApp message
or group id, and those contain "@". The browser sends it as %40. When
the router matched on the raw path and handed the handler "...%40g.us",
the handler compared it against the real "...@g.us", found nothing, and
reported that the record did not exist -- a real job, present in the
database, told the dispatcher it could not be loaded.
"""

import pathlib
import re
import sys
from urllib.parse import quote

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import operator_api  # noqa: E402

# Shaped exactly like a real WhatsApp message id -- the "@" is the
# whole point, since that is what encodeURIComponent turns into
# %40. The digits are invented: a test needs the shape, not a real
# group or device identifier from the owner's account.
REAL_ID = "false_100000000000000000@g.us_3AA0000000000000000A_100000000000000@lid"


def _route(method, path):
    for route_method, pattern, fn, opts in operator_api._COMPILED_ROUTES:
        if route_method != method:
            continue
        m = pattern.match(path)
        if m:
            return pattern, m, fn
    return None, None, None


def test_an_encoded_whatsapp_id_still_matches_its_route():
    path = "/api/crm/job/" + quote(REAL_ID, safe="")
    pattern, m, fn = _route("GET", path)
    assert fn is not None, "the encoded path must still reach a route"
    assert fn.__name__ == "route_crm_job"


def test_the_raw_match_is_the_encoded_value_which_is_why_decoding_is_needed():
    path = "/api/crm/job/" + quote(REAL_ID, safe="")
    _pattern, m, _fn = _route("GET", path)
    assert m.group("message_id") != REAL_ID
    assert "%40" in m.group("message_id")


def test_the_dispatcher_decodes_before_calling_the_handler():
    source = (ROOT / "operator_api.py").read_text(encoding="utf-8")
    body = source[source.index("def _dispatch"):]
    body = body[:body.index("\n    def ", 1)] if "\n    def " in body[1:] else body
    assert "unquote(" in body, "_dispatch must decode path parameters"
    assert re.search(r"result = fn\(self, path_params, body\)", body), \
        "the handler must receive the DECODED params, not m.groupdict()"
    assert "fn(self, m.groupdict(), body)" not in body


def test_decoding_a_plain_numeric_id_is_a_no_op():
    from urllib.parse import unquote
    assert unquote("412") == "412"
