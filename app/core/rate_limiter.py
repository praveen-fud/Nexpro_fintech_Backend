from slowapi import Limiter
from slowapi.util import get_remote_address

# Single limiter instance shared across all routers.
# Key function is remote IP address — fine for a proxied environment
# because Railway/most reverse proxies set X-Forwarded-For correctly.
limiter = Limiter(key_func=get_remote_address)
