"""
tradiePrototype/views.py

All API views for the TradieRM backend.

Viewsets handle standard CRUD operations via the DRF router.
Function-based views handle authentication, webhook intake, and
any action that falls outside standard resource CRUD.

Role access summary:
    Administrator  -- full access to all resources
    Technician     -- own schedule, own jobs, AI suggestion approval
    Customer       -- own jobs and invoices (read-only in most cases)
"""

import logging
from datetime import date

from django.utils import timezone
from django.contrib.auth.models import User
from rest_framework import viewsets, status
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.authtoken.models import Token

from .models import (
    Customer, Technician, Job, JobPart, ScheduleBlock,
    Invoice, ClientRequest, AIResponseSuggestion, UserProfile,
)
from .serializers import (
    CustomerSerializer, TechnicianSerializer,
    JobSerializer, JobCreateSerializer, JobPartSerializer,
    ScheduleBlockSerializer, InvoiceSerializer,
    ClientRequestSerializer, WebhookInboundSerializer,
    AIResponseSuggestionSerializer, ApproveResponseSerializer,
)
from .services.scheduler import schedule_job, get_technician_schedule
from .services.invoice_generator import generate_invoice
from .services.confirmation import send_confirmation
from .services.ai_responder import generate_ai_suggestion
from .permissions import IsAdministrator, IsTechnician, IsCustomer, IsAdminOrTechnician

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Customer ViewSet
# ---------------------------------------------------------------------------

class CustomerViewSet(viewsets.ModelViewSet):
    """
    UC1 -- Add Customer (and full CRUD).

    Access: Administrator only.
    Customers are created and managed exclusively by administrators.
    Self-registration is not permitted.
    """

    queryset           = Customer.objects.all()
    serializer_class   = CustomerSerializer
    permission_classes = [IsAdministrator]


# ---------------------------------------------------------------------------
# Technician ViewSet
# ---------------------------------------------------------------------------

class TechnicianViewSet(viewsets.ModelViewSet):
    """
    UC6 -- Add Technician (and full CRUD).

    Access: Administrator only.
    Only active technicians are returned. Inactive records are retained
    for historical reference but excluded from listings.
    Technicians are created and managed exclusively by administrators.
    Self-registration is not permitted.
    """

    queryset           = Technician.objects.filter(is_active=True)
    serializer_class   = TechnicianSerializer
    permission_classes = [IsAdministrator]


# ---------------------------------------------------------------------------
# Job ViewSet
# ---------------------------------------------------------------------------

