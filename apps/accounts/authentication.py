from datetime import timedelta

from django.utils import timezone
from rest_framework import authentication, exceptions

from apps.accounts.models import AuthToken, hash_token


class ExpiringTokenAuthentication(authentication.BaseAuthentication):
    keyword = "Bearer"

    def authenticate(self, request):
        header = authentication.get_authorization_header(request).decode("latin-1")
        if not header:
            return None
        parts = header.split()
        if len(parts) != 2 or parts[0] != self.keyword:
            return None
        try:
            token = AuthToken.objects.select_related("user").get(key_hash=hash_token(parts[1]))
        except AuthToken.DoesNotExist:
            raise exceptions.AuthenticationFailed("Invalid or expired session. Please sign in again.")
        now = timezone.now()
        if token.expires_at <= now or not token.user.is_active:
            token.delete()
            raise exceptions.AuthenticationFailed("Invalid or expired session. Please sign in again.")
        if not token.last_used_at or now - token.last_used_at > timedelta(minutes=5):
            AuthToken.objects.filter(pk=token.pk).update(last_used_at=now)
        request.auth_token_obj = token
        return token.user, token

    def authenticate_header(self, request):
        return self.keyword
