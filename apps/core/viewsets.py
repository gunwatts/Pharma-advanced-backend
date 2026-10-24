from django.db.models import ProtectedError, RestrictedError
from rest_framework import viewsets
from rest_framework.exceptions import APIException

from apps.accounts.permissions import ReadOrConfigure
from apps.core import audit
from apps.core.exceptions import ConfirmationRequired
from apps.core.models import SystemSettings


class InUse(APIException):
    status_code = 409
    default_code = "in_use"


def confirmed(request):
    return str(request.query_params.get("confirm", "")).lower() in {"1", "true", "yes"}


class AuditedModelViewSet(viewsets.ModelViewSet):
    """Model viewset that audits every change and asks for confirmation before destructive deletes.

    Override ``impacts(instance)`` to describe dependent configuration that a
    delete would affect; the client must then repeat the call with ``?confirm=true``.
    """

    permission_classes = [ReadOrConfigure]
    read_permission = "admin.configure"
    write_permission = "admin.configure"
    bumps_config_version = True

    def impacts(self, instance):
        return []

    def update_impacts(self, instance, validated_data):
        return []

    def _changed(self):
        if self.bumps_config_version:
            SystemSettings.bump_config_version()

    def perform_create(self, serializer):
        obj = serializer.save()
        audit.record("create", obj=obj, changes={"after": audit.snapshot(obj)})
        self._changed()

    def perform_update(self, serializer):
        impacts = self.update_impacts(serializer.instance, serializer.validated_data)
        if impacts and not confirmed(self.request):
            raise ConfirmationRequired("This change affects other configuration. Review the impacts and confirm.", impacts)
        before = audit.snapshot(serializer.instance)
        obj = serializer.save()
        audit.record("update", obj=obj, changes=audit.diff(before, audit.snapshot(obj)))
        self._changed()

    def perform_destroy(self, instance):
        impacts = self.impacts(instance)
        if impacts and not confirmed(self.request):
            raise ConfirmationRequired("Deleting this item affects other configuration. Review the impacts and confirm.", impacts)
        before = audit.snapshot(instance)
        label, pk = str(instance), instance.pk
        try:
            instance.delete()
        except (ProtectedError, RestrictedError) as exc:
            examples = sorted({str(o) for o in list(exc.protected_objects if hasattr(exc, "protected_objects") else exc.restricted_objects)[:5]})
            raise InUse({"detail": "This item is still in use and cannot be deleted. Deactivate it instead.", "used_by": examples})
        audit.record("delete", object_type=instance._meta.label_lower, object_id=pk, object_repr=label, changes={"before": before})
        self._changed()
