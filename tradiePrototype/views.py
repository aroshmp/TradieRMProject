"""
tradiePrototype/views.py

All API views for TradieRM.

Use case coverage:
    UC1  -- ClientRequestViewSet.process action
    UC2  -- CustomerViewSet.create_with_job action
    UC3  -- BookingViewSet.create (admin-triggered)
    UC4  -- BookingViewSet.send_request action + booking_token_submit view
    UC5  -- InventoryViewSet
    UC6  -- TechnicianViewSet.create (provisions User + sends email)
    UC7  -- BookingViewSet.allocate action (distance calc + emails)
    UC8  -- webhook_intake view
    UC9  -- JobViewSet.update_status action

Role access summary:
    Administrator  -- full access to all resources and all actions
    Technician     -- own schedule, own jobs, job status update, AI suggestions
    Customer       -- read own jobs; booking form submission via token (UC4)
"""

import logging
from datetime import timedelta

from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.core import signing
from django.conf import settings as django_settings
from django.utils import timezone

from rest_framework import viewsets, status
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.authtoken.models import Token

from .models import (
    Customer, Technician, Job, Inventory, JobInventory,
    Booking, ScheduleBlock, Invoice,
    ClientRequest, AIResponseSuggestion, UserProfile,
)
from .serializers import (
    CustomerSerializer,
    TechnicianSerializer, TechnicianCreateSerializer,
    InventorySerializer,
    JobInventorySerializer,
    JobSerializer, JobCreateSerializer, JobStatusUpdateSerializer,
    BookingSerializer, BookingCreateSerializer, BookingTokenSubmitSerializer,
    ScheduleBlockSerializer,
    InvoiceSerializer,
    ClientRequestSerializer, ClientRequestProcessSerializer,
    WebhookInboundSerializer,
    AIResponseSuggestionSerializer, ApproveResponseSerializer,
)
from .services.distance_service import get_road_distance_km
from .services.invoice_generator import generate_invoice
from .services.confirmation import send_confirmation
from .services.ai_responder import generate_ai_suggestion
from .permissions import IsAdministrator, IsTechnician, IsCustomer, IsAdminOrTechnician

logger = logging.getLogger(__name__)

# Token expiry window for the customer-facing booking form link (UC4).
BOOKING_TOKEN_EXPIRY_HOURS = 48


# ---------------------------------------------------------------------------
# Customer ViewSet
# ---------------------------------------------------------------------------

