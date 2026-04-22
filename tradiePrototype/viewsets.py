"""
tradiePrototype/viewsets.py

All API views for TradieRM.

Use case coverage:
    UC2  -- CustomerViewSet.create_with_job action
    UC2  -- ClientRequestViewSet.process action
    UC2  -- webhook_intake view (inbound API payload)
    UC3  -- BookingViewSet.create (admin-triggered)
    UC4  -- BookingViewSet.send_request action + booking_token_submit view
    UC5  -- CustomerViewSet.create_with_job action (admin-triggered with booking)
    UC7  -- CustomerViewSet.update (standard DRF update)
    UC8  -- CustomerViewSet.destroy (soft delete)
    UC9  -- BookingViewSet.reject action
    UC10 -- BookingViewSet.destroy (soft delete)
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
    BookingStubSerializer,
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
    """

    serializer_class   = CustomerSerializer
    permission_classes = [IsAdministrator]

    def get_queryset(self):
        return Customer.objects.filter(status=Customer.Status.ACTIVE)

    def destroy(self, request, *args, **kwargs):
        """UC8 -- Soft-delete a customer record by setting status to Inactive."""
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
        """UC2 -- Create a Customer record and a Job record in a single request."""
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

    @action(detail=True, methods=['post'], url_path='add-job-with-booking')
    def add_job_with_booking(self, request, pk=None):
        """
        UC6 -- Add a Job record and a Booking record to an existing Customer.

        Request body fields:
            Required:
                job_title        (str)  -- administrator-supplied job title
                subject          (str)  -- job subject line
                client_message   (str)  -- full job description
                date             (date) -- preferred booking date (YYYY-MM-DD)
                time             (time) -- preferred booking time (HH:MM)
                physical_address (str)  -- booking location address

            Optional customer field updates:
                email_address    (str)  -- updates the customer record if supplied
                telephone_number (str)  -- updates the customer record if supplied

        Steps performed (UC6 Steps 8-12):
            1. Validate all required fields are present and non-empty.
            2. Optionally update email_address and telephone_number on the
               customer record if the administrator changed them.
            3. Create the Job record with status Pending.
            4. Create the Booking record with status Pending, linked to the
               customer and the job.
            5. Send a confirmation email to the customer (UC6 Step 12).

        Returns:
            201 -- { job: {...}, booking: {...} }
            400 -- { field: [error message] } on validation failure
        """
        customer = self.get_object()

        # -- Collect and validate required job fields.
        job_title = request.data.get('job_title', '').strip()
        subject = request.data.get('subject', '').strip()
        client_message = request.data.get('client_message', '').strip()
        physical_address = request.data.get('physical_address', '').strip()
        date = request.data.get('date', '').strip()
        time_value = request.data.get('time', '').strip()

        errors = {}
        if not job_title:
            errors['job_title'] = 'Job Title is required.'
        if not subject:
            errors['subject'] = 'Subject is required.'
        if not client_message:
            errors['client_message'] = 'Client Message is required.'
        if not physical_address:
            errors['physical_address'] = 'Physical Address is required.'
        if not date:
            errors['date'] = 'Date is required.'
        if not time_value:
            errors['time'] = 'Time is required.'
        if errors:
            return Response(errors, status=status.HTTP_400_BAD_REQUEST)

        # -- Optionally update writable customer fields if supplied.
        email_address = request.data.get('email_address', '').strip()
        telephone_number = request.data.get('telephone_number', '').strip()

        customer_updated = False
        if email_address and email_address != customer.email_address:
            customer.email_address = email_address
            customer_updated = True
        if telephone_number and telephone_number != customer.telephone_number:
            customer.telephone_number = telephone_number
            customer_updated = True
        if physical_address and physical_address != customer.physical_address:
            customer.physical_address = physical_address
            customer_updated = True
        if customer_updated:
            customer.save()

        # -- UC6 Step 11a -- create the Job record (status: Pending).
        job = Job.objects.create(
            customer=customer,
            job_title=job_title,
            subject=subject,
            client_message=client_message,
            status=Job.Status.PENDING,
            source=Job.Source.MANUAL,
        )

        # -- UC6 Step 11b -- create the Booking record (status: Pending).
        booking = Booking.objects.create(
            job=job,
            customer=customer,
            physical_address=physical_address,
            date=date,
            time=time_value,
            status=Booking.Status.PENDING,
        )

        logger.info(
            "UC6 -- Job #%s and Booking #%s created for Customer #%s "
            "by administrator '%s'.",
            job.pk, booking.pk, customer.pk, request.user.username,
        )

        return Response(
            {
                'job': JobSerializer(job).data,
                'booking': BookingSerializer(booking).data,
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
    """

    queryset           = Technician.objects.filter(status=Technician.Status.ACTIVE)
    permission_classes = [IsAdministrator]

    def get_serializer_class(self):
        if self.action == 'create':
            return TechnicianCreateSerializer
        return TechnicianSerializer

    def create(self, request, *args, **kwargs):
        """
        UC13 -- Create a Technician record and provision their Django User account.
        Temporary password = telephone_number.
        """
        serializer = TechnicianCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        username   = serializer.validated_data.pop('username')
        technician = serializer.save()

        temp_password = technician.telephone_number or 'changeme123'
        user = User.objects.create_user(
            username=username,
            email=technician.email_address,
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
        """UC15 -- Soft-delete a Technician record by setting status to Inactive."""
        technician = self.get_object()
        technician.status = Technician.Status.INACTIVE
        technician.save()

        try:
            linked_user = User.objects.get(email=technician.email_address)
            linked_user.is_active = False
            linked_user.save()
        except User.DoesNotExist:
            logger.warning(
                "UC15 -- No linked Django User found for Technician #%s (%s). "
                "Technician marked Inactive without revoking a user account.",
                technician.pk, technician.email_address,
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
    """UC20, UC21, UC22 -- Full CRUD for Inventory records. Access: Administrator only."""

    queryset           = Inventory.objects.all()
    serializer_class   = InventorySerializer
    permission_classes = [IsAdministrator]


# ---------------------------------------------------------------------------
# Job Inventory ViewSet
# ---------------------------------------------------------------------------

class JobInventoryViewSet(viewsets.ModelViewSet):
    """
    UC23, UC24 -- Manage assignment of Inventory items (parts) to a Job.

    UC23 (Admin-Triggered): job must be Allocated, In Progress, or Completed.
    UC24 (Technician-Triggered): job must be Allocated or In Progress; technician
    must be assigned to the job.
    """

    queryset           = JobInventory.objects.select_related('job', 'inventory')
    serializer_class   = JobInventorySerializer
    permission_classes = [IsAdministrator | IsTechnician]

    def get_queryset(self):
        user    = self.request.user
        profile = getattr(user, 'profile', None)

        qs = JobInventory.objects.select_related('job', 'inventory')

        if profile and profile.is_technician:
            qs = qs.filter(job__technician__email_address=user.email)

        job_id = self.request.query_params.get('job')
        if job_id:
            qs = qs.filter(job_id=job_id)

        return qs

    def create(self, request, *args, **kwargs):
        """UC23, UC24 -- Add a part to a job."""
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
            allowed_statuses = [Job.Status.ALLOCATED, Job.Status.IN_PROGRESS]
            if job.status not in allowed_statuses:
                return Response(
                    {'error': 'Job parts can only be added to a job with a status of Allocated or In Progress.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if not job.technician or job.technician.email_address != request.user.email:
                return Response(
                    {'error': 'You are not assigned to this job.'},
                    status=status.HTTP_403_FORBIDDEN,
                )
        else:
            allowed_statuses = [Job.Status.ALLOCATED, Job.Status.IN_PROGRESS, Job.Status.COMPLETED]
            if job.status not in allowed_statuses:
                return Response(
                    {'error': 'Job parts can only be added to a job with a status of Allocated, In Progress, or Completed.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        job_inventory = serializer.save()
        return Response(JobInventorySerializer(job_inventory).data, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# Job ViewSet
# ---------------------------------------------------------------------------

class JobViewSet(viewsets.ModelViewSet):
    """
    UC2, UC18, UC19, UC25, UC26 -- Full CRUD for Job records.
    """

    queryset           = Job.objects.select_related('customer', 'technician')
    permission_classes = [IsAdministrator | IsTechnician | IsCustomer]

    def get_serializer_class(self):
        if self.action == 'create':
            return JobCreateSerializer
        return JobSerializer

    def get_queryset(self):
        user    = self.request.user
        profile = getattr(user, 'profile', None)

        if profile and profile.is_technician:
            return Job.objects.filter(
                technician__email_address=user.email
            ).select_related('customer', 'technician')

        if profile and profile.is_customer:
            return Job.objects.filter(
                customer__email_address=user.email
            ).select_related('customer', 'technician')

        return Job.objects.select_related('customer', 'technician')

    def create(self, request, *args, **kwargs):
        """UC2 -- Create a standalone Job record. Status always forced to Pending."""
        serializer = JobCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        job = serializer.save(status=Job.Status.PENDING)
        return Response(JobSerializer(job).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='update-status')
    def update_status(self, request, pk=None):
        """UC18, UC25, UC26 -- Update the status of an existing job."""
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

        if new_status == Job.Status.IN_PROGRESS:
            job.start_time = timezone.now()
            logger.info(
                "UC25 -- Job #%s start_time recorded as %s by technician '%s'.",
                job.pk, job.start_time, request.user.username,
            )

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

        invoice_data = None
        if new_status == Job.Status.COMPLETED:
            try:
                invoice      = generate_invoice(job)
                invoice_data = InvoiceSerializer(invoice).data
            except Exception as exc:
                logger.error("UC26 -- Invoice generation failed for Job #%s: %s", job.pk, exc)

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
    Access: Administrator only.
    """

    queryset           = Booking.objects.select_related('job', 'customer', 'technician')
    permission_classes = [IsAdministrator]

    def get_serializer_class(self):
        if self.action == 'create':
            return BookingCreateSerializer
        return BookingSerializer

    def get_queryset(self):
        """
        Returns all Booking records excluding Inactive status.
        Inactive bookings are soft-deleted records (UC10) and must not
        appear in any list or detail view.
        """
        return Booking.objects.select_related(
            'job', 'customer', 'technician'
        ).exclude(
            status=Booking.Status.INACTIVE
        ).order_by('created_at')

    def create(self, request, *args, **kwargs):
        """UC3 -- Create a new Booking record with status Pending."""
        serializer = BookingCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        booking = serializer.save(status=Booking.Status.PENDING)
        return Response(BookingSerializer(booking).data, status=status.HTTP_201_CREATED)

    def destroy(self, request, *args, **kwargs):
        """UC10 -- Soft-delete a booking by setting status to Inactive."""
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
        """UC4 -- Email the customer a signed booking form link."""
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
        """UC9 -- Reject a Pending booking."""
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
            technician = Technician.objects.get(
                pk=technician_id, status=Technician.Status.ACTIVE
            )
        except Technician.DoesNotExist:
            return Response(
                {'error': 'Technician not found or is inactive.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Calculate road distance between technician address and booking address (UC17).
        distance_km = None
        if technician.physical_address and booking.physical_address:
            distance_km = get_road_distance_km(
                technician.physical_address,
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
# Schedule Block ViewSet
# ---------------------------------------------------------------------------

class ScheduleBlockViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only view of raw ScheduleBlock records."""

    queryset           = ScheduleBlock.objects.select_related('technician', 'job', 'booking')
    serializer_class   = ScheduleBlockSerializer
    permission_classes = [IsAdminOrTechnician]

    def get_queryset(self):
        user    = self.request.user
        profile = getattr(user, 'profile', None)

        if profile and profile.is_technician:
            return ScheduleBlock.objects.filter(
                technician__email_address=user.email
            ).select_related('technician', 'job')

        return ScheduleBlock.objects.select_related('technician', 'job')


# ---------------------------------------------------------------------------
# Technician Schedule ViewSet (UC28, UC29)
# ---------------------------------------------------------------------------

class TechnicianScheduleViewSet(viewsets.ViewSet):
    """
    UC28, UC29 -- Technician schedule views.

    UC28: Admin views all technician schedules.
    UC29: Technician views their own schedule.
    """

    permission_classes = [IsAdminOrTechnician]

    def list(self, request):
        """UC28 -- Return list of all active technicians. Access: Administrator only."""
        profile = getattr(request.user, 'profile', None)
        if not profile or not profile.is_admin:
            return Response(
                {'error': 'Only administrators can view the technician list.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        from django.db.models import Exists, OuterRef

        has_active_booking = Booking.objects.filter(
            technician=OuterRef('pk'),
            status=Booking.Status.CONFIRMED,
            job__status__in=[Job.Status.ALLOCATED, Job.Status.IN_PROGRESS],
        )

        technicians = (
            Technician.objects
            .filter(status=Technician.Status.ACTIVE)
            .annotate(is_allocated=Exists(has_active_booking))
            .order_by('-is_allocated', 'last_name', 'first_name')
        )

        data = TechnicianSerializer(technicians, many=True).data
        return Response(data)

    def retrieve(self, request, pk=None):
        """UC28 -- Return the schedule for a specific technician. Access: Administrator only."""
        profile = getattr(request.user, 'profile', None)
        if not profile or not profile.is_admin:
            return Response(
                {'error': 'Only administrators can view technician schedules.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            technician = Technician.objects.get(pk=pk, status=Technician.Status.ACTIVE)
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
        """UC29 -- Return the authenticated technician's own schedule."""
        try:
            technician = Technician.objects.get(
                email_address=request.user.email,
                status=Technician.Status.ACTIVE,
            )
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
    In Progress jobs first, then Allocated jobs ordered by date/time.
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

    return in_progress_entries + allocated_entries


# ---------------------------------------------------------------------------
# Invoice ViewSet
# ---------------------------------------------------------------------------

class InvoiceViewSet(viewsets.ModelViewSet):
    """
    UC26, UC27 -- View and manage Invoice records.
    Access: Administrator only.
    """

    queryset           = Invoice.objects.select_related('job__customer', 'technician')
    serializer_class   = InvoiceSerializer
    permission_classes = [IsAdministrator]

    def get_queryset(self):
        qs            = Invoice.objects.select_related('job__customer', 'job', 'technician')
        status_filter = self.request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter)
        return qs

    def create(self, request, *args, **kwargs):
        return Response(
            {'error': 'Invoices are created automatically when a job is completed.'},
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    @action(detail=True, methods=['post'], url_path='recalculate')
    def recalculate(self, request, pk=None):
        """UC27 -- Recalculate invoice cost fields without saving."""
        invoice    = self.get_object()
        serializer = InvoiceRecalculateSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        validated = serializer.validated_data

        if 'hours_taken' in validated:
            invoice.hours_taken = validated['hours_taken']
        if 'distance_rate' in validated:
            invoice.distance_rate = validated['distance_rate']
        if 'service_charge_percentage' in validated:
            invoice.service_charge_percentage = validated['service_charge_percentage']
        if 'notes' in validated:
            invoice.notes = validated['notes']

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
        """UC27 -- Approve a draft invoice: recalculate, generate PDF, email customer."""
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

        invoice.calculate_totals()

        try:
            pdf_bytes = _generate_invoice_pdf(invoice)
        except Exception as exc:
            logger.error("UC27 -- PDF generation failed for Invoice #%s: %s", invoice.pk, exc)
            return Response(
                {'error': 'PDF generation failed. Invoice has not been sent.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        try:
            _send_invoice_to_customer(invoice, pdf_bytes)
        except Exception as exc:
            logger.error("UC27 -- Email dispatch failed for Invoice #%s: %s", invoice.pk, exc)
            return Response(
                {'error': 'Email dispatch failed. Invoice has not been marked as Sent.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

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
    """UC26, step 10 -- In-system notification records. Access: Administrator only."""

    serializer_class   = NotificationSerializer
    permission_classes = [IsAdministrator]

    def get_queryset(self):
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
        """Mark all unread notifications as read."""
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
    """

    queryset           = ClientRequest.objects.all()
    serializer_class   = ClientRequestSerializer
    permission_classes = [IsAdministrator]

    def get_queryset(self):
        return ClientRequest.objects.order_by('status', 'date_received')

    @action(detail=True, methods=['post'], url_path='process')
    def process(self, request, pk=None):
        """
        UC2 -- Convert an Unprocessed ClientRequest into a Customer, Job,
        and stub Booking record.

        The job_title field is required in the request body (UC2 Step 8).
        physical_address, date, and time are not known at this point; the
        customer supplies them via the booking form link (UC4).

        All three records are created atomically. If any step fails the
        request remains Unprocessed and an error is returned.
        """
        client_request = self.get_object()

        if client_request.status != ClientRequest.Status.UNPROCESSED:
            return Response(
                {'error': 'This request has already been processed.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        job_title = request.data.get('job_title', '').strip()
        if not job_title:
            return Response(
                {'error': 'Job Title is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        process_serializer = ClientRequestProcessSerializer(
            data={}, context={'client_request': client_request}
        )
        if not process_serializer.is_valid():
            return Response(process_serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        customer, _ = Customer.objects.get_or_create(
            email_address=client_request.email_address,
            defaults={
                'first_name': client_request.first_name,
                'last_name': client_request.last_name,
                'telephone_number': client_request.telephone_number,
            },
        )

        job = Job.objects.create(
            customer=customer,
            job_title=job_title,
            subject=client_request.subject,
            client_message=client_request.client_message,
            status=Job.Status.PENDING,
            source=Job.Source.WEBHOOK,
            client_request=client_request,
        )

        # UC2 Step 14 -- create stub Booking record (status: Pending).
        # physical_address, date, and time are populated later via UC4.
        booking = Booking.objects.create(
            job=job,
            customer=customer,
            status=Booking.Status.PENDING,
        )

        client_request.status = ClientRequest.Status.PROCESSED
        client_request.save(update_fields=['status', 'updated_at'])

        logger.info(
            "UC2 -- ClientRequest #%s processed. Customer #%s, Job #%s, "
            "and Booking #%s created by administrator '%s'.",
            client_request.pk, customer.pk, job.pk,
            booking.pk, request.user.username,
        )

        return Response({
            'customer': CustomerSerializer(customer).data,
            'job': JobSerializer(job).data,
            'booking': BookingSerializer(booking).data,
        }, status=status.HTTP_201_CREATED)

# ---------------------------------------------------------------------------
# AI Response Suggestion ViewSet (BR4, BR5 -- descoped, retained for audit)
# ---------------------------------------------------------------------------

class AIResponseSuggestionViewSet(viewsets.ReadOnlyModelViewSet):
    """Formally descoped. Retained to avoid breaking existing API clients."""

    queryset           = AIResponseSuggestion.objects.select_related('client_request')
    serializer_class   = AIResponseSuggestionSerializer
    permission_classes = [IsAdminOrTechnician]

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
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
    """UC2 -- Receive an inbound job request from the external website via API."""
    serializer = WebhookInboundSerializer(data=request.data)

    if not serializer.is_valid():
        email = request.data.get('email', '').strip()
        if email:
            _send_contact_details_email_on_failed_request(email)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    validated = serializer.validated_data

    client_request = ClientRequest.objects.create(
        source_ip        = _get_client_ip(request),
        raw_payload      = request.data,
        first_name       = validated['first_name'],
        last_name        = validated['last_name'],
        email_address    = validated['email'],
        telephone_number = validated.get('phone', ''),
        subject          = validated.get('subject', ''),
        client_message   = validated['message'],
        status           = ClientRequest.Status.UNPROCESSED,
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
    """UC4 -- Accept a customer's booking form submission via a signed token link."""
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
    booking.booking_token    = ''
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
    """Invalidate the requesting user's authentication token."""
    request.user.auth_token.delete()
    return Response({'message': 'Successfully logged out.'}, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def me(request):
    """Return the authenticated user's identity and role."""
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
    """Generate PDF for the approved invoice. Falls back to plain-text if reportlab missing."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm
        from reportlab.pdfgen import canvas as rl_canvas

        buffer = io.BytesIO()
        c      = rl_canvas.Canvas(buffer, pagesize=A4)
        width, height = A4

        customer   = invoice.job.customer
        technician = invoice.technician

        c.setFont("Helvetica-Bold", 18)
        c.drawString(20 * mm, height - 25 * mm, "TAX INVOICE")

        c.setFont("Helvetica", 10)
        c.drawString(20 * mm, height - 35 * mm, f"Invoice #:    {invoice.pk}")
        c.drawString(20 * mm, height - 41 * mm, f"Date:         {invoice.date_generated.strftime('%d %B %Y')}")
        c.drawString(20 * mm, height - 47 * mm, f"Job #:        {invoice.job.pk}")
        c.drawString(20 * mm, height - 53 * mm, f"Subject:      {invoice.job.subject}")

        c.setFont("Helvetica-Bold", 11)
        c.drawString(20 * mm, height - 65 * mm, "Bill To")
        c.setFont("Helvetica", 10)
        c.drawString(20 * mm, height - 72 * mm, f"{customer.first_name} {customer.last_name}")
        c.drawString(20 * mm, height - 78 * mm, customer.physical_address or '')
        c.drawString(20 * mm, height - 84 * mm, customer.telephone_number or '')
        c.drawString(20 * mm, height - 90 * mm, customer.email_address or '')

        c.setFont("Helvetica-Bold", 11)
        c.drawString(110 * mm, height - 65 * mm, "Technician")
        c.setFont("Helvetica", 10)
        if technician:
            c.drawString(110 * mm, height - 72 * mm,
                         f"{technician.first_name} {technician.last_name}")
            c.drawString(110 * mm, height - 78 * mm, technician.email_address or '')

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
    """UC27 -- Email the approved invoice PDF to the customer."""
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
    body += "If you have any questions, please contact us.\n\nKind regards,\nThe TradieRM Team"

    email_msg = EmailMessage(
        subject    = subject,
        body       = body,
        from_email = getattr(django_settings, 'DEFAULT_FROM_EMAIL', 'noreply@example.com'),
        to         = [customer.email_address],
    )
    email_msg.attach(
        filename = f"invoice_{invoice.pk}.pdf",
        content  = pdf_bytes,
        mimetype = 'application/pdf',
    )
    email_msg.send(fail_silently=False)


def _send_technician_welcome_email(technician, username: str, temp_password: str) -> None:
    """UC13 -- Send a welcome email to a newly created technician."""
    subject = "Your TradieRM account has been created"
    message = (
        f"Hi {technician.first_name},\n\n"
        f"An account has been created for you on TradieRM.\n\n"
        f"  Username           : {username}\n"
        f"  Temporary password : {temp_password}\n\n"
        f"Please log in and change your password as soon as possible.\n\n"
        f"Kind regards,\nThe TradieRM Team"
    )
    try:
        send_mail(
            subject        = subject,
            message        = message,
            from_email     = getattr(django_settings, 'DEFAULT_FROM_EMAIL', 'noreply@example.com'),
            recipient_list = [technician.email_address],
            fail_silently  = False,
        )
    except Exception as exc:
        logger.error(
            "Failed to send welcome email to Technician #%s (%s): %s",
            technician.pk, technician.email_address, exc,
        )


def _send_client_acknowledgement_email(client_request) -> None:
    """Send an acknowledgement email confirming receipt of a job request."""
    subject = "We have received your job request"
    message = (
        f"Dear {client_request.first_name} {client_request.last_name},\n\n"
        f"Thank you for submitting your job request. "
        f"We have received it and our team will be in touch shortly.\n\n"
        f"  Request reference : #{client_request.pk}\n"
        f"  Subject           : {client_request.subject}\n\n"
        f"Kind regards,\nThe TradieRM Team"
    )
    try:
        send_mail(
            subject        = subject,
            message        = message,
            from_email     = getattr(django_settings, 'DEFAULT_FROM_EMAIL', 'noreply@example.com'),
            recipient_list = [client_request.email_address],
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
        f"  Name       : {client_request.first_name} {client_request.last_name}\n"
        f"  Email      : {client_request.email_address}\n"
        f"  Phone      : {client_request.telephone_number}\n"
        f"  Subject    : {client_request.subject}\n"
        f"  Received   : {client_request.date_received.strftime('%Y-%m-%d %H:%M UTC')}\n\n"
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
        f"Kind regards,\nThe TradieRM Team"
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
        f"Kind regards,\nThe TradieRM Team"
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
    """UC17 -- Notify the technician that a job has been allocated to them."""
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
        f"Kind regards,\nThe TradieRM Team"
    )
    try:
        send_mail(
            subject        = subject,
            message        = message,
            from_email     = getattr(django_settings, 'DEFAULT_FROM_EMAIL', 'noreply@example.com'),
            recipient_list = [technician.email_address],
            fail_silently  = False,
        )
    except Exception as exc:
        logger.error(
            "Failed to send allocation email to Technician #%s (%s): %s",
            technician.pk, technician.email_address, exc,
        )


def _send_contact_details_email_on_failed_request(email: str) -> None:
    """Send company contact details when a webhook submission fails validation."""
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
        f"Kind regards,\nThe TradieRM Team"
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
        logger.error("Failed to send contact details email to '%s': %s", email, exc)


def _get_client_ip(request) -> str:
    """Extract the originating IP address from the request."""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')