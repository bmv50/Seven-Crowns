"""Versioned explicit terms acceptance, independent of character recreation."""
import hashlib
import os
from urllib.parse import urlsplit


def configuration():
    url = os.getenv('LEGAL_DOCS_URL', '').strip()
    version = os.getenv('MAX_LEGAL_VERSION', '').strip()
    contact = os.getenv('SUPPORT_CONTACT', '').strip()
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or any(c.isspace() for c in url) or not version or not contact
            or '[УКАЖИТЕ' in url + version + contact):
        return None
    digest = hashlib.sha256((version+'\n'+url).encode('utf-8')).hexdigest()[:32]
    return {'url': url, 'version': version, 'contact': contact, 'token': digest}


class ConsentStore:
    def __init__(self, db):
        self.db = db

    async def accepted(self, uid, version):
        return bool(await self.db.pool.fetchval('''SELECT EXISTS(SELECT 1 FROM max_terms_acceptance
            WHERE uid=$1 AND version=$2 AND revoked_at IS NULL)''', uid, version))

    async def accept(self, uid, version):
        if uid >= 0 or not version:
            raise ValueError('Invalid MAX terms acceptance')
        await self.db.pool.execute('''INSERT INTO max_terms_acceptance(uid,version) VALUES($1,$2)
            ON CONFLICT(uid) DO UPDATE SET version=EXCLUDED.version,accepted_at=now(),revoked_at=NULL
            WHERE max_terms_acceptance.version<>EXCLUDED.version OR max_terms_acceptance.revoked_at IS NOT NULL''', uid, version)

    async def revoke(self, uid):
        await self.db.pool.execute('UPDATE max_terms_acceptance SET revoked_at=now() WHERE uid=$1 AND revoked_at IS NULL', uid)