class JobViewSet(viewsets.ModelViewSet):
    """
    UC2 -- Add Job (and full CRUD).

    Access:
        Administrator -- full access to all job records.
        Customer      -- read access restricted to their own jobs only,
                         matched by email address.

    The create action uses a dedicated lightweight serializer (JobCreateSerializer)
    to restrict the writable fields on creation. All other actions use the full
    JobSerializer which includes nested parts and computed fields.
    """

    queryset           = Job.objects.select_related('customer', 'technician').prefetch_related('parts')
    permission_classes = [IsAdministrator | IsCustomer]

    def get_serializer_class(self):
        """Return the appropriate serializer based on the current action."""
        return JobCreateSerializer if self.action == 'create' else JobSerializer

    def get_queryset(self):
        """
        Restrict the queryset for customer-role users to their own jobs.
        Administrators receive the full queryset with related objects pre-fetched.
        """
        user    = self.request.user
        profile = getattr(user, 'profile', None)

        if profile and profile.is_customer:
            return Job.objects.filter(customer__email=user.email)

        return Job.objects.select_related('customer', 'technician').prefetch_related('parts')

    @action(detail=True, methods=['post'])
    def book(self, request, pk=None):
        """
        UC3 -- Schedule the Booking.

        Creates a ScheduleBlock for the job. If a technician is already
        assigned, travel time blocks are automatically inserted before and
        after the job block (BR6).
        """
        job  = self.get_object()
        data = request.data.copy()
        data['job'] = job.pk

        result = schedule_job(job, data)
        if result.get('error'):
            return Response({'error': result['error']}, status=status.HTTP_400_BAD_REQUEST)

        return Response(result, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def complete(self, request, pk=None):
        """
        UC -- Complete a Job and generate an invoice (BR10).

        Expects 'labour_hours' and optionally 'labour_rate' in the request body.
        Marks the job status as COMPLETED and triggers invoice generation.
        """
        job          = self.get_object()
        labour_hours = float(request.data.get('labour_hours', 0))
        labour_rate  = request.data.get('labour_rate')

        job.status = Job.Status.COMPLETED
        job.save(update_fields=['status'])

        invoice = generate_invoice(
            job,
            labour_hours=labour_hours,
            labour_rate=float(labour_rate) if labour_rate else None,
        )

        return Response({
            'job':     JobSerializer(job).data,
            'invoice': InvoiceSerializer(invoice).data,
        }, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# Job Part ViewSet
# ---------------------------------------------------------------------------

class JobPartViewSet(viewsets.ModelViewSet):
    """
    UC5 -- Add New Inventory / Job Parts (and full CRUD).

    Access: Administrator only.
    Supports optional filtering by job via query parameter: ?job=<id>
    """

    queryset           = JobPart.objects.all()
    serializer_class   = JobPartSerializer
    permission_classes = [IsAdministrator]

    def get_queryset(self):
        """Filter parts by job ID if the 'job' query parameter is provided."""
        job_id = self.request.query_params.get('job')
        if job_id:
            return JobPart.objects.filter(job_id=job_id)
        return super().get_queryset()


# ---------------------------------------------------------------------------
# Schedule Block ViewSet
# ---------------------------------------------------------------------------

class ScheduleBlockViewSet(viewsets.ReadOnlyModelViewSet):
    """
    UC3 / BR6 -- View technician schedule blocks.

    Access:
        Administrator -- all technicians' schedule blocks.
        Technician    -- own schedule blocks only, matched by email address.

    Read-only. Blocks are created indirectly via the Job.book action.
    """

    queryset           = ScheduleBlock.objects.select_related('technician', 'job')
    serializer_class   = ScheduleBlockSerializer
    permission_classes = [IsAdminOrTechnician]

    def get_queryset(self):
        """Restrict queryset to the requesting technician's own blocks if applicable."""
        user    = self.request.user
        profile = getattr(user, 'profile', None)

        if profile and profile.is_technician:
            return ScheduleBlock.objects.filter(technician__email=user.email)

        return ScheduleBlock.objects.select_related('technician', 'job')


# ---------------------------------------------------------------------------
# Invoice ViewSet
# ---------------------------------------------------------------------------

class InvoiceViewSet(viewsets.ModelViewSet):
    """
    UC -- View and manage invoices (BR10).

    Access: Administrator only.
    Invoices are generated automatically when a job is completed.
    The customer record is pre-fetched via the related job to avoid
    additional queries during serialization.
    """

    queryset           = Invoice.objects.select_related('job__customer')
    serializer_class   = InvoiceSerializer
    permission_classes = [IsAdministrator]


# ---------------------------------------------------------------------------
# Client Request ViewSet
# ---------------------------------------------------------------------------

class ClientRequestViewSet(viewsets.ReadOnlyModelViewSet):
    """
    UC8 -- View inbound job requests received via the webhook (BR2).

    Access: Administrator only.
    Read-only. Records are created exclusively by the webhook_intake view.
    """

    queryset           = ClientRequest.objects.all()
    serializer_class   = ClientRequestSerializer
    permission_classes = [IsAdministrator]


# ---------------------------------------------------------------------------
# AI Response Suggestion ViewSet
# ---------------------------------------------------------------------------

class AIResponseSuggestionViewSet(viewsets.ReadOnlyModelViewSet):
    """
    UC -- Review and approve AI-generated response suggestions (BR4, BR5).

    Access: Administrator and Technician.
    Suggestions are created automatically by the webhook pipeline.
    Administrators and Technicians may approve, reject, or send suggestions.
    No suggestion may be sent without prior explicit approval (BR5).
    """

    queryset           = AIResponseSuggestion.objects.select_related('client_request')
    serializer_class   = AIResponseSuggestionSerializer
    permission_classes = [IsAdminOrTechnician]

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        """
        Approve a pending AI suggestion (BR5 -- human approval gate).

        The reviewer must supply a final_response and their role.
        Only suggestions in PENDING status may be approved.
        """
        suggestion = self.get_object()

        if suggestion.approval_status != AIResponseSuggestion.ApprovalStatus.PENDING:
            return Response(
                {'error': f"Suggestion is already '{suggestion.approval_status}', not pending."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = ApproveResponseSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        suggestion.final_response      = serializer.validated_data['final_response']
        suggestion.reviewed_by_role    = serializer.validated_data['reviewed_by_role']
        suggestion.reviewed_by_user_id = request.user.pk
        suggestion.reviewed_at         = timezone.now()
        suggestion.approval_status     = AIResponseSuggestion.ApprovalStatus.APPROVED
        suggestion.save()

        return Response(AIResponseSuggestionSerializer(suggestion).data)

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        """
        Reject a pending AI suggestion.

        Records the reviewer and timestamp. The suggestion remains in the
        system for audit purposes but will not be sent.
        """
        suggestion = self.get_object()

        suggestion.approval_status     = AIResponseSuggestion.ApprovalStatus.REJECTED
        suggestion.reviewed_by_user_id = request.user.pk
        suggestion.reviewed_at         = timezone.now()
        suggestion.save()

        return Response(AIResponseSuggestionSerializer(suggestion).data)

    @action(detail=True, methods=['post'])
    def send(self, request, pk=None):
        """
        Dispatch an approved suggestion to the client via email (BR5).

        This action is blocked if the suggestion has not been approved.
        On success the suggestion status transitions to SENT.
        """
        suggestion = self.get_object()

        if not suggestion.is_sendable:
            return Response(
                {'error': 'Only approved suggestions may be sent.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from django.core.mail import send_mail
        from django.conf import settings as django_settings

        client_request = suggestion.client_request

        send_mail(
            subject=f"Re: {client_request.subject or 'Your Enquiry'}",
            message=suggestion.final_response,
            from_email=getattr(django_settings, 'DEFAULT_FROM_EMAIL', 'noreply@example.com'),
            recipient_list=[client_request.contact_email],
            fail_silently=False,
        )

        suggestion.approval_status = AIResponseSuggestion.ApprovalStatus.SENT
        suggestion.sent_at         = timezone.now()
        suggestion.save()

        return Response(AIResponseSuggestionSerializer(suggestion).data)


# ---------------------------------------------------------------------------
# Webhook Intake
# ---------------------------------------------------------------------------

@api_view(['POST'])
@permission_classes([])
def webhook_intake(request):
    """
    UC8 -- Receive and acknowledge an inbound job request from the external website.

    No authentication is required. The endpoint is intentionally public
    to allow external systems to POST without credentials.

    Pipeline on successful receipt:
        1. Validate the inbound payload against WebhookInboundSerializer.
        2. Persist the ClientRequest record with status RECEIVED.
        3. Send an automatic acknowledgement email to the client (BR3).
        4. Attempt AI suggestion generation; log and continue on failure (BR4).
    """
    serializer = WebhookInboundSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    data = serializer.validated_data

    client_request = ClientRequest.objects.create(
        contact_name=data['name'],
        contact_email=data['email'],
        contact_phone=data.get('phone', ''),
        subject=data.get('subject', ''),
        message=data['message'],
        source_ip=_get_client_ip(request),
        raw_payload=request.data,
        status=ClientRequest.Status.RECEIVED,
    )

    # Send the automatic confirmation email (BR3).
    send_confirmation(client_request)

    # Generate an AI-suggested response for admin/technician review (BR4).
    # A failure here must not prevent the webhook from returning a success response.
    try:
        generate_ai_suggestion(client_request)
    except Exception as exc:
        logger.error(
            "AI suggestion generation failed for ClientRequest #%s: %s",
            client_request.pk,
            exc,
        )

    return Response({
        'message':    'Request received. A confirmation has been sent to your email.',
        'request_id': client_request.pk,
    }, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# Authentication Views
# ---------------------------------------------------------------------------

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def logout(request):
    """
    Invalidate the requesting user's authentication token.

    Deleting the token immediately revokes all API access for that session.
    The client is responsible for discarding the token on their end.
    """
    request.user.auth_token.delete()
    return Response({'message': 'Successfully logged out.'}, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def me(request):
    """
    Return the authenticated user's identity and role.

    Used by the frontend immediately after login to determine which
    dashboard and navigation options to display.
    """
    user    = request.user
    profile = getattr(user, 'profile', None)

    return Response({
        'id':       user.pk,
        'username': user.username,
        'email':    user.email,
        'role':     profile.role if profile else None,
    })


# ---------------------------------------------------------------------------
# Internal Helpers
# ---------------------------------------------------------------------------

def _get_client_ip(request):
    """
    Extract the originating IP address from the request.

    Checks the X-Forwarded-For header first to handle requests routed
    through a proxy or load balancer. Falls back to REMOTE_ADDR.
    """
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')