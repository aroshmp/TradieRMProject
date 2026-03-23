"""
URL configuration for TradieRMProject project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework.authtoken.views import obtain_auth_token
from tradiePrototype.views import (
    CustomerViewSet, TechnicianViewSet,
    JobViewSet, JobPartViewSet, ScheduleBlockViewSet,
    InvoiceViewSet, ClientRequestViewSet,
    AIResponseSuggestionViewSet, webhook_intake,
    register_customer, register_technician, logout, me,
)

router = DefaultRouter()
router.register(r'customers',        CustomerViewSet,             basename='customer')
router.register(r'technicians',      TechnicianViewSet,           basename='technician')
router.register(r'jobs',             JobViewSet,                  basename='job')
router.register(r'job-parts',        JobPartViewSet,              basename='jobpart')
router.register(r'schedule',         ScheduleBlockViewSet,        basename='schedule')
router.register(r'invoices',         InvoiceViewSet,              basename='invoice')
router.register(r'client-requests',  ClientRequestViewSet,        basename='clientrequest')
router.register(r'ai-suggestions',   AIResponseSuggestionViewSet, basename='aisuggestion')

urlpatterns = [
    path('admin/',                        admin.site.urls),
    path('api/',                          include(router.urls)),
    path('api/auth/login/',               obtain_auth_token,   name='api-token-auth'),
    path('api/auth/logout/',              logout,              name='api-logout'),
    path('api/auth/register/customer/',   register_customer,   name='api-register-customer'),
    path('api/auth/register/technician/', register_technician, name='api-register-technician'),
    path('api/auth/me/',                  me,                  name='api-me'),
    path('api/webhook/job-request/',      webhook_intake,      name='webhook-intake'),
]
