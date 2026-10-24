"""Token-protected scheduler trigger for hosts without a built-in scheduler.

An external cron (e.g. a scheduled GitHub Actions workflow) POSTs here every
15-30 minutes. Each call runs ``scheduler.tick()``, which starts a job only when
it is due and at most once per business day, guarded by the DB run lock.
"""

import hmac
import logging

from django.conf import settings
from rest_framework.decorators import api_view, authentication_classes, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle

from apps.reminders.services import scheduler

logger = logging.getLogger("qa.jobs")


class JobTriggerThrottle(SimpleRateThrottle):
    scope = "sensitive"

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": "job_trigger", "ident": self.get_ident(request)}


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([JobTriggerThrottle])
def job_tick(request):
    expected = settings.JOB_TRIGGER_TOKEN
    supplied = request.headers.get("X-Job-Token", "")
    if not expected:
        return Response({"detail": "Not found."}, status=404)  # feature disabled unless a token is configured
    if not hmac.compare_digest(supplied.encode(), expected.encode()):
        logger.warning("Rejected job trigger with invalid token")
        return Response({"detail": "Invalid job token."}, status=403)
    results = scheduler.tick(triggered_by="external-trigger")
    return Response({"ran": results})
