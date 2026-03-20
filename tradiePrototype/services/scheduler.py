"""
core/services/scheduler.py
BR6 – Auto-adjust technician timetable to include travel time between jobs.
"""

from datetime import timedelta
from django.utils import timezone
from tradiePrototype.models import Job, ScheduleBlock

DEFAULT_TRAVEL_MINUTES = 30


def get_travel_minutes(origin: str, destination: str) -> int:
    """
    Estimate driving time in minutes between two addresses.
    Replace the stub below with a Google Maps Distance Matrix call when ready.
    """
    if not origin or not destination:
        return DEFAULT_TRAVEL_MINUTES

    # TODO: integrate Google Maps Distance Matrix API
    # import googlemaps
    # gmaps = googlemaps.Client(key=settings.GOOGLE_MAPS_API_KEY)
    # result = gmaps.distance_matrix(origin, destination, mode='driving')
    # return result['rows'][0]['elements'][0]['duration']['value'] // 60

    return DEFAULT_TRAVEL_MINUTES


def schedule_job(job: Job) -> list:
    """
    US6.2 – Assign travel + job blocks to a technician's timetable.

    1. Finds the technician's last block on the same day.
    2. Calculates travel time from that block's job address (or home address).
    3. Checks for conflicts.
    4. Creates a TRAVEL block and a JOB block.

    Returns [travel_block, job_block].
    Raises ValueError on missing data or scheduling conflicts.
    """
    if not job.technician:
        raise ValueError("Job must have an assigned technician before scheduling.")
    if not job.scheduled_start or not job.scheduled_end:
        raise ValueError("Job must have scheduled_start and scheduled_end set.")

    technician = job.technician
    day_start  = job.scheduled_start.replace(hour=0, minute=0, second=0, microsecond=0)

    # Find the last block on the same day before this job's start
    previous_block = (
        ScheduleBlock.objects
        .filter(technician=technician, end_time__lte=job.scheduled_start, start_time__gte=day_start)
        .order_by('-end_time')
        .first()
    )

    origin = (
        previous_block.job.job_address if (previous_block and previous_block.job)
        else technician.home_address
    )

    travel_minutes = get_travel_minutes(origin, job.job_address)
    travel_start   = job.scheduled_start - timedelta(minutes=travel_minutes)

    # Conflict detection
    conflicts = (
        ScheduleBlock.objects
        .filter(technician=technician, start_time__lt=job.scheduled_end, end_time__gt=travel_start)
        .exclude(job=job)
    )
    if conflicts.exists():
        times = ", ".join(f"{b.start_time:%H:%M}–{b.end_time:%H:%M}" for b in conflicts[:3])
        raise ValueError(
            f"Schedule conflict for {technician} on {job.scheduled_start:%Y-%m-%d}: overlaps {times}"
        )

    # Save travel time on the job
    job.travel_time_minutes = travel_minutes
    job.save(update_fields=['travel_time_minutes'])

    travel_block = ScheduleBlock.objects.create(
        technician=technician, job=job,
        block_type=ScheduleBlock.BlockType.TRAVEL,
        start_time=travel_start, end_time=job.scheduled_start,
        notes=f"Travel to: {job.job_address}",
    )
    job_block = ScheduleBlock.objects.create(
        technician=technician, job=job,
        block_type=ScheduleBlock.BlockType.JOB,
        start_time=job.scheduled_start, end_time=job.scheduled_end,
        notes=job.title,
    )

    return [travel_block, job_block]


def get_technician_schedule(technician, date) -> list:
    """US6.1/6.3 – Return all schedule blocks for a technician on a given date."""
    day_start = timezone.make_aware(
        timezone.datetime(date.year, date.month, date.day, 0, 0, 0)
    )
    day_end = day_start + timedelta(days=1)

    return list(
        ScheduleBlock.objects
        .filter(technician=technician, start_time__gte=day_start, start_time__lt=day_end)
        .select_related('job')
        .order_by('start_time')
    )