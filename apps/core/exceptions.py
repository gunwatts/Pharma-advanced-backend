"""Exception handling that never leaks internals, secrets or personal data."""

import logging
import uuid

from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404
from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import exception_handler

logger = logging.getLogger("qa.errors")


class ConfirmationRequired(exceptions.APIException):
    """Raised for destructive changes that need an explicit ?confirm=true."""

    status_code = status.HTTP_409_CONFLICT
    default_code = "confirmation_required"

    def __init__(self, message, impacts=None):
        super().__init__(detail={"detail": message, "code": "confirmation_required", "impacts": impacts or []})


def safe_exception_handler(exc, context):
    if isinstance(exc, DjangoValidationError):
        exc = exceptions.ValidationError(exc.message_dict if hasattr(exc, "message_dict") else exc.messages)
    elif isinstance(exc, Http404):
        exc = exceptions.NotFound()
    elif isinstance(exc, PermissionDenied):
        exc = exceptions.PermissionDenied()

    response = exception_handler(exc, context)
    if response is not None:
        return response

    error_id = uuid.uuid4().hex[:12]
    view = context.get("view")
    logger.exception("Unhandled API error", extra={"error_id": error_id, "view": view.__class__.__name__ if view else None})
    return Response(
        {"detail": "An internal error occurred. Contact your administrator with the error reference.", "error_id": error_id},
        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )
