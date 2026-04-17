"""
tradiePrototype/views.py

All API views for TradieRM.

Use case coverage:
    UC2  -- CustomerViewSet.create_with_job action
    UC3  -- BookingViewSet.create (admin-triggered)
    UC4  -- BookingViewSet.send_request action + booking_token_submit view
    UC5  -- CustomerViewSet.create_with_job action (admin-triggered with booking)
    UC7  -- CustomerViewSet.update (standard DRF update)
    UC8  -- CustomerViewSet.destroy (soft delete)
    UC9  -- BookingViewSet.destroy (soft delete)
    UC10 -- BookingViewSet.reject action
    UC13 -- TechnicianViewSet.create (provisions User + sends email)
    UC15 -- TechnicianViewSet.destroy (soft delete)
    UC17 -- BookingViewSet.allocate action (distance calc + emails)
    UC18 -- JobViewSet.update_status action (admin status transitions)
    UC20 -- InventoryViewSet
    UC21 -- InventoryViewSet (update)
    UC23 -- JobInventoryViewSet (admin-triggered part assignment)
    UC24 -- JobInventoryViewSet (technician-triggered part assignment)
    UC25 -- JobViewSet.update_status -> In Progress (records start_time)
    UC26 -- JobViewSet.update_status -> Completed (records end_time, creates invoice)
    UC27 -- InvoiceViewSet.recalculate + InvoiceViewSet.approve (PDF + email)
    UC28 -- TechnicianScheduleViewSet (admin-triggered)
    UC29 -- TechnicianScheduleViewSet (technician-triggered, own schedule)
    UC2  -- ClientRequestViewSet.process action
    UC2  -- webhook_intake view (inbound API payload)

Role access summary:
    Administrator -- full access to all resources and all actions
    Technician    -- own schedule, own jobs, job status update, part assignment
    Customer      -- read own jobs; booking form submission via token (UC4)
"""

import io
import logging
from datetime import timedelta

from django.contrib.auth.models import User
from django.core.mail import EmailMessage, send_mail
from django.core import signing
from django.conf import settings as django_settings
from django.utils import timezone

from rest_framework import viewsets, status
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.authtoken.models import Token

from .models import (
    Booking,
    Customer,
    Technician,
    Job,
    Inventory,
    JobInventory,
    ScheduleBlock,
    Invoice,
    Notification,
    ClientRequest,
    AIResponseSuggestion,
    UserProfile,
)
from .serializers import (
    CustomerSerializer,
    TechnicianSerializer,
    TechnicianCreateSerializer,
    InventorySerializer,
    JobInventorySerializer,
    JobSerializer,
    JobCreateSerializer,
    JobStatusUpdateSerializer,
    BookingSerializer,
    BookingCreateSerializer,
    BookingTokenSubmitSerializer,
    ScheduleBlockSerializer,
    TechnicianScheduleEntrySerializer,
    InvoiceSerializer,
    InvoiceRecalculateSerializer,
    InvoiceApproveSerializer,
    NotificationSerializer,
    ClientRequestSerializer,
    ClientRequestProcessSerializer,
    WebhookInboundSerializer,
    AIResponseSuggestionSerializer,
    ApproveResponseSerializer,
)
from .services.distance_service import get_road_distance_km
from .services.invoice_generator import generate_invoice
from .permissions import IsAdministrator, IsTechnician, IsCustomer, IsAdminOrTechnician

logger = logging.getLogger(__name__)

# Expiry window (hours) for the customer-facing booking form token (UC4).
BOOKING_TOKEN_EXPIRY_HOURS = 48


# ---------------------------------------------------------------------------
# Customer ViewSet
# ---------------------------------------------------------------------------

