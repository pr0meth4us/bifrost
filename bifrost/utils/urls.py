from flask import current_app, has_request_context, request


def public_url():
    """The origin Bifrost is reachable at, for links and discovery documents.

    Config wins: this string is the OIDC issuer relying parties pin, and an
    issuer a request header can move is an issuer nobody can trust.

    Forwarded headers are the fallback for a deployment that never set the
    variable — a wrong-but-live origin beats a localhost literal that produces a
    plausible-looking discovery document pointing at the user's own machine.
    Outside a request (scheduler, CLI) there are no headers, so callers get ''
    and omit the link rather than emit a broken one.
    """
    configured = current_app.config.get('BIFROST_PUBLIC_URL')
    if configured:
        return configured.rstrip('/')

    if not has_request_context():
        return ''

    host = request.headers.get('X-Forwarded-Host', request.host)
    proto = request.headers.get(
        'X-Forwarded-Proto',
        'http' if host.startswith(('localhost', '127.0.0.1')) else 'https',
    )
    return f"{proto}://{host}"
