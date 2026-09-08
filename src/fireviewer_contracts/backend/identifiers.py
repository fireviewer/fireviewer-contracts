import re

FIRE_ID_RE = re.compile(r"^FR-[0-9A-Z]{2,3}-[0-9]{5}$")
SOURCE_KEY_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{1,126}[a-zA-Z0-9]$")
TRACE_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$")
IDEMPOTENCY_KEY_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:/-]{7,127}$")
TERRITORY_CODE_RE = re.compile(r"^[0-9A-Z]{2,3}$")