class CustomerViewSet(viewsets.ModelViewSet):
    """
    UC2, UC5, UC7, UC8, UC9 -- Full CRUD for Customer records.

    Access: Administrator only.

    UC8 (Delete Customer) is implemented as a soft delete. The destroy action
    sets status to Inactive rather than removing the record, so it is retained
    as an audit log per UC8, step 8.

    UC2 (Add Customer and Job from API pool) uses the create_with_job action,
    which creates both a Customer and a Job record atomically.
    """

    serializer_class   = CustomerSerializer
    permission_classes = [IsAdministrator]

    def get_queryset(self):
        """
        Return only Active customers for standard list and detail operations.
        Inactive records are excluded from API responses but retained in the DB.
        """
        return Customer.objects.filter(status=Customer.Status.ACTIVE)

    def destroy(self, request, *args, **kwargs):
        """
        UC8 -- Soft-delete a customer record by setting status to Inactive.

        The record is not removed from the database; it is retained as an
        audit log per UC8, step 8.
        """
        customer = self.get_object()
        customer.status = Customer.Status.INACTIVE
        customer.save()

        logger.info(
            "UC8 -- Customer #%s (%s %s) marked as Inactive by administrator '%s'.",
            customer.pk, customer.first_name, customer.last_name,
            request.user.username,
        )

        return Response(
            {'detail': 'Customer record has been marked as Inactive.'},
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=['post'], url_path='create-with-job')
    def create_with_job(self, request):
        """
        UC2 -- Create a Customer record and a Job record in a single request.

        Expected payload fields (Database Dictionary field names):
            first_name, last_name, telephone_number,
            email_address, physical_address  -- customer fields
            job_title, subject, client_message  -- job fields

        Both records are created atomically. If either fails validation,
        neither record is persisted.
        """
        customer_data = {
            'first_name':       request.data.get('first_name',       '').strip(),
            'last_name':        request.data.get('last_name',        '').strip(),
            'email_address':    request.data.get('email_address',    '').strip(),
            'telephone_number': request.data.get('telephone_number', '').strip(),
            'physical_address': request.data.get('physical_address', '').strip(),
        }
        customer_serializer = CustomerSerializer(data=customer_data)
        if not customer_serializer.is_valid():
            return Response(customer_serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        job_title      = request.data.get('job_title',      '').strip()
        subject        = request.data.get('subject',        '').strip()
        client_message = request.data.get('client_message', '').strip()

        errors = {}
        if not job_title:
            errors['job_title'] = 'Job title is required.'
        if not subject:
            errors['subject'] = 'Subject is required.'
        if not client_message:
            errors['client_message'] = 'Client message is required.'
        if errors:
            return Response(errors, status=status.HTTP_400_BAD_REQUEST)

        customer = customer_serializer.save()
        job = Job.objects.create(
            customer=customer,
            job_title=job_title,
            subject=subject,
            client_message=client_message,
            status=Job.Status.PENDING,
            source=Job.Source.MANUAL,
        )

        logger.info(
            "UC2 -- Customer #%s and Job #%s created by administrator '%s'.",
            customer.pk, job.pk, request.user.username,
        )

        return Response(
            {
                'customer': CustomerSerializer(customer).data,
                'job':      JobSerializer(job).data,
            },
            status=status.HTTP_201_CREATED,
        )


# ---------------------------------------------------------------------------
# Technician ViewSet
# ---------------------------------------------------------------------------

class TechnicianViewSet(viewsets.ModelViewSet):
    """
    UC13, UC14, UC15, UC16 -- Full CRUD for Technician records.

    Access: Administrator only.
    On create (UC13), a Django User login account is provisioned and a
    welcome email is dispatched. Soft-delete via UC15 sets is_active=False
    and deactivates the linked User account.
    """

    queryset           = Technician.objects.filter(is_active=True)
    permission_classes = [IsAdministrator]

    def get_serializer_class(self):
        """Use the creation serializer for POST; the read serializer otherwise."""
        if self.action == 'create':
            return TechnicianCreateSerializer
        return TechnicianSerializer

    def create(self, request, *args, **kwargs):
        """
        UC13 -- Create a Technician record and provision their Django User account.

        Steps:
            1. Validate all profile fields and the username.
            2. Create the Technician record.
            3. Create a Django User with a temporary password = phone number.
            4. Create a UserProfile assigning the Technician role.
            5. Create an auth token for the new user.
            6. Send a welcome email with login credentials (UC13, step 11).
        """
        serializer = TechnicianCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        username   = serializer.validated_data.pop('username')
        technician = serializer.save()

        temp_password = technician.phone or 'changeme123'
        user = User.objects.create_user(
            username=username,
            email=technician.email,
            password=temp_password,
        )
        UserProfile.objects.create(user=user, role=UserProfile.Role.TECHNICIAN)
        Token.objects.create(user=user)

        _send_technician_welcome_email(technician, username, temp_password)

        logger.info(
            "UC13 -- Technician #%s (%s %s) created with username '%s' by administrator '%s'.",
            technician.pk, technician.first_name, technician.last_name,
            username, request.user.username,
        )

        return Response(TechnicianSerializer(technician).data, status=status.HTTP_201_CREATED)

    def destroy(self, request, *args, **kwargs):
        """
        UC15 -- Soft-delete a Technician record by setting is_active to False.

        Also deactivates the linked Django User account to revoke login access.
        The record is retained as an audit log (UC15, step 9).
        """
        technician = self.get_object()
        technician.is_active = False
        technician.save()

        try:
            linked_user = User.objects.get(email=technician.email)
            linked_user.is_active = False
            linked_user.save()
        except User.DoesNotExist:
            logger.warning(
                "UC15 -- No linked Django User found for Technician #%s (%s). "
                "Technician marked Inactive without revoking a user account.",
                technician.pk, technician.email,
            )

        logger.info(
            "UC15 -- Technician #%s (%s %s) marked as Inactive by administrator '%s'.",
            technician.pk, technician.first_name, technician.last_name,
            request.user.username,
        )

        return Response(
            {'detail': 'Technician record has been marked as Inactive.'},
            status=status.HTTP_200_OK,
        )


# ---------------------------------------------------------------------------
# Inventory ViewSet
# ---------------------------------------------------------------------------

class InventoryViewSet(viewsets.ModelViewSet):
    """
    UC20, UC21, UC22 -- Full CRUD for Inventory records.

    Access: Administrator only.
    The status field is managed automatically by the model based on quantity.
    Name uniqueness is enforced by the serializer (case-insensitive).
    """

    queryset           = Inventory.objects.all()
    serializer_class   = InventorySerializer
    permission_classes = [IsAdministrator]


# ---------------------------------------------------------------------------
# Job Inventory ViewSet
# ---------------------------------------------------------------------------

class JobInventoryViewSet(viewsets.ModelViewSet):
    """
    UC23, UC24 -- Manage assignment of Inventory items (parts) to a Job.

    UC23 (Admin-Triggered): permitted when job status is Allocated, In Progress,
    or Completed. Access: Administrator only.

    UC24 (Technician-Triggered): permitted only when job status is Allocated or
    In Progress, and the technician must be assigned to the job. Access: Technician.

    Permission and status validation is enforced in create() and destroy().
    Filtering by job via ?job=<id> is supported for both list views.
    """

    queryset           = JobInventory.objects.select_related('job', 'inventory')
    serializer_class   = JobInventorySerializer
    permission_classes = [IsAdministrator | IsTechnician]

    def get_queryset(self):
        """
        Return JobInventory records scoped by role and optional job filter.

        Administrators receive all records.
        Technicians receive only records for jobs assigned to them (UC24 --
        a technician must not view or modify parts on another technician's job).

        The ?job=<id> query parameter narrows results further for both roles.
        NOTE: job__technician__email will be updated to job__technician__email_address
        once the Technician model field rename migration is applied.
        """
        user    = self.request.user
        profile = getattr(user, 'profile', None)

        qs = JobInventory.objects.select_related('job', 'inventory')

        if profile and profile.is_technician:
            # Restrict technicians to jobs assigned to them only.
            qs = qs.filter(job__technician__email=user.email)

        job_id = self.request.query_params.get('job')
        if job_id:
            qs = qs.filter(job_id=job_id)

        return qs

    def create(self, request, *args, **kwargs):
        """
        UC23, UC24 -- Add a part to a job.

        Administrators (UC23): job must be Allocated, In Progress, or Completed.
        Technicians (UC24): job must be Allocated or In Progress, and the
        requesting technician must be the one assigned to the job.
        """
        profile = getattr(request.user, 'profile', None)

        serializer = JobInventorySerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        job_id = serializer.validated_data['job'].pk
        try:
            job = Job.objects.get(pk=job_id)
        except Job.DoesNotExist:
            return Response({'error': 'Job not found.'}, status=status.HTTP_404_NOT_FOUND)

        if profile and profile.is_technician:
            # UC24 -- technician is restricted to Allocated and In Progress only.
            allowed_statuses = [Job.Status.ALLOCATED, Job.Status.IN_PROGRESS]
            if job.status not in allowed_statuses:
                return Response(
                    {
                        'error': (
                            "Job parts can only be added to a job with a status of "
                            "Allocated or In Progress."
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            # UC24 -- technician must be assigned to this job.
            if not job.technician or job.technician.email != request.user.email:
                return Response(
                    {'error': 'You are not assigned to this job.'},
                    status=status.HTTP_403_FORBIDDEN,
                )
        else:
            # UC23 -- administrator is permitted for Allocated, In Progress, and Completed.
            allowed_statuses = [
                Job.Status.ALLOCATED,
                Job.Status.IN_PROGRESS,
                Job.Status.COMPLETED,
            ]
            if job.status not in allowed_statuses:
                return Response(
                    {
                        'error': (
                            "Job parts can only be added to a job with a status of "
                            "Allocated, In Progress, or Completed."
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        job_inventory = serializer.save()
        return Response(
            JobInventorySerializer(job_inventory).data,
            status=status.HTTP_201_CREATED,
        )


# ---------------------------------------------------------------------------
# Job ViewSet
# ---------------------------------------------------------------------------

class JobViewSet(viewsets.ModelViewSet):
    """
    UC2, UC18, UC19, UC25, UC26 -- Full CRUD for Job records.

    Access:
        Administrator -- full access to all jobs and all status transitions.
        Technician    -- read access to own assigned jobs; In Progress and
                         Completed transitions (UC25, UC26).
        Customer      -- read access to own jobs only (matched by email_address).
    """

    queryset           = Job.objects.select_related('customer', 'technician')
    permission_classes = [IsAdministrator | IsTechnician | IsCustomer]

    def get_serializer_class(self):
        """Use the create serializer for POST; the full serializer otherwise."""
        if self.action == 'create':
            return JobCreateSerializer
        return JobSerializer

    def get_queryset(self):
        """
        Scope the queryset based on the requesting user's role.
        Administrators receive all jobs.
        Technicians receive only jobs assigned to them.
        Customers receive only their own jobs matched by email_address.
        NOTE: technician__email will be updated to technician__email_address
        once the Technician model field rename migration is applied.
        """
        user    = self.request.user
        profile = getattr(user, 'profile', None)

        if profile and profile.is_technician:
            return Job.objects.filter(
                technician__email=user.email
            ).select_related('customer', 'technician')

        if profile and profile.is_customer:
            return Job.objects.filter(
                customer__email_address=user.email
            ).select_related('customer', 'technician')

        return Job.objects.select_related('customer', 'technician')

    def create(self, request, *args, **kwargs):
        """
        UC2 -- Create a standalone Job record.
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
        UC18, UC25, UC26 -- Update the status of an existing job.

        Transition rules enforced by JobStatusUpdateSerializer:
            Allocated   -> In Progress  (UC25 -- technician; records start_time)
            In Progress -> Completed    (UC26 -- technician; records end_time,
                                                creates draft Invoice)
            Allocated/In Progress -> Suspended   (admin_feedback required)
            Allocated/In Progress -> Cancelled   (admin_feedback required)

        The current_status of the job is injected into the serializer so
        transition guards can be evaluated without trusting client input.
        """
        job     = self.get_object()
        profile = getattr(request.user, 'profile', None)
        role    = profile.role if profile else ''

        serializer = JobStatusUpdateSerializer(
            data={
                **request.data,
                'role':           role,
                'current_status': job.status,
            }
        )
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        validated  = serializer.validated_data
        new_status = validated['new_status']

        # -- UC25: Record the job start time when transitioning to In Progress.
        if new_status == Job.Status.IN_PROGRESS:
            job.start_time = timezone.now()
            logger.info(
                "UC25 -- Job #%s start_time recorded as %s by technician '%s'.",
                job.pk, job.start_time, request.user.username,
            )

        # -- UC26: Record the job end time when transitioning to Completed.
        if new_status == Job.Status.COMPLETED:
            job.end_time = timezone.now()
            logger.info(
                "UC26 -- Job #%s end_time recorded as %s by technician '%s'.",
                job.pk, job.end_time, request.user.username,
            )

        job.status = new_status

        if validated.get('admin_feedback'):
            job.admin_feedback = validated['admin_feedback']
        if validated.get('technician_feedback'):
            job.technician_feedback = validated['technician_feedback']

        job.save()

        # -- UC26, step 9: Auto-generate a draft Invoice on job completion.
        invoice_data = None
        if new_status == Job.Status.COMPLETED:
            try:
                invoice      = generate_invoice(job)
                invoice_data = InvoiceSerializer(invoice).data
            except Exception as exc:
                logger.error(
                    "UC26 -- Invoice generation failed for Job #%s: %s",
                    job.pk, exc,
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
    UC3, UC4, UC9, UC10, UC11, UC17 -- Manage Booking records.

    Access: Administrator only for all standard CRUD and actions.
    The booking_token_submit view is separate and unauthenticated (UC4).
    """

    queryset           = Booking.objects.select_related('job', 'customer', 'technician')
    permission_classes = [IsAdministrator]

    def get_serializer_class(self):
        """Use the create serializer for POST; the read serializer otherwise."""
        if self.action == 'create':
            return BookingCreateSerializer
        return BookingSerializer

    def get_queryset(self):
        """Return bookings ordered by creation date to prioritise the UC17 allocation queue."""
        return Booking.objects.select_related(
            'job', 'customer', 'technician'
        ).order_by('created_at')

    def create(self, request, *args, **kwargs):
        """UC3 -- Create a new Booking record with status Pending."""
        serializer = BookingCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        booking = serializer.save(status=Booking.Status.PENDING)
        return Response(BookingSerializer(booking).data, status=status.HTTP_201_CREATED)

    def destroy(self, request, *args, **kwargs):
        """
        UC10 -- Soft-delete a booking record by setting status to Inactive.

        A Confirmed booking cannot be deleted (UC10, step 6).
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
            "UC10 -- Booking #%s marked as Inactive by administrator '%s'.",
            booking.pk, request.user.username,
        )

        return Response(
            {'detail': 'Booking record has been marked as Inactive.'},
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=['post'], url_path='send-request')
    def send_request(self, request, pk=None):
        """
        UC4 -- Email the customer a signed booking form link.

        Generates a time-limited signed token using Django's TimestampSigner,
        stores it on the Booking record, and dispatches the email.
        """
        booking  = self.get_object()
        customer = booking.customer

        if not customer.email_address:
            return Response(
                {'error': 'No email address is recorded for this customer.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        token  = signing.dumps({'booking_id': booking.pk}, salt='booking-request')
        expiry = timezone.now() + timedelta(hours=BOOKING_TOKEN_EXPIRY_HOURS)

        booking.booking_token    = token
        booking.token_expires_at = expiry
        booking.save(update_fields=['booking_token', 'token_expires_at'])

        base_url     = getattr(django_settings, 'SITE_BASE_URL', 'http://localhost:3000')
        booking_link = f"{base_url}/booking/submit?token={token}"

        _send_booking_request_email(customer, booking, booking_link)

        return Response({'message': 'Booking request email sent successfully.'})

    @action(detail=True, methods=['post'], url_path='reject')
    def reject(self, request, pk=None):
        """
        UC9 -- Reject a Pending booking.

        Only a Pending booking may be rejected (UC9, step 6a).
        The record is retained as an audit log.
        """
        booking = self.get_object()

        if booking.status != Booking.Status.PENDING:
            return Response(
                {
                    'error': (
                        f"Booking is '{booking.status}', not Pending. "
                        "Only a Pending booking can be rejected."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        booking.status = Booking.Status.REJECTED
        booking.save()

        logger.info(
            "UC9 -- Booking #%s rejected by administrator '%s'.",
            booking.pk, request.user.username,
        )

        return Response(
            {'detail': 'Booking record has been set to Rejected.'},
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=['post'], url_path='allocate')
    def allocate(self, request, pk=None):
        """
        UC17 -- Allocate a technician to a Pending booking.

        Steps:
            1. Confirm booking is in Pending status.
            2. Retrieve and validate the supplied technician.
            3. Calculate road distance via OpenRouteService.
            4. Update booking: status -> Confirmed, assign technician, store distance.
            5. Update job: status -> Allocated, assign technician.
            6. Send confirmation emails to customer and technician.
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

        # Calculate road distance between technician home and booking address (UC17, step 6).
        distance_km = None
        if technician.home_address and booking.physical_address:
            distance_km = get_road_distance_km(
                technician.home_address,
                booking.physical_address,
            )
            if distance_km is None:
                logger.warning(
                    "UC17 -- Distance calculation returned None for Booking #%s. "
                    "Proceeding with null distance value.",
                    booking.pk,
                )

        booking.technician = technician
        booking.status     = Booking.Status.CONFIRMED
        booking.distance   = distance_km
        booking.save()

        job            = booking.job
        job.technician = technician
        job.status     = Job.Status.ALLOCATED
        job.save()

        _send_allocation_email_to_customer(booking)
        _send_allocation_email_to_technician(booking)

        return Response({
            'booking':     BookingSerializer(booking).data,
            'job':         JobSerializer(job).data,
            'distance_km': distance_km,
        })


# ---------------------------------------------------------------------------
# Schedule Block ViewSet (raw blocks -- UC28/UC29 use TechnicianScheduleViewSet)
# ---------------------------------------------------------------------------

class ScheduleBlockViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Read-only view of raw ScheduleBlock records.

    Access:
        Administrator -- all blocks for all technicians.
        Technician    -- own blocks only (matched by email).

    Note: UC28 and UC29 are served by TechnicianScheduleViewSet which
    returns the structured schedule format defined in the use cases.
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
# Technician Schedule ViewSet (UC28, UC29)
# ---------------------------------------------------------------------------

class TechnicianScheduleViewSet(viewsets.ViewSet):
    """
    UC28, UC29 -- Technician schedule views.

    UC28 (Admin-Triggered):
        GET /api/technician-schedule/
            Returns list of all active technicians, allocated first.
        GET /api/technician-schedule/{technician_id}/
            Returns the schedule for the specified technician.

    UC29 (Technician-Triggered):
        GET /api/my-schedule/
            Returns the authenticated technician's own schedule.

    Schedule format per use case:
        - In Progress job displayed first (is_in_progress=True).
        - Followed by Allocated jobs ordered by booking date and time.
        - Each entry: Booking ID, Job ID, Customer Full Name,
          Customer Physical Address, Date, Time, Distance.
    """

    permission_classes = [IsAdminOrTechnician]

    def list(self, request):
        """
        UC28, step 2 -- Return list of all active technicians.

        Allocated technicians (those with at least one Allocated or In Progress
        booking) are returned first, followed by unallocated technicians.
        Access: Administrator only.
        """
        profile = getattr(request.user, 'profile', None)
        if not profile or not profile.is_admin:
            return Response(
                {'error': 'Only administrators can view the technician list.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        from django.db.models import Exists, OuterRef

        # Subquery: does this technician have any Allocated or In Progress bookings?
        has_active_booking = Booking.objects.filter(
            technician=OuterRef('pk'),
            status=Booking.Status.CONFIRMED,
            job__status__in=[Job.Status.ALLOCATED, Job.Status.IN_PROGRESS],
        )

        technicians = (
            Technician.objects
            .filter(is_active=True)
            .annotate(is_allocated=Exists(has_active_booking))
            .order_by('-is_allocated', 'last_name', 'first_name')
        )

        data = TechnicianSerializer(technicians, many=True).data
        return Response(data)

    def retrieve(self, request, pk=None):
        """
        UC28, steps 3-4 -- Return the schedule for a specific technician.

        Access: Administrator only.
        Returns In Progress job first, then Allocated jobs ordered by date/time.
        Returns an empty schedule list if the technician has no active jobs (UC28, step 3a).
        """
        profile = getattr(request.user, 'profile', None)
        if not profile or not profile.is_admin:
            return Response(
                {'error': 'Only administrators can view technician schedules.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            technician = Technician.objects.get(pk=pk, is_active=True)
        except Technician.DoesNotExist:
            return Response(
                {'error': 'Technician not found or is inactive.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        schedule = _build_technician_schedule(technician)

        return Response({
            'technician': TechnicianSerializer(technician).data,
            'schedule':   TechnicianScheduleEntrySerializer(schedule, many=True).data,
        })

    @action(detail=False, methods=['get'], url_path='mine',
            permission_classes=[IsTechnician])
    def mine(self, request):
        """
        UC29 -- Return the authenticated technician's own schedule.

        In Progress job is shown first, followed by Allocated jobs ordered by
        booking date and time. Returns an empty list if no active jobs exist
        (UC29, step 2a alternate course).
        """
        try:
            technician = Technician.objects.get(email=request.user.email, is_active=True)
        except Technician.DoesNotExist:
            return Response(
                {'error': 'Technician profile not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        schedule = _build_technician_schedule(technician)

        return Response({
            'technician': TechnicianSerializer(technician).data,
            'schedule':   TechnicianScheduleEntrySerializer(schedule, many=True).data,
        })


def _build_technician_schedule(technician: Technician) -> list:
    """
    UC28, UC29 -- Build the ordered schedule entry list for a technician.

    Returns a list of dicts matching TechnicianScheduleEntrySerializer:
        - In Progress jobs first (is_in_progress=True).
        - Then Allocated jobs ordered by booking date ascending, then time ascending.

    Queries Booking records in Confirmed status whose associated Job is either
    In Progress or Allocated. The booking provides date, time, distance, and
    customer address as required by the use cases.
    """
    bookings = (
        Booking.objects
        .filter(
            technician=technician,
            status=Booking.Status.CONFIRMED,
            job__status__in=[Job.Status.IN_PROGRESS, Job.Status.ALLOCATED],
        )
        .select_related('job', 'job__customer')
        .order_by('date', 'time')
    )

    in_progress_entries = []
    allocated_entries   = []

    for booking in bookings:
        job      = booking.job
        customer = job.customer

        entry = {
            'booking_id':         booking.pk,
            'job_id':             job.pk,
            'customer_full_name': f"{customer.first_name} {customer.last_name}",
            'customer_address':   booking.physical_address,
            'date':               booking.date,
            'time':               booking.time,
            'distance':           booking.distance,
            'job_status':         job.status,
            'is_in_progress':     job.status == Job.Status.IN_PROGRESS,
        }

        if job.status == Job.Status.IN_PROGRESS:
            in_progress_entries.append(entry)
        else:
            allocated_entries.append(entry)

    # In Progress job always displayed first per UC28, step 4 / UC29, step 2.
    return in_progress_entries + allocated_entries


# ---------------------------------------------------------------------------
# Invoice ViewSet
# ---------------------------------------------------------------------------

class InvoiceViewSet(viewsets.ModelViewSet):
    """
    UC26, UC27 -- View and manage Invoice records.

    Access: Administrator only.

    Standard list/retrieve: returns all invoices (default ordering: newest first).
    The list endpoint supports filtering by status via ?status=draft or ?status=sent.

    Custom actions:
        POST /api/invoices/{id}/recalculate/  -- UC27, steps 5-7
        POST /api/invoices/{id}/approve/      -- UC27, steps 8-13

    Invoices are created automatically by the invoice_generator service (UC26).
    Manual creation via POST is disabled.
    """

    queryset           = Invoice.objects.select_related('job__customer', 'technician')
    serializer_class   = InvoiceSerializer
    permission_classes = [IsAdministrator]

    def get_queryset(self):
        """
        Return invoices filtered by status if the ?status query parameter is supplied.
        Default ordering is newest first (model Meta ordering applies).
        """
        qs            = Invoice.objects.select_related('job__customer', 'job', 'technician')
        status_filter = self.request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter)
        return qs

    def create(self, request, *args, **kwargs):
        """
        Invoice creation is handled automatically by the system (UC26).
        Manual creation via the API is not permitted.
        """
        return Response(
            {'error': 'Invoices are created automatically when a job is completed.'},
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    @action(detail=True, methods=['post'], url_path='recalculate')
    def recalculate(self, request, pk=None):
        """
        UC27, steps 5-7 -- Recalculate invoice cost fields from updated input values.

        Accepts any combination of hours_taken, distance_rate,
        service_charge_percentage, and notes. Applies the supplied values to
        the invoice instance and recalculates all derived fields without saving.

        Returns the updated invoice data so the administrator can review the
        new totals before deciding to approve.
        """
        invoice    = self.get_object()
        serializer = InvoiceRecalculateSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        validated = serializer.validated_data

        # Apply only the fields that were supplied; retain existing values otherwise.
        if 'hours_taken' in validated:
            invoice.hours_taken = validated['hours_taken']
        if 'distance_rate' in validated:
            invoice.distance_rate = validated['distance_rate']
        if 'service_charge_percentage' in validated:
            invoice.service_charge_percentage = validated['service_charge_percentage']
        if 'notes' in validated:
            invoice.notes = validated['notes']

        # Recalculate all derived fields (UC27, step 7). Do not save yet.
        invoice.calculate_totals()

        logger.info(
            "UC27 -- Invoice #%s recalculated by administrator '%s'. "
            "hours_taken=%.2f, distance_rate=%.2f, scp=%.2f, total=%.2f.",
            invoice.pk, request.user.username,
            invoice.hours_taken, invoice.distance_rate,
            invoice.service_charge_percentage, invoice.total_cost,
        )

        return Response(InvoiceSerializer(invoice).data)

    @action(detail=True, methods=['post'], url_path='approve')
    def approve(self, request, pk=None):
        """
        UC27, steps 8-13 -- Approve a draft invoice.

        Steps performed:
            1. Validate submitted fields (hours_taken must be > 0, UC27 step 9a).
            2. Apply updated values and recalculate all cost fields.
            3. Persist the updated invoice.
            4. Generate a PDF of the invoice.
            5. Email the PDF to the customer with invoice details in the body.
            6. Set invoice status to Sent.
            7. Return confirmation response (UC27, step 13).
        """
        invoice = self.get_object()

        if invoice.status != Invoice.Status.DRAFT:
            return Response(
                {'error': f"Invoice is '{invoice.status}', not Draft. Cannot approve."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = InvoiceApproveSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        validated = serializer.validated_data

        invoice.hours_taken = validated['hours_taken']
        if 'distance_rate' in validated:
            invoice.distance_rate = validated['distance_rate']
        if 'service_charge_percentage' in validated:
            invoice.service_charge_percentage = validated['service_charge_percentage']
        if 'notes' in validated:
            invoice.notes = validated['notes']

        # Recalculate all derived fields with the final values (UC27, step 7).
        invoice.calculate_totals()

        # Generate the PDF document (UC27, step 10).
        try:
            pdf_bytes = _generate_invoice_pdf(invoice)
        except Exception as exc:
            logger.error(
                "UC27 -- PDF generation failed for Invoice #%s: %s",
                invoice.pk, exc,
            )
            return Response(
                {'error': 'PDF generation failed. Invoice has not been sent.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # Email the PDF to the customer (UC27, step 11).
        try:
            _send_invoice_to_customer(invoice, pdf_bytes)
        except Exception as exc:
            logger.error(
                "UC27 -- Email dispatch failed for Invoice #%s: %s",
                invoice.pk, exc,
            )
            return Response(
                {'error': 'Email dispatch failed. Invoice has not been marked as Sent.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # Mark as Sent only after both PDF generation and email succeed (UC27, step 12).
        invoice.status = Invoice.Status.SENT
        invoice.save()

        logger.info(
            "UC27 -- Invoice #%s approved and sent to customer '%s' by administrator '%s'.",
            invoice.pk, invoice.job.customer.email_address, request.user.username,
        )

        return Response({
            'detail': 'Invoice has been approved and sent to the customer.',
            'invoice': InvoiceSerializer(invoice).data,
        })


# ---------------------------------------------------------------------------
# Notification ViewSet
# ---------------------------------------------------------------------------

class NotificationViewSet(viewsets.ReadOnlyModelViewSet):
    """
    UC26, step 10 -- In-system notification records for the administrator dashboard.

    Access: Administrator only.
    Returns notifications addressed to the authenticated administrator.
    Unread notifications are surfaced first.

    Custom action:
        POST /api/notifications/{id}/mark-read/ -- acknowledges a notification.
    """

    serializer_class   = NotificationSerializer
    permission_classes = [IsAdministrator]

    def get_queryset(self):
        """
        Return notifications for the authenticated administrator only,
        ordered with unread first, then by creation time descending.
        """
        return Notification.objects.filter(
            recipient=self.request.user
        ).order_by('is_read', '-created_at')

    @action(detail=True, methods=['post'], url_path='mark-read')
    def mark_read(self, request, pk=None):
        """Mark a single notification as read."""
        notification = self.get_object()

        if notification.is_read:
            return Response(
                {'detail': 'Notification is already marked as read.'},
                status=status.HTTP_200_OK,
            )

        notification.mark_as_read()

        logger.info(
            "Notification #%s marked as read by administrator '%s'.",
            notification.pk, request.user.username,
        )

        return Response(NotificationSerializer(notification).data)

    @action(detail=False, methods=['post'], url_path='mark-all-read')
    def mark_all_read(self, request):
        """Mark all unread notifications for the authenticated administrator as read."""
        now           = timezone.now()
        updated_count = Notification.objects.filter(
            recipient=request.user,
            is_read=False,
        ).update(is_read=True, read_at=now)

        logger.info(
            "%d notification(s) marked as read by administrator '%s'.",
            updated_count, request.user.username,
        )

        return Response({'detail': f"{updated_count} notification(s) marked as read."})


# ---------------------------------------------------------------------------
# Client Request ViewSet
# ---------------------------------------------------------------------------

class ClientRequestViewSet(viewsets.ReadOnlyModelViewSet):
    """
    UC2 -- View inbound job requests and process them into Customer + Job records.

    Access: Administrator only.
    Records are created exclusively by the webhook_intake view.
    The process action converts an Unprocessed request into a Customer + Job.
    """

    queryset           = ClientRequest.objects.all()
    serializer_class   = ClientRequestSerializer
    permission_classes = [IsAdministrator]

    def get_queryset(self):
        """Return Unprocessed requests first to support the UC2 workflow."""
        return ClientRequest.objects.order_by('status', 'created_at')

    @action(detail=True, methods=['post'], url_path='process')
    def process(self, request, pk=None):
        """
        UC2 -- Convert an Unprocessed ClientRequest into a Customer and Job record.

        Validates that all required fields exist on the ClientRequest, then
        creates both records and marks the request as Processed.
        """
        client_request = self.get_object()

        if client_request.status != ClientRequest.Status.UNPROCESSED:
            return Response(
                {'error': 'This request has already been processed.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        process_serializer = ClientRequestProcessSerializer(
            data={}, context={'client_request': client_request}
        )
        if not process_serializer.is_valid():
            return Response(process_serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        name_parts = client_request.contact_name.strip().split(' ', 1)
        first_name = name_parts[0]
        last_name  = name_parts[1] if len(name_parts) > 1 else ''

        customer, _ = Customer.objects.get_or_create(
            email_address=client_request.contact_email,
            defaults={
                'first_name':       first_name,
                'last_name':        last_name,
                'telephone_number': client_request.contact_phone,
            },
        )

        job = Job.objects.create(
            customer=customer,
            subject=client_request.subject,
            client_message=client_request.message,
            status=Job.Status.PENDING,
            source=Job.Source.WEBHOOK,
            client_request=client_request,
        )

        client_request.status = ClientRequest.Status.PROCESSED
        client_request.save(update_fields=['status', 'updated_at'])

        logger.info(
            "UC2 -- ClientRequest #%s processed. Customer #%s and Job #%s created "
            "by administrator '%s'.",
            client_request.pk, customer.pk, job.pk, request.user.username,
        )

        return Response({
            'customer':       CustomerSerializer(customer).data,
            'job':            JobSerializer(job).data,
            'client_request': ClientRequestSerializer(client_request).data,
        }, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# AI Response Suggestion ViewSet (BR4, BR5 -- descoped, retained for audit)
# ---------------------------------------------------------------------------

class AIResponseSuggestionViewSet(viewsets.ReadOnlyModelViewSet):
    """
    BR4, BR5 -- Review AI-generated response suggestions.

    NOTE: This feature was formally descoped. The ViewSet is retained to
    avoid breaking existing API clients that may query these endpoints.
    No new suggestions are created by the current application flow.
    """

    queryset           = AIResponseSuggestion.objects.select_related('client_request')
    serializer_class   = AIResponseSuggestionSerializer
    permission_classes = [IsAdminOrTechnician]

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        """BR5 -- Approve a pending AI suggestion (descoped; retained for audit)."""
        suggestion = self.get_object()

        if suggestion.approval_status != AIResponseSuggestion.ApprovalStatus.PENDING:
            return Response(
                {'error': f"Suggestion is already '{suggestion.approval_status}'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = ApproveResponseSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        suggestion.final_response      = serializer.validated_data['final_response']
        suggestion.reviewed_by_user_id = request.user.pk
        suggestion.reviewed_at         = timezone.now()
        suggestion.approval_status     = AIResponseSuggestion.ApprovalStatus.APPROVED
        suggestion.save()

        return Response(AIResponseSuggestionSerializer(suggestion).data)

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        """BR5 -- Reject a pending AI suggestion (descoped; retained for audit)."""
        suggestion = self.get_object()

        suggestion.reviewed_by_user_id = request.user.pk
        suggestion.reviewed_at         = timezone.now()
        suggestion.approval_status     = AIResponseSuggestion.ApprovalStatus.REJECTED
        suggestion.save()

        return Response(AIResponseSuggestionSerializer(suggestion).data)


# ---------------------------------------------------------------------------
# Public views (no authentication required)
# ---------------------------------------------------------------------------

@api_view(['POST'])
@permission_classes([])
def webhook_intake(request):
    """
    UC2 -- Receive an inbound job request from the external website via API.

    No authentication required. Validates the payload, creates a ClientRequest
    record, sends an acknowledgement email to the client, and notifies the admin.
    """
    serializer = WebhookInboundSerializer(data=request.data)

    if not serializer.is_valid():
        # Validation failure -- send contact details to client if possible.
        email = request.data.get('email', '').strip()
        if email:
            _send_contact_details_email_on_failed_request(email)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    validated = serializer.validated_data

    client_request = ClientRequest.objects.create(
        source_ip     = _get_client_ip(request),
        raw_payload   = request.data,
        subject       = validated.get('subject', ''),
        message       = validated['message'],
        contact_name  = f"{validated['first_name']} {validated['last_name']}".strip(),
        contact_email = validated['email'],
        contact_phone = validated.get('phone', ''),
        status        = ClientRequest.Status.UNPROCESSED,
    )

    _send_client_acknowledgement_email(client_request)
    _send_admin_new_request_notification(client_request)

    logger.info(
        "UC2 -- ClientRequest #%s created from webhook (IP: %s).",
        client_request.pk, client_request.source_ip,
    )

    return Response({
        'message':    'Your request has been received. A confirmation has been sent to your email.',
        'request_id': client_request.pk,
    }, status=status.HTTP_201_CREATED)


@api_view(['POST'])
@permission_classes([])
def booking_token_submit(request):
    """
    UC4 -- Accept a customer's booking form submission via a signed token link.

    No authentication required. The signed token in the request body identifies
    the booking and enforces expiry. On success the booking is updated with the
    customer's preferred physical address, date, and time.
    """
    serializer = BookingTokenSubmitSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    validated = serializer.validated_data
    token     = validated['token']

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

    booking.physical_address = validated['physical_address']
    booking.date             = validated['date']
    booking.time             = validated['time']
    booking.booking_token    = ''  # Clear token after use to prevent resubmission.
    booking.save()

    return Response({
        'message':    'Your booking request has been received. We will confirm your appointment shortly.',
        'booking_id': booking.pk,
    })


# ---------------------------------------------------------------------------
# Authentication views
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

    Used by the frontend immediately after login to determine which dashboard
    and navigation options to render.
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
# PDF generation helper (UC27, step 10)
# ---------------------------------------------------------------------------

def _generate_invoice_pdf(invoice: Invoice) -> bytes:
    """
    UC27, step 10 -- Generate a PDF document for the approved invoice.

    Uses the reportlab library if available. Falls back to a plain-text
    byte string for environments where reportlab is not installed,
    so the approve action is never blocked by a missing dependency.

    Returns raw PDF bytes suitable for attachment to an email.
    """
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm
        from reportlab.pdfgen import canvas as rl_canvas

        buffer = io.BytesIO()
        c      = rl_canvas.Canvas(buffer, pagesize=A4)
        width, height = A4

        customer   = invoice.job.customer
        technician = invoice.technician

        # -- Header
        c.setFont("Helvetica-Bold", 18)
        c.drawString(20 * mm, height - 25 * mm, "TAX INVOICE")

        c.setFont("Helvetica", 10)
        c.drawString(20 * mm, height - 35 * mm, f"Invoice #:    {invoice.pk}")
        c.drawString(20 * mm, height - 41 * mm, f"Date:         {invoice.date_generated.strftime('%d %B %Y')}")
        c.drawString(20 * mm, height - 47 * mm, f"Job #:        {invoice.job.pk}")
        c.drawString(20 * mm, height - 53 * mm, f"Subject:      {invoice.job.subject}")

        # -- Customer details
        c.setFont("Helvetica-Bold", 11)
        c.drawString(20 * mm, height - 65 * mm, "Bill To")
        c.setFont("Helvetica", 10)
        c.drawString(20 * mm, height - 72 * mm, f"{customer.first_name} {customer.last_name}")
        c.drawString(20 * mm, height - 78 * mm, customer.physical_address or '')
        c.drawString(20 * mm, height - 84 * mm, customer.telephone_number or '')
        c.drawString(20 * mm, height - 90 * mm, customer.email_address or '')

        # -- Technician details
        c.setFont("Helvetica-Bold", 11)
        c.drawString(110 * mm, height - 65 * mm, "Technician")
        c.setFont("Helvetica", 10)
        if technician:
            c.drawString(110 * mm, height - 72 * mm,
                         f"{technician.first_name} {technician.last_name}")
            c.drawString(110 * mm, height - 78 * mm, technician.email or '')

        # -- Cost breakdown
        y = height - 110 * mm
        c.setFont("Helvetica-Bold", 11)
        c.drawString(20 * mm, y, "Cost Breakdown")
        c.setFont("Helvetica", 10)

        rows = [
            ("Hours Taken",   f"{invoice.hours_taken:.2f} hrs"),
            ("Hourly Rate",   f"${invoice.hourly_rate:.2f}"),
            ("Labour Cost",   f"${invoice.labour_cost:.2f}"),
            ("Distance",      f"{invoice.distance:.2f} km"),
            ("Distance Rate", f"${invoice.distance_rate:.2f}/km"),
            ("Distance Cost", f"${invoice.distance_cost:.2f}"),
            ("Parts Cost",    f"${invoice.parts_cost:.2f}"),
            ("Subtotal",      f"${invoice.subtotal:.2f}"),
            (f"Service Charge ({invoice.service_charge_percentage:.2f}%)",
             f"${invoice.service_charge:.2f}"),
            ("TOTAL",         f"${invoice.total_cost:.2f}"),
        ]

        for i, (label, value) in enumerate(rows):
            row_y = y - (8 * mm) - (i * 7 * mm)
            if label == "TOTAL":
                c.setFont("Helvetica-Bold", 11)
            c.drawString(20 * mm, row_y, label)
            c.drawRightString(190 * mm, row_y, value)
            c.setFont("Helvetica", 10)

        if invoice.notes:
            notes_y = y - (8 * mm) - (len(rows) * 7 * mm) - 10 * mm
            c.setFont("Helvetica-Oblique", 9)
            c.drawString(20 * mm, notes_y, f"Notes: {invoice.notes}")

        c.showPage()
        c.save()

        return buffer.getvalue()

    except ImportError:
        # reportlab is not installed -- generate a minimal plain-text fallback.
        logger.warning(
            "reportlab is not installed. Generating plain-text invoice for Invoice #%s.",
            invoice.pk,
        )
        customer = invoice.job.customer
        text = (
            f"TAX INVOICE\n"
            f"Invoice #: {invoice.pk}\n"
            f"Date: {invoice.date_generated.strftime('%d %B %Y')}\n"
            f"Job #: {invoice.job.pk}\n\n"
            f"Customer: {customer.first_name} {customer.last_name}\n"
            f"Address: {customer.physical_address}\n\n"
            f"Labour Cost:    ${invoice.labour_cost:.2f}\n"
            f"Distance Cost:  ${invoice.distance_cost:.2f}\n"
            f"Parts Cost:     ${invoice.parts_cost:.2f}\n"
            f"Subtotal:       ${invoice.subtotal:.2f}\n"
            f"Service Charge: ${invoice.service_charge:.2f}\n"
            f"TOTAL:          ${invoice.total_cost:.2f}\n"
        )
        return text.encode('utf-8')


# ---------------------------------------------------------------------------
# Email helpers
# ---------------------------------------------------------------------------

def _send_invoice_to_customer(invoice: Invoice, pdf_bytes: bytes) -> None:
    """
    UC27, step 11 -- Email the approved invoice PDF to the customer.

    Attaches the PDF and includes key invoice details in the email body.
    Raises on failure so the caller can abort without marking the invoice Sent.
    """
    customer = invoice.job.customer

    subject = f"Your invoice from TradieRM -- Invoice #{invoice.pk}"
    body    = (
        f"Dear {customer.first_name},\n\n"
        f"Please find attached your invoice for the recent service.\n\n"
        f"  Invoice #      : {invoice.pk}\n"
        f"  Job #          : {invoice.job.pk}\n"
        f"  Subject        : {invoice.job.subject}\n"
        f"  Labour Cost    : ${invoice.labour_cost:.2f}\n"
        f"  Distance Cost  : ${invoice.distance_cost:.2f}\n"
        f"  Parts Cost     : ${invoice.parts_cost:.2f}\n"
        f"  Subtotal       : ${invoice.subtotal:.2f}\n"
        f"  Service Charge : ${invoice.service_charge:.2f}\n"
        f"  TOTAL          : ${invoice.total_cost:.2f}\n\n"
    )
    if invoice.notes:
        body += f"Notes: {invoice.notes}\n\n"

    body += (
        f"If you have any questions regarding this invoice, please contact us.\n\n"
        f"Kind regards,\n"
        f"The TradieRM Team"
    )

    from_email = getattr(django_settings, 'DEFAULT_FROM_EMAIL', 'noreply@example.com')

    email_msg = EmailMessage(
        subject    = subject,
        body       = body,
        from_email = from_email,
        to         = [customer.email_address],
    )
    email_msg.attach(
        filename = f"invoice_{invoice.pk}.pdf",
        content  = pdf_bytes,
        mimetype = 'application/pdf',
    )
    email_msg.send(fail_silently=False)


def _send_technician_welcome_email(technician, username: str, temp_password: str) -> None:
    """
    UC13, step 11 -- Send a welcome email to a newly created technician
    with their login credentials.
    NOTE: technician.email will be updated to technician.email_address
    once the Technician model field rename migration is applied.
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
            subject        = subject,
            message        = message,
            from_email     = getattr(django_settings, 'DEFAULT_FROM_EMAIL', 'noreply@example.com'),
            recipient_list = [technician.email],
            fail_silently  = False,
        )
    except Exception as exc:
        logger.error(
            "Failed to send welcome email to Technician #%s (%s): %s",
            technician.pk, technician.email, exc,
        )


def _send_client_acknowledgement_email(client_request) -> None:
    """Send an acknowledgement email to the client confirming receipt of their job request."""
    subject = "We have received your job request"
    message = (
        f"Dear {client_request.contact_name},\n\n"
        f"Thank you for submitting your job request. "
        f"We have received it and our team will be in touch shortly.\n\n"
        f"  Request reference : #{client_request.pk}\n"
        f"  Subject           : {client_request.subject}\n\n"
        f"Kind regards,\n"
        f"The TradieRM Team"
    )
    try:
        send_mail(
            subject        = subject,
            message        = message,
            from_email     = getattr(django_settings, 'DEFAULT_FROM_EMAIL', 'noreply@example.com'),
            recipient_list = [client_request.contact_email],
            fail_silently  = False,
        )
    except Exception as exc:
        logger.error(
            "Failed to send acknowledgement email for ClientRequest #%s: %s",
            client_request.pk, exc,
        )


def _send_admin_new_request_notification(client_request) -> None:
    """Notify the administrator that a new job request has arrived."""
    admin_email = getattr(django_settings, 'ADMIN_NOTIFICATION_EMAIL', None)
    if not admin_email:
        logger.warning(
            "ADMIN_NOTIFICATION_EMAIL is not configured. "
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
            subject        = subject,
            message        = message,
            from_email     = getattr(django_settings, 'DEFAULT_FROM_EMAIL', 'noreply@example.com'),
            recipient_list = [admin_email],
            fail_silently  = False,
        )
    except Exception as exc:
        logger.error(
            "Failed to send admin notification for ClientRequest #%s: %s",
            client_request.pk, exc,
        )


def _send_booking_request_email(customer, booking, booking_link: str) -> None:
    """UC4 -- Email the customer a link to the unauthenticated booking form."""
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
            subject        = subject,
            message        = message,
            from_email     = getattr(django_settings, 'DEFAULT_FROM_EMAIL', 'noreply@example.com'),
            recipient_list = [customer.email_address],
            fail_silently  = False,
        )
    except Exception as exc:
        logger.error(
            "Failed to send booking request email to Customer #%s (%s): %s",
            customer.pk, customer.email_address, exc,
        )


def _send_allocation_email_to_customer(booking) -> None:
    """UC17 -- Notify the customer that a technician has been allocated."""
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
            subject        = subject,
            message        = message,
            from_email     = getattr(django_settings, 'DEFAULT_FROM_EMAIL', 'noreply@example.com'),
            recipient_list = [customer.email_address],
            fail_silently  = False,
        )
    except Exception as exc:
        logger.error(
            "Failed to send allocation email to Customer #%s (%s): %s",
            customer.pk, customer.email_address, exc,
        )


def _send_allocation_email_to_technician(booking) -> None:
    """
    UC17 -- Notify the technician that a job has been allocated to them.
    NOTE: technician.email will be updated to technician.email_address
    once the Technician model field rename migration is applied.
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
        f"  Phone    : {customer.telephone_number}\n"
        f"  Date     : {booking.date.strftime('%d %B %Y')}\n"
        f"  Time     : {booking.time.strftime('%I:%M %p')}\n\n"
        f"Please review the full job details in TradieRM before attending.\n\n"
        f"Kind regards,\n"
        f"The TradieRM Team"
    )
    try:
        send_mail(
            subject        = subject,
            message        = message,
            from_email     = getattr(django_settings, 'DEFAULT_FROM_EMAIL', 'noreply@example.com'),
            recipient_list = [technician.email],
            fail_silently  = False,
        )
    except Exception as exc:
        logger.error(
            "Failed to send allocation email to Technician #%s (%s): %s",
            technician.pk, technician.email, exc,
        )


def _send_contact_details_email_on_failed_request(email: str) -> None:
    """Send the company's contact details when a webhook submission fails validation."""
    company_phone = getattr(django_settings, 'COMPANY_CONTACT_PHONE', 'our office number')
    company_email = getattr(django_settings, 'COMPANY_CONTACT_EMAIL', 'info@tradierm.com')

    subject = "We received your enquiry -- please contact us directly"
    message = (
        f"Thank you for reaching out.\n\n"
        f"Unfortunately we were unable to process your submission because one or more "
        f"required fields were missing.\n\n"
        f"Please contact us directly:\n\n"
        f"  Phone : {company_phone}\n"
        f"  Email : {company_email}\n\n"
        f"Kind regards,\n"
        f"The TradieRM Team"
    )
    try:
        send_mail(
            subject        = subject,
            message        = message,
            from_email     = getattr(django_settings, 'DEFAULT_FROM_EMAIL', 'noreply@example.com'),
            recipient_list = [email],
            fail_silently  = False,
        )
    except Exception as exc:
        logger.error(
            "Failed to send contact details email to '%s': %s", email, exc,
        )


def _get_client_ip(request) -> str:
    """
    Extract the originating IP address from the request.

    Checks X-Forwarded-For first to handle requests routed through a proxy
    or load balancer. Falls back to REMOTE_ADDR.
    """
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')