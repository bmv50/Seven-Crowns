"""Extra CA trust scoped to the MAX client, never to the whole process/OS."""
import os
import ssl


def max_ssl_context(ca_file=None):
    path = os.environ.get('MAX_CA_FILE', '').strip() if ca_file is None else ca_file.strip()
    context = ssl.create_default_context()
    # Intermediate certificates help build the chain, but are not independent roots.
    context.verify_flags &= ~ssl.VERIFY_X509_PARTIAL_CHAIN
    if path:
        try:
            context.load_verify_locations(cafile=path)
        except (OSError, ssl.SSLError):
            raise ValueError('MAX_CA_FILE cannot be loaded as a PEM CA bundle') from None
    # Keep hostname and chain verification mandatory, including with a custom bundle.
    assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
    return context
