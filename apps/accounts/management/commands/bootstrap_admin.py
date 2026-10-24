import os

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import Role, UserProfile


class Command(BaseCommand):
    help = "Create (or update) an administrator from ADMIN_USERNAME / ADMIN_EMAIL / ADMIN_PASSWORD environment variables."

    def handle(self, *args, **opts):
        username = os.environ.get("ADMIN_USERNAME")
        password = os.environ.get("ADMIN_PASSWORD")
        email = os.environ.get("ADMIN_EMAIL", "")
        if not username or not password:
            raise CommandError("Set ADMIN_USERNAME and ADMIN_PASSWORD (remove ADMIN_PASSWORD from the environment afterwards).")
        User = get_user_model()
        user, created = User.objects.get_or_create(username=username, defaults={"email": email})
        validate_password(password, user=user)
        user.set_password(password)
        user.email = email or user.email
        user.is_staff = True
        user.save()
        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.role = Role.objects.get(key="admin")
        profile.all_data_scope = True
        profile.save()
        self.stdout.write(f"Administrator '{username}' {'created' if created else 'updated'}.")
