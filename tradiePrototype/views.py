from django.shortcuts import render

"""
tradiePrototype/views.py – All views in one place.
"""

import logging
from datetime import date

from django.utils import timezone
from django.contrib.auth.models import User
from rest_framework import viewsets, status
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
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


# ── Customers ─────────────────────────────────────────────────────────────────

class CustomerViewSet(viewsets.ModelViewSet):
    """US1.1 – CRUD for customers. Admin only."""
    queryset           = Customer.objects.all()
    serializer_class   = CustomerSerializer
    permission_classes = [IsAdministrator]


# ── Technicians ───────────────────────────────────────────────────────────────

class TechnicianViewSet(viewsets.ModelViewSet):
    """US1.2 – CRUD for technicians. Admin only."""
    queryset           = Technician.objects.filter(is_active=True)
    serializer_class   = TechnicianSerializer
    permission_classes = [IsAdministrator]


# ── Jobs ──────────────────────────────────────────────────────────────────────

class JobViewSet(viewsets.ModelViewSet):
    """US1.3 – Jobs. Admin full access. Customer sees own jobs only."""
    queryset           = Job.objects.select_related('customer', 'technician').prefetch_related('parts')
    permission_classes = [IsAdministrator | IsCustomer]

    def get_serializer_class(self):
        return JobCreateSerializer if self.action == 'create' else JobSerializer

    def get_queryset(self):
        user    = self.request.user
        profile = getattr(user, 'profile', None)
        if profile and profile.is_customer:
            return Job.objects.filter(customer__email=user.email)
        return Job.objects.select_related('customer', 'technician').prefetch_related('parts')

    @action(detail=True, methods=['post'])
    def book(self, request, pk=None):
        """US7.1/7.2 – Book a date/time. Auto-schedules travel blocks if technician assigned (BR6)."""
        job             = self.get_object()
        scheduled_start = request.data.get('scheduled_start')
        scheduled_end   = request.data.get('scheduled_end')

        if not scheduled_start or not scheduled_end:
            return Response({'error': 'scheduled_start and scheduled_end are required.'},
                            status=status.HTTP_400_BAD_REQUEST)

        job.scheduled_start = scheduled_start
        job.scheduled_end   = scheduled_end
        job.status          = Job.Status.BOOKED
        job.save(update_fields=['scheduled_start', 'scheduled_end', 'status'])

        blocks = []
        if job.technician:
            try:
                blocks = schedule_job(job)
            except ValueError as exc:
                return Response({'error': str(exc)}, status=status.HTTP_409_CONFLICT)

        return Response({
            'job':             JobSerializer(job).data,
            'schedule_blocks': ScheduleBlockSerializer(blocks, many=True).data,
        })

    @action(detail=True, methods=['post'], url_path='assign-technician')
    def assign_technician(self, request, pk=None):
        """US6.2 – Assign a technician and auto-calculate travel time."""
        job           = self.get_object()
        technician_id = request.data.get('technician_id')

        if not technician_id:
            return Response({'error': 'technician_id is required.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            technician = Technician.objects.get(pk=technician_id)
        except Technician.DoesNotExist:
            return Response({'error': 'Technician not found.'}, status=status.HTTP_404_NOT_FOUND)

        job.technician = technician
        job.save(update_fields=['technician'])

        blocks = []
        if job.scheduled_start and job.scheduled_end:
            ScheduleBlock.objects.filter(job=job).delete()
            try:
                blocks = schedule_job(job)
            except ValueError as exc:
                return Response({'error': str(exc)}, status=status.HTTP_409_CONFLICT)

        return Response({
            'job':             JobSerializer(job).data,
            'schedule_blocks': ScheduleBlockSerializer(blocks, many=True).data,
        })

    @action(detail=True, methods=['post'])
    def complete(self, request, pk=None):
        """US10.1 – Mark job complete and auto-generate invoice (BR10)."""
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


# ── Job Parts ─────────────────────────────────────────────────────────────────

class JobPartViewSet(viewsets.ModelViewSet):
    """US1.4 – CRUD for job parts. Admin only."""
    queryset           = JobPart.objects.all()
    serializer_class   = JobPartSerializer
    permission_classes = [IsAdministrator]

    def get_queryset(self):
        job_id = self.request.query_params.get('job')
        if job_id:
            return JobPart.objects.filter(job_id=job_id)
        return super().get_queryset()


# ── Schedule ──────────────────────────────────────────────────────────────────

class ScheduleBlockViewSet(viewsets.ReadOnlyModelViewSet):
    """US6.1/6.3 – Admin sees all. Technician sees own only."""
    queryset           = ScheduleBlock.objects.select_related('technician', 'job')
    serializer_class   = ScheduleBlockSerializer
    permission_classes = [IsAdminOrTechnician]

    def get_queryset(self):
        user    = self.request.user
        profile = getattr(user, 'profile', None)
        if profile and profile.is_technician:
            return ScheduleBlock.objects.filter(technician__email=user.email)
        return ScheduleBlock.objects.select_related('technician', 'job')


# ── Invoices ──────────────────────────────────────────────────────────────────

class InvoiceViewSet(viewsets.ModelViewSet):
    """US10.1/10.2 – View and manage invoices. Admin only."""
    queryset           = Invoice.objects.select_related('job__customer')
    serializer_class   = InvoiceSerializer
    permission_classes = [IsAdministrator]


# ── Client Requests ───────────────────────────────────────────────────────────

class ClientRequestViewSet(viewsets.ReadOnlyModelViewSet):
    """View inbound client requests. Admin only."""
    queryset           = ClientRequest.objects.all()
    serializer_class   = ClientRequestSerializer
    permission_classes = [IsAdministrator]


# ── AI Suggestions ────────────────────────────────────────────────────────────

class AIResponseSuggestionViewSet(viewsets.ReadOnlyModelViewSet):
    """US4.1/4.2 – Admin and Technician can approve/reject."""
    queryset           = AIResponseSuggestion.objects.select_related('client_request')
    serializer_class   = AIResponseSuggestionSerializer
    permission_classes = [IsAdminOrTechnician]

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        """US5.1/5.2 – Approve a suggestion (admin or technician)."""
        suggestion = self.get_object()

        if suggestion.approval_status != AIResponseSuggestion.ApprovalStatus.PENDING:
            return Response({'error': f"Already '{suggestion.approval_status}', not pending."},
                            status=status.HTTP_400_BAD_REQUEST)

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
        """US5.1/5.2 – Reject a suggestion."""
        suggestion = self.get_object()
        suggestion.approval_status     = AIResponseSuggestion.ApprovalStatus.REJECTED
        suggestion.reviewed_by_user_id = request.user.pk
        suggestion.reviewed_at         = timezone.now()
        suggestion.save()
        return Response(AIResponseSuggestionSerializer(suggestion).data)

    @action(detail=True, methods=['post'])
    def send(self, request, pk=None):
        """BR5 – Dispatch an APPROVED suggestion. Blocked if not approved."""
        suggestion = self.get_object()

        if not suggestion.is_sendable:
            return Response({'error': 'Only approved suggestions can be sent.'},
                            status=status.HTTP_400_BAD_REQUEST)

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


# ── Webhook ───────────────────────────────────────────────────────────────────

@api_view(['POST'])
@permission_classes([AllowAny])
def webhook_intake(request):
    """
    US2.2 – Receive inbound job request from external website (no auth required).
    US3.1 – Auto-sends confirmation email on receipt.
    US4.1 – Generates AI suggestion stored as PENDING.
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

    send_confirmation(client_request)

    try:
        generate_ai_suggestion(client_request)
    except Exception as exc:
        logger.error("AI suggestion failed for ClientRequest #%s: %s", client_request.pk, exc)

    return Response({
        'message':    'Request received. A confirmation has been sent to your email.',
        'request_id': client_request.pk,
    }, status=status.HTTP_201_CREATED)


# ── Auth Views ────────────────────────────────────────────────────────────────

@api_view(['POST'])
@permission_classes([AllowAny])
def register_customer(request):
    """Public self-registration for customers."""
    username = request.data.get('username')
    password = request.data.get('password')
    email    = request.data.get('email', '')
    phone    = request.data.get('phone', '')
    address  = request.data.get('address', '')

    # ── Validations ───────────────────────────────────────────────
    if not username or not password:
        return Response({'error': 'Username and password are required.'},
                        status=status.HTTP_400_BAD_REQUEST)

    if len(password) < 8:
        return Response({'error': 'Password must be at least 8 characters.'},
                        status=status.HTTP_400_BAD_REQUEST)

    if not any(c.isupper() for c in password):
        return Response({'error': 'Password must contain at least one uppercase letter.'},
                        status=status.HTTP_400_BAD_REQUEST)

    if not any(c.isdigit() for c in password):
        return Response({'error': 'Password must contain at least one number.'},
                        status=status.HTTP_400_BAD_REQUEST)

    if User.objects.filter(username=username).exists():
        return Response({'error': 'Username already exists.'},
                        status=status.HTTP_400_BAD_REQUEST)

    if email and User.objects.filter(email=email).exists():
        return Response({'error': 'Email already exists.'},
                        status=status.HTTP_400_BAD_REQUEST)
    # ──────────────────────────────────────────────────────────────

    user = User.objects.create_user(username=username, password=password, email=email)
    UserProfile.objects.create(
        user=user,
        role=UserProfile.Role.CUSTOMER,
        phone=phone,
        address=address,
    )

    token, _ = Token.objects.get_or_create(user=user)

    return Response({
        'token':    token.key,
        'username': user.username,
        'email':    user.email,
        'role':     UserProfile.Role.CUSTOMER,
    }, status=status.HTTP_201_CREATED)


@api_view(['POST'])
@permission_classes([AllowAny])
def register_technician(request):
    """Public self-registration for technicians."""
    username = request.data.get('username')
    password = request.data.get('password')
    email    = request.data.get('email', '')
    phone    = request.data.get('phone', '')
    address  = request.data.get('address', '')

    # ── Validations ───────────────────────────────────────────────
    if not username or not password:
        return Response({'error': 'Username and password are required.'},
                        status=status.HTTP_400_BAD_REQUEST)

    if len(password) < 8:
        return Response({'error': 'Password must be at least 8 characters.'},
                        status=status.HTTP_400_BAD_REQUEST)

    if not any(c.isupper() for c in password):
        return Response({'error': 'Password must contain at least one uppercase letter.'},
                        status=status.HTTP_400_BAD_REQUEST)

    if not any(c.isdigit() for c in password):
        return Response({'error': 'Password must contain at least one number.'},
                        status=status.HTTP_400_BAD_REQUEST)

    if User.objects.filter(username=username).exists():
        return Response({'error': 'Username already exists.'},
                        status=status.HTTP_400_BAD_REQUEST)

    if email and User.objects.filter(email=email).exists():
        return Response({'error': 'Email already exists.'},
                        status=status.HTTP_400_BAD_REQUEST)
    # ──────────────────────────────────────────────────────────────

    user = User.objects.create_user(username=username, password=password, email=email)
    UserProfile.objects.create(
        user=user,
        role=UserProfile.Role.TECHNICIAN,
        phone=phone,
        address=address,
    )

    token, _ = Token.objects.get_or_create(user=user)

    return Response({
        'token':    token.key,
        'username': user.username,
        'email':    user.email,
        'role':     UserProfile.Role.TECHNICIAN,
    }, status=status.HTTP_201_CREATED)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def logout(request):
    """Delete the user's token — effectively logging them out."""
    request.user.auth_token.delete()
    return Response({'message': 'Successfully logged out.'}, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def me(request):
    """Return the currently logged-in user's details and role."""
    user    = request.user
    profile = getattr(user, 'profile', None)
    return Response({
        'id':       user.pk,
        'username': user.username,
        'email':    user.email,
        'role':     profile.role if profile else None,
    })


# ── Helper ────────────────────────────────────────────────────────────────────

def _get_client_ip(request):
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')
