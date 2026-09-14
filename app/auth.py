from fastapi import HTTPException, Request


def login_required(request: Request) -> str:
    user = request.session.get("user")
    if not user:
        # Browsers follow the Location header on any 3xx (or error) response,
        # so this doubles as a redirect-to-login for a plain dependency.
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return user
