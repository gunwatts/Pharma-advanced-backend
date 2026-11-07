from django.urls import path

from apps.simple import views

urlpatterns = [
    path("layout/", views.workbook_layout, name="simple-layout"),
    path("event-types/", views.event_type_create, name="simple-event-type-create"),
    path("event-types/<int:pk>/", views.event_type_detail, name="simple-event-type"),
    path("template.xlsx", views.template_download, name="simple-template"),
    path("plants/", views.plants, name="simple-plants"),
    path("plants/<int:pk>/", views.plant_detail, name="simple-plant"),
    path("plants/<int:pk>/workbook-link/", views.plant_workbook_link, name="simple-plant-link"),
    path("plants/<int:pk>/workbook-upload/", views.plant_workbook_upload, name="simple-plant-upload"),
    path("plants/<int:pk>/check/", views.plant_check, name="simple-plant-check"),
    path("plants/<int:pk>/departments/", views.plant_departments, name="simple-plant-departments"),
    path("users/", views.users, name="simple-users"),
    path("users/<int:pk>/", views.user_detail, name="simple-user"),
    path("today/", views.today_preview, name="simple-today"),
]