class CustomerViewSet(viewsets.ModelViewSet):
    """
    UC2 -- Full CRUD for Customer records.

    Access: Administrator only.
    The create_with_job action handles the combined customer + job
    creation flow described in UC2.
    """

    serializer_class   = CustomerSerializer
    permission_classes = [IsAdministrator]

    @action(detail=False, methods=['post'], url_path='create-with-job')
    def create_with_job(self, request):
        """
        UC2 -- Create a customer record and a job record in a single request.

        Expected payload:
            first_name, last_name, phone, email  -- customer fields
            subject, client_message              -- job fields

        Both records are created together. If either validation fails,
        neither record is persisted.
        """
        # Validate customer fields using the customer serializer.
        customer_data = {
            'first_name': request.data.get('first_name', '').strip(),
            'last_name':  request.data.get('last_name',  '').strip(),
            'email':      request.data.get('email',      '').strip(),
            'phone':      request.data.get('phone',      '').strip(),
        }
        customer_serializer = CustomerSerializer(data=customer_data)
        if not customer_serializer.is_valid():
            return Response(customer_serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        # Validate job fields manually before persisting either record.
        subject        = request.data.get('subject', '').strip()
        client_message = request.data.get('client_message', '').strip()

        errors = {}
        if not subject:
            errors['subject'] = 'Subject is required.'
        if not client_message:
            errors['client_message'] = 'Client message is required.'
        if errors:
            return Response(errors, status=status.HTTP_400_BAD_REQUEST)

        # Persist the customer record (UC2, step 7).
        customer = customer_serializer.save()

        # Persist the job record with status Pending (UC2, step 8).
        job = Job.objects.create(
            customer=customer,
            subject=subject,
            client_message=client_message,
            status=Job.Status.PENDING,
            source=Job.Source.MANUAL,
        )

        return Response({
            'customer': CustomerSerializer(customer).data,
            'job':      JobSerializer(job).data,
        }, status=status.HTTP_201_CREATED)

    def get_queryset(self):
        """
        UC5 -- Return only active customer records for standard list and detail operations.
        Inactive records (soft-deleted via UC5) are retained in the database as a log
        but excluded from all API responses.
        """
        return Customer.objects.filter(is_active=True)

    def destroy(self, request, *args, **kwargs):
        """
        UC5, Alternate Course Step 5b -- Soft-delete a customer record.

        The record is NOT physically removed from the database. Instead, is_active
        is set to False to mark the record as Inactive, preserving it as an audit log
        per UC5 Step 5b.5. A Confirmed booking constraint does not apply to customers,
        but the record is retained regardless of associated jobs or bookings.
        """
        customer = self.get_object()
        customer.is_active = False
        customer.save()

        logger.info(
            "Customer #%s (%s %s) marked as Inactive by administrator '%s'.",
            customer.pk,
            customer.first_name,
            customer.last_name,
            request.user.username,
        )

        return Response(
            {'detail': 'Customer record has been marked as Inactive.'},
            status=status.HTTP_200_OK,
        )
# ---------------------------------------------------------------------------
# Technician ViewSet
# ---------------------------------------------------------------------------

class TechnicianViewSet(viewsets.ModelViewSet):
    """
    UC6 -- Full CRUD for Technician records.

    Access: Administrator only.
    The create action provisions a Django User login account with a
    temporary password equal to the technician's phone number,
    and sends a confirmation email to the technician.
    """

    queryset           = Technician.objects.filter(is_active=True)
    permission_classes = [IsAdministrator]

    def get_serializer_class(self):
        """Use the creation serializer for POST; the read serializer for all other actions."""
        if self.action == 'create':
            return TechnicianCreateSerializer
        return TechnicianSerializer

    def create(self, request, *args, **kwargs):
        """
        UC6 -- Create a technician record and provision their login account.

        Steps:
            1. Validate all technician profile fields and the username.
            2. Create the Technician record.
            3. Create a Django User with the supplied username and a
               temporary password set to the technician's phone number (UC6, step 8).
            4. Create a UserProfile linking the User to the Technician role.
            5. Create an authentication token for the new user.
            6. Send a confirmation email with login details (UC6, step 9).
        """
        serializer = TechnicianCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        validated = serializer.validated_data
        username  = validated.pop('username')
        phone     = validated.get('phone', '')

        # Create the technician profile record (UC6, step 7).
        technician = Technician.objects.create(**validated)

        # Temporary password is set to the technician's phone number (UC6, step 8).
        # A fallback is provided in case phone is blank.
        temp_password = phone if phone else 'ChangeMe123!'

        user = User.objects.create_user(
            username=username,
            password=temp_password,
            email=technician.email,
            first_name=technician.first_name,
            last_name=technician.last_name,
        )
        UserProfile.objects.create(user=user, role=UserProfile.Role.TECHNICIAN)
        Token.objects.get_or_create(user=user)

        # Send the confirmation email (UC6, step 9).
        _send_technician_welcome_email(technician, username, temp_password)

        return Response(TechnicianSerializer(technician).data, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# Inventory ViewSet
# ---------------------------------------------------------------------------

class InventoryViewSet(viewsets.ModelViewSet):
    """
    UC5 -- Full CRUD for Inventory records.

    Access: Administrator only.
    The status field is managed automatically by the model based on quantity.
    Uniqueness of the inventory name is enforced by the serializer.
    """

    queryset           = Inventory.objects.all()
    serializer_class   = InventorySerializer
    permission_classes = [IsAdministrator]


# ---------------------------------------------------------------------------
# Job Inventory ViewSet
# ---------------------------------------------------------------------------

class JobInventoryViewSet(viewsets.ModelViewSet):
    """
    Manage the assignment of Inventory items to a specific Job.

    Access: Administrator only.
    Supports filtering by job via the ?job=<id> query parameter.
    """

    queryset           = JobInventory.objects.select_related('job', 'inventory')
    serializer_class   = JobInventorySerializer
    permission_classes = [IsAdministrator]

    def get_queryset(self):
        """Filter by job ID if the 'job' query parameter is provided."""
        job_id = self.request.query_params.get('job')
        if job_id:
            return JobInventory.objects.filter(
                job_id=job_id
            ).select_related('inventory')
        return super().get_queryset()


# ---------------------------------------------------------------------------
# Job ViewSet
# ---------------------------------------------------------------------------

class JobViewSet(viewsets.ModelViewSet):
    """
    UC1, UC2, UC9 -- Full CRUD for Job records.

    Access:
        Administrator -- full access to all jobs.
        Technician    -- read access to own allocated jobs; can update status.
        Customer      -- read access to own jobs only (matched by email).
    """

    queryset           = Job.objects.select_related('customer', 'technician')
    permission_classes = [IsAdministrator | IsTechnician | IsCustomer]

    def get_serializer_class(self):
        """Use the create serializer for POST; the full serializer for all other actions."""
        if self.action == 'create':
            return JobCreateSerializer
        return JobSerializer

    def get_queryset(self):
        """
        Scope the queryset based on the requesting user's role.
        Administrators receive all jobs.
        Technicians receive only jobs assigned to them.
        Customers receive only their own jobs matched by email.
        """
        user    = self.request.user
        profile = getattr(user, 'profile', None)

        if profile and profile.is_technician:
            return Job.objects.filter(
                technician__email=user.email
            ).select_related('customer', 'technician')

        if profile and profile.is_customer:
            return Job.objects.filter(
                customer__email=user.email
            ).select_related('customer', 'technician')

        return Job.objects.select_related('customer', 'technician')

    def create(self, request, *args, **kwargs):
        """
        UC2 -- Create a standalone job record.
        Status is always forced to Pending regardless of submitted value.
        """
        serializer = JobCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        job = serializer.save(status=Job.Status.PENDING)
        return Response(JobSerializer(job).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='update-status')
    def update_status(self, request, pk=None):
        """
        UC9 -- Update the status of an existing job.

        Valid transitions:
            -> In Progress  (no feedback required)
            -> Completed    (no feedback required; invoice auto-generated)
            -> Suspended    (admin_feedback or technician_feedback required)
            -> Cancelled    (admin_feedback or technician_feedback required)

        The user's role is derived from their UserProfile and injected into
        the serializer to determine which feedback field is required.
        """
        job     = self.get_object()
        profile = getattr(request.user, 'profile', None)
        role    = profile.role if profile else ''

        serializer = JobStatusUpdateSerializer(
            data={**request.data, 'role': role}
        )
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        validated  = serializer.validated_data
        new_status = validated['new_status']

        job.status = new_status

        if validated.get('admin_feedback'):
            job.admin_feedback = validated['admin_feedback']
        if validated.get('technician_feedback'):
            job.technician_feedback = validated['technician_feedback']

        job.save()

        # UC9, step 9 -- auto-generate invoice when the job is completed.
        invoice_data = None
        if new_status == Job.Status.COMPLETED:
            try:
                invoice = generate_invoice(job)
                invoice_data = InvoiceSerializer(invoice).data
            except Exception as exc:
                logger.error(
                    "Invoice generation failed for Job #%s: %s", job.pk, exc
                )

        response_data = {'job': JobSerializer(job).data}
        if invoice_data:
            response_data['invoice'] = invoice_data

        return Response(response_data)


# ---------------------------------------------------------------------------
# Booking ViewSet
# ---------------------------------------------------------------------------

class BookingViewSet(viewsets.ModelViewSet):
    """
    UC3, UC4, UC7 -- Manage Booking records.

    Access: Administrator only for all standard CRUD and actions.
    The booking_token_submit endpoint is separate and unauthenticated (UC4).
    """

    queryset           = Booking.objects.select_related('job', 'customer', 'technician')
    permission_classes = [IsAdministrator]

    def get_serializer_class(self):
        """Use the create serializer for POST; the read serializer for all other actions."""
        if self.action == 'create':
            return BookingCreateSerializer
        return BookingSerializer

    def get_queryset(self):
        """
        Return bookings ordered with Pending first, then by creation time
        to support first-come-first-served processing in UC7.
        """
        return Booking.objects.select_related(
            'job', 'customer', 'technician'
        ).order_by('created_at')

    def create(self, request, *args, **kwargs):
        """
        UC3 -- Create a new Booking record with status Pending.

        The administrator supplies the physical address, date, and time
        for a job that already exists in the system.
        """
        serializer = BookingCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        booking = serializer.save(status=Booking.Status.PENDING)
        return Response(BookingSerializer(booking).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='send-request')
    def send_request(self, request, pk=None):
        """
        UC4 -- Email the customer a signed booking form link.

        A time-limited signed token is generated using Django's built-in
        signing framework (TimestampSigner). The token encodes the booking ID
        and is stored on the Booking record for validation at submission time.

        Rejects the action if no email address is recorded for the customer.
        """
        booking  = self.get_object()
        customer = booking.customer

        if not customer.email:
            return Response(
                {'error': 'No email address is recorded for this customer.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Generate a signed token encoding the booking ID.
        token  = signing.dumps({'booking_id': booking.pk}, salt='booking-request')
        expiry = timezone.now() + timedelta(hours=BOOKING_TOKEN_EXPIRY_HOURS)

        booking.booking_token    = token
        booking.token_expires_at = expiry
        booking.save(update_fields=['booking_token', 'token_expires_at'])

        base_url     = getattr(django_settings, 'SITE_BASE_URL', 'http://localhost:3000')
        booking_link = f"{base_url}/booking/submit?token={token}"

        _send_booking_request_email(customer, booking, booking_link)

        return Response({'message': 'Booking request email sent successfully.'})

    @action(detail=True, methods=['post'], url_path='allocate')
    def allocate(self, request, pk=None):
        """
        UC7 -- Allocate a technician to a Pending booking.

        Steps performed:
            1. Confirm the booking is in Pending status.
            2. Retrieve and validate the supplied technician.
            3. Calculate road distance via OpenRouteService.
            4. Update booking: status -> Confirmed, assign technician, store distance.
            5. Update job: status -> Allocated, assign technician.
            6. Send confirmation emails to both customer and technician.
        """
        booking = self.get_object()

        if booking.status != Booking.Status.PENDING:
            return Response(
                {'error': f"Booking is '{booking.status}', not Pending. Cannot allocate."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        technician_id = request.data.get('technician_id')
        if not technician_id:
            return Response(
                {'error': 'technician_id is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            technician = Technician.objects.get(pk=technician_id, is_active=True)
        except Technician.DoesNotExist:
            return Response(
                {'error': 'Technician not found or is inactive.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Calculate road distance between technician home and booking address (UC7, step 7).
        distance_km = None
        if technician.home_address and booking.physical_address:
            distance_km = get_road_distance_km(
                technician.home_address,
                booking.physical_address,
            )
            if distance_km is None:
                logger.warning(
                    "Distance calculation returned None for Booking #%s. "
                    "Proceeding with null distance value.",
                    booking.pk,
                )

        # Confirm the booking (UC7, step 10).
        booking.technician = technician
        booking.status     = Booking.Status.CONFIRMED
        booking.distance   = distance_km
        booking.save()

        # Update the job (UC7, step 11).
        job            = booking.job
        job.technician = technician
        job.status     = Job.Status.ALLOCATED
        job.save()

        # Send confirmation emails to both parties (UC7, steps 12 and 13).
        _send_allocation_email_to_customer(booking)
        _send_allocation_email_to_technician(booking)

        return Response({
            'booking':     BookingSerializer(booking).data,
            'job':         JobSerializer(job).data,
            'distance_km': distance_km,
        })

    def destroy(self, request, *args, **kwargs):
        """
        UC6, Alternate Course Step 5a -- Soft-delete a booking record.

        A booking with a status of Confirmed cannot be deleted (UC6, Step 5a.3a.1).
        For Pending bookings, the record is NOT physically removed. Instead, the
        status is set to Inactive to preserve it as an audit log (UC6, Step 5a.5).
        """
        booking = self.get_object()

        if booking.status == Booking.Status.CONFIRMED:
            return Response(
                {'error': 'A Confirmed booking cannot be deleted.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        booking.status = Booking.Status.INACTIVE
        booking.save()

        logger.info(
            "Booking #%s marked as Inactive by administrator '%s'.",
            booking.pk,
            request.user.username,
        )

        return Response(
            {'detail': 'Booking record has been marked as Inactive.'},
            status=status.HTTP_200_OK,
        )


# ---------------------------------------------------------------------------
# Schedule Block ViewSet
# ---------------------------------------------------------------------------

class ScheduleBlockViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Read-only view of technician schedule blocks.

    Access:
        Administrator -- all blocks for all technicians.
        Technician    -- own blocks only (matched by email).
    """

    queryset           = ScheduleBlock.objects.select_related('technician', 'job', 'booking')
    serializer_class   = ScheduleBlockSerializer
    permission_classes = [IsAdminOrTechnician]

    def get_queryset(self):
        """Restrict to the requesting technician's own blocks if applicable."""
        user    = self.request.user
        profile = getattr(user, 'profile', None)

        if profile and profile.is_technician:
            return ScheduleBlock.objects.filter(
                technician__email=user.email
            ).select_related('technician', 'job')

        return ScheduleBlock.objects.select_related('technician', 'job')


# ---------------------------------------------------------------------------
# Invoice ViewSet
# ---------------------------------------------------------------------------

class InvoiceViewSet(viewsets.ModelViewSet):
    """
    UC9 -- View and manage Invoice records.

    Access: Administrator only.
    Invoices are generated automatically when a job status is set to Completed.
    """

    queryset           = Invoice.objects.select_related('job__customer', 'technician')
    serializer_class   = InvoiceSerializer
    permission_classes = [IsAdministrator]


# ---------------------------------------------------------------------------
# Client Request ViewSet
# ---------------------------------------------------------------------------

class ClientRequestViewSet(viewsets.ReadOnlyModelViewSet):
    """
    UC8, UC1 -- View inbound job requests and process them into customer + job records.

    Access: Administrator only.
    Records are created exclusively by the webhook_intake view (UC8).
    The process action converts an Unprocessed request into a Customer + Job (UC1).
    """

    queryset           = ClientRequest.objects.all()
    serializer_class   = ClientRequestSerializer
    permission_classes = [IsAdministrator]

    def get_queryset(self):
        """Return Unprocessed requests first to support the UC1 workflow."""
        return ClientRequest.objects.order_by('status', 'created_at')

    @action(detail=True, methods=['post'], url_path='process')
    def process(self, request, pk=None):
        """
        UC1 -- Convert an Unprocessed ClientRequest into a Customer and Job record.

        Validates that all required fields exist on the ClientRequest, then
        creates both records and marks the request as Processed.

        The customer name is split on the first space. If only one name token
        is present it is used as the first name with last name left blank.
        """
        client_request = self.get_object()

        if client_request.status != ClientRequest.Status.UNPROCESSED:
            return Response(
                {'error': 'This request has already been processed.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Validate required fields on the ClientRequest (UC1, step 6).
        process_serializer = ClientRequestProcessSerializer(
            data={}, context={'client_request': client_request}
        )
        if not process_serializer.is_valid():
            return Response(process_serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        # Parse first and last name from the contact_name field.
        name_parts = client_request.contact_name.strip().split(' ', 1)
        first_name = name_parts[0]
        last_name  = name_parts[1] if len(name_parts) > 1 else ''

        # Create or retrieve the customer record (UC1, step 7).
        customer, _ = Customer.objects.get_or_create(
            email=client_request.contact_email,
            defaults={
                'first_name': first_name,
                'last_name':  last_name,
                'phone':      client_request.contact_phone,
            },
        )

        # Create the job record with status Pending (UC1, step 8).
        job = Job.objects.create(
            customer=customer,
            subject=client_request.subject,
            client_message=client_request.message,
            status=Job.Status.PENDING,
            source=Job.Source.WEBHOOK,
            client_request=client_request,
        )

        # Mark the request as Processed (UC1, step 9).
        client_request.status = ClientRequest.Status.PROCESSED
        client_request.save(update_fields=['status', 'updated_at'])

        return Response({
            'customer':       CustomerSerializer(customer).data,
            'job':            JobSerializer(job).data,
            'client_request': ClientRequestSerializer(client_request).data,
        }, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# AI Response Suggestion ViewSet
# ---------------------------------------------------------------------------

class AIResponseSuggestionViewSet(viewsets.ReadOnlyModelViewSet):
    """
    BR4, BR5 -- Review and action AI-generated response suggestions.

    Access: Administrator and Technician.
    Suggestions are created automatically when a ClientRequest is received.
    No suggestion may be sent without prior explicit approval (BR5).
    """

    queryset           = AIResponseSuggestion.objects.select_related('client_request')
    serializer_class   = AIResponseSuggestionSerializer
    permission_classes = [IsAdminOrTechnician]

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        """
        BR5 -- Approve a pending AI suggestion.

        The reviewer supplies their edited final response and role.
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
        BR5 -- Reject a pending AI suggestion.

        The record is retained for audit purposes but will not be sent.
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
        BR5 -- Dispatch an approved suggestion to the client via email.

        Blocked if the suggestion has not been explicitly approved.
        On success the suggestion status transitions to SENT.
        """
        suggestion = self.get_object()

        if not suggestion.is_sendable:
            return Response(
                {'error': 'Only approved suggestions may be sent.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

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

    No authentication required. The endpoint is intentionally public.

    Pipeline:
        1. Validate the payload against WebhookInboundSerializer.
        2. Persist the ClientRequest with status UNPROCESSED (UC8, step 4).
        3. Send an acknowledgement email to the client (UC8, step 5 / BR3).
        4. Send a notification email to the administrator (UC8, step 6).
        5. Attempt AI suggestion generation; log and continue on failure (BR4).
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
        status=ClientRequest.Status.UNPROCESSED,
    )

    # Send acknowledgement to the client (BR3, UC8 step 5).
    send_confirmation(client_request)

    # Notify the administrator of the new request (UC8, step 6).
    _send_admin_new_request_notification(client_request)

    # Generate AI suggestion for review (BR4).
    # A failure here must not prevent the webhook returning a success response.
    try:
        generate_ai_suggestion(client_request)
    except Exception as exc:
        logger.error(
            "AI suggestion generation failed for ClientRequest #%s: %s",
            client_request.pk, exc,
        )

    return Response({
        'message':    'Request received. A confirmation has been sent to your email.',
        'request_id': client_request.pk,
    }, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# UC4 -- Unauthenticated Customer Booking Form Submission
# ---------------------------------------------------------------------------

@api_view(['POST'])
@permission_classes([])
def booking_token_submit(request):
    """
    UC4 -- Accept a customer's booking form submission via a signed token link.

    No authentication required. The signed token in the request body
    identifies the booking and enforces expiry.

    On success the booking is updated with the customer's preferred
    physical address, date, and time. Status remains Pending until a
    technician is allocated by the administrator (UC7).
    """
    serializer = BookingTokenSubmitSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    validated = serializer.validated_data
    token     = validated['token']

    # Validate the signed token.
    try:
        payload    = signing.loads(token, salt='booking-request', max_age=None)
        booking_id = payload.get('booking_id')
    except signing.BadSignature:
        return Response(
            {'error': 'Invalid or tampered booking token.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        booking = Booking.objects.get(pk=booking_id, booking_token=token)
    except Booking.DoesNotExist:
        return Response(
            {'error': 'Booking not found for this token.'},
            status=status.HTTP_404_NOT_FOUND,
        )

    # Check whether the token has expired.
    if booking.token_expires_at and timezone.now() > booking.token_expires_at:
        return Response(
            {'error': 'This booking link has expired. Please contact us for a new link.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if booking.status != Booking.Status.PENDING:
        return Response(
            {'error': 'This booking has already been processed.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Update the booking with the customer's submitted details.
    booking.physical_address = validated['physical_address']
    booking.date             = validated['date']
    booking.time             = validated['time']
    # Clear the token after use to prevent resubmission.
    booking.booking_token    = ''
    booking.save()

    return Response({
        'message':    'Your booking request has been received. We will confirm your appointment shortly.',
        'booking_id': booking.pk,
    })


# ---------------------------------------------------------------------------
# Authentication Views
# ---------------------------------------------------------------------------

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def logout(request):
    """
    Invalidate the requesting user's authentication token.

    Deleting the token immediately revokes all API access for that session.
    The client is responsible for discarding the token locally.
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

    Checks X-Forwarded-For first to handle requests routed through a
    proxy or load balancer. Falls back to REMOTE_ADDR.
    """
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def _send_technician_welcome_email(technician, username: str, temp_password: str):
    """
    UC6, step 9 -- Send a welcome email to a newly created technician
    with their login credentials.
    """
    subject = "Your TradieRM account has been created"
    message = (
        f"Hi {technician.first_name},\n\n"
        f"An account has been created for you on TradieRM.\n\n"
        f"  Username           : {username}\n"
        f"  Temporary password : {temp_password}\n\n"
        f"Please log in and change your password as soon as possible.\n\n"
        f"Kind regards,\n"
        f"The TradieRM Team"
    )
    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=getattr(django_settings, 'DEFAULT_FROM_EMAIL', 'noreply@example.com'),
            recipient_list=[technician.email],
            fail_silently=False,
        )
    except Exception as exc:
        logger.error(
            "Failed to send welcome email to technician #%s (%s): %s",
            technician.pk, technician.email, exc,
        )


def _send_admin_new_request_notification(client_request):
    """
    UC8, step 6 -- Notify the administrator that a new job request has arrived.

    Requires ADMIN_NOTIFICATION_EMAIL to be set in Django settings.
    Logs a warning and skips silently if it is not configured.
    """
    admin_email = getattr(django_settings, 'ADMIN_NOTIFICATION_EMAIL', None)
    if not admin_email:
        logger.warning(
            "ADMIN_NOTIFICATION_EMAIL is not configured in settings. "
            "Admin notification for ClientRequest #%s skipped.",
            client_request.pk,
        )
        return

    subject = f"New job request received -- #{client_request.pk}"
    message = (
        f"A new job request has been received and is awaiting processing.\n\n"
        f"  Request ID : #{client_request.pk}\n"
        f"  Name       : {client_request.contact_name}\n"
        f"  Email      : {client_request.contact_email}\n"
        f"  Phone      : {client_request.contact_phone}\n"
        f"  Subject    : {client_request.subject}\n"
        f"  Received   : {client_request.created_at.strftime('%Y-%m-%d %H:%M UTC')}\n\n"
        f"Log in to TradieRM to review and process this request."
    )
    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=getattr(django_settings, 'DEFAULT_FROM_EMAIL', 'noreply@example.com'),
            recipient_list=[admin_email],
            fail_silently=False,
        )
    except Exception as exc:
        logger.error(
            "Failed to send admin notification for ClientRequest #%s: %s",
            client_request.pk, exc,
        )


def _send_booking_request_email(customer, booking, booking_link: str):
    """
    UC4 -- Email the customer a link to the booking form.
    """
    subject = f"Please select your preferred appointment time -- Job #{booking.job_id}"
    message = (
        f"Hi {customer.first_name},\n\n"
        f"We would like to arrange an appointment for your job request.\n\n"
        f"Please use the link below to select your preferred date, time, and address.\n"
        f"The link will expire in {BOOKING_TOKEN_EXPIRY_HOURS} hours.\n\n"
        f"  {booking_link}\n\n"
        f"If you did not request this, please ignore this email.\n\n"
        f"Kind regards,\n"
        f"The TradieRM Team"
    )
    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=getattr(django_settings, 'DEFAULT_FROM_EMAIL', 'noreply@example.com'),
            recipient_list=[customer.email],
            fail_silently=False,
        )
    except Exception as exc:
        logger.error(
            "Failed to send booking request email to customer #%s (%s): %s",
            customer.pk, customer.email, exc,
        )


def _send_allocation_email_to_customer(booking):
    """
    UC7, step 12 -- Notify the customer that a technician has been allocated
    and their appointment is confirmed.
    """
    customer   = booking.customer
    technician = booking.technician

    subject = f"Your appointment is confirmed -- Job #{booking.job_id}"
    message = (
        f"Hi {customer.first_name},\n\n"
        f"Your appointment has been confirmed.\n\n"
        f"  Job ID     : #{booking.job_id}\n"
        f"  Technician : {technician.first_name} {technician.last_name}\n"
        f"  Date       : {booking.date.strftime('%d %B %Y')}\n"
        f"  Time       : {booking.time.strftime('%I:%M %p')}\n"
        f"  Address    : {booking.physical_address}\n\n"
        f"If you need to make any changes, please contact us directly.\n\n"
        f"Kind regards,\n"
        f"The TradieRM Team"
    )
    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=getattr(django_settings, 'DEFAULT_FROM_EMAIL', 'noreply@example.com'),
            recipient_list=[customer.email],
            fail_silently=False,
        )
    except Exception as exc:
        logger.error(
            "Failed to send allocation email to customer #%s (%s): %s",
            customer.pk, customer.email, exc,
        )


def _send_allocation_email_to_technician(booking):
    """
    UC7, step 13 -- Notify the technician that a job has been allocated to them.
    """
    technician = booking.technician
    customer   = booking.customer

    subject = f"New job allocated to you -- Job #{booking.job_id}"
    message = (
        f"Hi {technician.first_name},\n\n"
        f"A new job has been allocated to you.\n\n"
        f"  Job ID   : #{booking.job_id}\n"
        f"  Customer : {customer.first_name} {customer.last_name}\n"
        f"  Address  : {booking.physical_address}\n"
        f"  Phone    : {customer.phone}\n"
        f"  Date     : {booking.date.strftime('%d %B %Y')}\n"
        f"  Time     : {booking.time.strftime('%I:%M %p')}\n\n"
        f"Please review the full job details in TradieRM before attending.\n\n"
        f"Kind regards,\n"
        f"The TradieRM Team"
    )
    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=getattr(django_settings, 'DEFAULT_FROM_EMAIL', 'noreply@example.com'),
            recipient_list=[technician.email],
            fail_silently=False,
        )
    except Exception as exc:
        logger.error(
            "Failed to send allocation email to technician #%s (%s): %s",
            technician.pk, technician.email, exc,
        )