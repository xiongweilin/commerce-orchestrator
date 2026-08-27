"""Commerce adapters for portable responsibility contracts.

Only public/reference semantic seams belong here.  DBOS and provider execution
internals remain outside this package.
"""

from app.responsibility.store import CommerceResponsibilityStore

__all__ = ["CommerceResponsibilityStore"]
