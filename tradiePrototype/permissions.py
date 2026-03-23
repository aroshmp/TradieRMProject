"""
tradiePrototype/permissions.py
Custom role-based permissions for Administrator, Technician, and Customer.
"""

from rest_framework.permissions import BasePermission


class IsAdministrator(BasePermission):
    """Allow access only to Administrator role."""
    def has_permission(self, request, view):
        return (
            request.user.is_authenticated and
            hasattr(request.user, 'profile') and
            request.user.profile.is_admin
        )


class IsTechnician(BasePermission):
    """Allow access only to Technician role."""
    def has_permission(self, request, view):
        return (
            request.user.is_authenticated and
            hasattr(request.user, 'profile') and
            request.user.profile.is_technician
        )


class IsCustomer(BasePermission):
    """Allow access only to Customer role."""
    def has_permission(self, request, view):
        return (
            request.user.is_authenticated and
            hasattr(request.user, 'profile') and
            request.user.profile.is_customer
        )


class IsAdminOrTechnician(BasePermission):
    """Allow access to both Administrators and Technicians."""
    def has_permission(self, request, view):
        return (
            request.user.is_authenticated and
            hasattr(request.user, 'profile') and
            request.user.profile.role in ['administrator', 'technician']
        )