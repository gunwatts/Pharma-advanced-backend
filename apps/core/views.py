import logging

from django.db import connection
from django.utils import timezone
from rest_framework import generics
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import HasPermission
from apps.core import audit
from apps.core.models import AuditEvent, SystemSettings
from apps.core.serializers import AuditEventSerializer, SystemSettingsSerializer

logger = logging.getLogger("qa.health")


@api_view(["GET"])
@permission_classes([AllowAny])
@throttle_classes([])
def health(request):
    """Liveness/readiness without sensitive details."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        db_ok = True
    except Exception:
        logger.exception("Health check database failure")
        db_ok = False
    return Response({"status": "ok" if db_ok else "degraded", "database": "ok" if db_ok else "error",
                     "time": timezone.now().isoformat()}, status=200 if db_ok else 503)


class SystemSettingsView(APIView):
    def get_permissions(self):
        return [HasPermission.of("admin.configure")()]

    def get(self, request):
        return Response(SystemSettingsSerializer(SystemSettings.load()).data)

    def patch(self, request):
        obj = SystemSettings.load()
        before = audit.snapshot(obj)
        serializer = SystemSettingsSerializer(obj, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        audit.record("update", obj=obj, changes=audit.diff(before, audit.snapshot(obj)))
        SystemSettings.bump_config_version()
        return Response(SystemSettingsSerializer(SystemSettings.load()).data)


class AuditEventList(generics.ListAPIView):
    serializer_class = AuditEventSerializer
    permission_classes = [HasPermission.of("audit.view")]

    def get_queryset(self):
        qs = AuditEvent.objects.all()
        for param in ("action", "object_type", "actor_username"):
            value = self.request.query_params.get(param)
            if value:
                qs = qs.filter(**{param: value})
        return qs
