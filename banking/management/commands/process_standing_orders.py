from django.core.management.base import BaseCommand
from django.utils import timezone

from banking import services


class Command(BaseCommand):
    help = (
        "Execute all due standing orders. Intended to run once a day from a "
        "cron job or systemd timer."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--date",
            help="Process orders as of this date (YYYY-MM-DD) instead of today. "
                 "Useful for testing or catching up.",
        )

    def handle(self, *args, **options):
        as_of = None
        if options.get("date"):
            as_of = timezone.datetime.strptime(options["date"], "%Y-%m-%d").date()

        result = services.process_due_standing_orders(
            as_of=as_of, stdout=lambda m: self.stdout.write(m)
        )
        self.stdout.write(self.style.SUCCESS(
            f"Standing orders done — {result['processed']} run(s): "
            f"{result['succeeded']} succeeded, {result['failed']} skipped/failed."
        ))
