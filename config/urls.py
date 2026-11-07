from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.accounts.views import ChangePasswordView, LoginView, LogoutView, MeView, RoleViewSet, UserViewSet
from apps.catalog.views import (
    CategoryViewSet,
    DepartmentViewSet,
    FieldDefinitionViewSet,
    RecipientGroupViewSet,
    RelationshipViewSet,
    StatusDefinitionViewSet,
    UnitViewSet,
)
from apps.core import m365_views
from apps.core.views import AuditEventList, SystemSettingsView, health
from apps.dashboards.views import AssignmentViewSet, DashboardViewSet, WidgetViewSet
from apps.records.views import RecordViewSet
from apps.reminders import setup_views
from apps.reminders.job_views import job_tick
from apps.reminders.views import (
    DailyRunViewSet,
    DeliveryViewSet,
    EmailTemplateViewSet,
    JobScheduleViewSet,
    ReminderRuleViewSet,
    SenderProfileViewSet,
)
from apps.sources.views import DataSourceViewSet, SourceMappingViewSet, SyncRunViewSet, browse_graph, browse_server, source_options

router = DefaultRouter()
router.include_root_view = False
router.register("users", UserViewSet, basename="user")
router.register("roles", RoleViewSet, basename="role")
router.register("units", UnitViewSet, basename="unit")
router.register("departments", DepartmentViewSet, basename="department")
router.register("recipient-groups", RecipientGroupViewSet, basename="recipient-group")
router.register("categories", CategoryViewSet, basename="category")
router.register("fields", FieldDefinitionViewSet, basename="field")
router.register("statuses", StatusDefinitionViewSet, basename="status")
router.register("relationships", RelationshipViewSet, basename="relationship")
router.register("sources", DataSourceViewSet, basename="source")
router.register("mappings", SourceMappingViewSet, basename="mapping")
router.register("sync-runs", SyncRunViewSet, basename="sync-run")
router.register("records", RecordViewSet, basename="record")
router.register("senders", SenderProfileViewSet, basename="sender")
router.register("rules", ReminderRuleViewSet, basename="rule")
router.register("templates", EmailTemplateViewSet, basename="template")
router.register("schedules", JobScheduleViewSet, basename="schedule")
router.register("runs", DailyRunViewSet, basename="run")
router.register("deliveries", DeliveryViewSet, basename="delivery")
router.register("dashboards", DashboardViewSet, basename="dashboard")
router.register("widgets", WidgetViewSet, basename="widget")
router.register("dashboard-assignments", AssignmentViewSet, basename="dashboard-assignment")

api = [
    path("health/", health, name="health"),
    path("jobs/tick/", job_tick, name="job-tick"),
    path("m365/status/", m365_views.m365_status, name="m365-status"),
    path("m365/mode/", m365_views.m365_mode, name="m365-mode"),
    path("m365/login/", m365_views.m365_login, name="m365-login"),
    path("m365/callback/", m365_views.m365_callback, name="m365-callback"),
    path("m365/disconnect/", m365_views.m365_disconnect, name="m365-disconnect"),
    path("m365/test/", m365_views.m365_test, name="m365-test"),
    path("auth/login/", LoginView.as_view(), name="login"),
    path("auth/logout/", LogoutView.as_view(), name="logout"),
    path("auth/me/", MeView.as_view(), name="me"),
    path("auth/change-password/", ChangePasswordView.as_view(), name="change-password"),
    path("settings/", SystemSettingsView.as_view(), name="settings"),
    path("audit/", AuditEventList.as_view(), name="audit"),
    path("setup/status/", setup_views.setup_status, name="setup-status"),
    path("setup/progress/", setup_views.setup_progress, name="setup-progress"),
    path("setup/activate/", setup_views.activate, name="setup-activate"),
    path("setup/deactivate/", setup_views.deactivate, name="setup-deactivate"),
    path("source-options/", source_options, name="source-options"),
    path("browse/server/", browse_server, name="browse-server"),
    path("browse/graph/", browse_graph, name="browse-graph"),
    path("simple/", include("apps.simple.urls")),
    path("", include(router.urls)),
]

urlpatterns = [path("api/", include(api))]
if settings.ENABLE_DJANGO_ADMIN:
    urlpatterns.append(path("django-admin/", admin.site.urls))
