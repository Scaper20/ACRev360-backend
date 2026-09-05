"""
Point-in-time JSON backup of a council's `department` and `council_revenue_item`
tables, plus every row that hangs off a revenue item and would therefore be lost
if the item were deleted: `rate_schedule`, `rate_band` and `rate_band`'s
`rate_tier` children.

Those three dependent tables are in scope deliberately. `rate_band` is CASCADE
off `council_revenue_item`, so clearing revenue items silently takes the whole
gazette band set with it; a backup of the two named tables alone would restore
the items but not the schedules that price them, which is not a backup of
anything useful.

Both target tables are under FORCE ROW LEVEL SECURITY (see apps/common/db.py),
so every read here runs inside `council_context` — without it Postgres filters
each SELECT down to zero rows and the command would cheerfully write an empty
backup file.
"""

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.revenue.models import CouncilRevenueItem, RateBand, RateSchedule, RateTier
from apps.tenancy.context import council_context
from apps.tenancy.models import Council, Department

DEFAULT_BACKUP_DIR = Path("docs/backups")


def _json_default(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"not JSON serialisable: {type(value)!r}")


class Command(BaseCommand):
    help = "Back up a council's departments, revenue items, rate schedules, bands and tiers to JSON."

    def add_arguments(self, parser):
        parser.add_argument("--council", default="KAC", help="Council code to back up (default: KAC).")
        parser.add_argument(
            "--out",
            default=None,
            help="Destination file. Defaults to docs/backups/<COUNCIL>_departments_revenue_<timestamp>.json",
        )

    def handle(self, *args, **options):
        council_code = options["council"]
        try:
            council = Council.objects.get(council_code=council_code)
        except Council.DoesNotExist:
            raise CommandError(f"No council with code {council_code!r}.")

        payload = self._collect(council)

        out_path = options["out"]
        if out_path:
            out_path = Path(out_path)
        else:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            out_path = DEFAULT_BACKUP_DIR / f"{council_code}_departments_revenue_{stamp}.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(payload, indent=2, default=_json_default, ensure_ascii=False),
            encoding="utf-8",
        )

        counts = payload["counts"]
        self.stdout.write(
            self.style.SUCCESS(
                f"Backed up {council_code}: "
                f"{counts['departments']} departments, {counts['revenue_items']} revenue items, "
                f"{counts['rate_schedules']} rate schedules, {counts['rate_bands']} bands, "
                f"{counts['rate_tiers']} tiers -> {out_path}"
            )
        )
        return str(out_path)

    def _collect(self, council):
        """Read every in-scope row inside one RLS-scoped transaction."""
        with council_context(council.id):
            departments = [
                {
                    "id": d.id,
                    "department_name": d.department_name,
                    "department_code": d.department_code,
                    "head_name": d.head_name,
                    "head_phone": d.head_phone,
                    "legal_basis": d.legal_basis,
                }
                for d in Department.objects.filter(council=council).order_by("id")
            ]

            items = list(
                CouncilRevenueItem.objects.filter(council=council)
                .select_related("category", "template")
                .order_by("id")
            )
            revenue_items = [
                {
                    "id": i.id,
                    "template_code": i.template.harmonised_code if i.template_id else None,
                    "harmonised_code": i.harmonised_code,
                    "item_name": i.item_name,
                    "category": i.category.name,
                    "unit_of_charge": i.unit_of_charge,
                    "is_active": i.is_active,
                    "department_id": i.department_id,
                    "bye_law_reference": i.bye_law_reference,
                    "bye_law_description": i.bye_law_description,
                }
                for i in items
            ]

            item_ids = [i.id for i in items]
            rate_schedules = [
                {
                    "id": s.id,
                    "council_revenue_item_id": s.council_revenue_item_id,
                    "rate_amount": s.rate_amount,
                    "effective_from": s.effective_from,
                    "effective_to": s.effective_to,
                }
                for s in RateSchedule.objects.filter(council_revenue_item_id__in=item_ids).order_by("id")
            ]

            bands = list(RateBand.objects.filter(council_revenue_item_id__in=item_ids).order_by("id"))
            rate_bands = [
                {
                    "id": b.id,
                    "council_revenue_item_id": b.council_revenue_item_id,
                    "label": b.label,
                    "sort_order": b.sort_order,
                    "rate_mode": b.rate_mode,
                    "flat_amount": b.flat_amount,
                    "min_amount": b.min_amount,
                    "max_amount": b.max_amount,
                    "effective_from": b.effective_from,
                    "effective_to": b.effective_to,
                }
                for b in bands
            ]
            rate_tiers = [
                {
                    "id": t.id,
                    "band_id": t.band_id,
                    "label": t.label,
                    "amount": t.amount,
                    "sort_order": t.sort_order,
                }
                for t in RateTier.objects.filter(band_id__in=[b.id for b in bands]).order_by("id")
            ]

        return {
            "backed_up_at": datetime.now().isoformat(timespec="seconds"),
            "council": {"id": council.id, "council_code": council.council_code, "council_name": council.council_name},
            "counts": {
                "departments": len(departments),
                "revenue_items": len(revenue_items),
                "rate_schedules": len(rate_schedules),
                "rate_bands": len(rate_bands),
                "rate_tiers": len(rate_tiers),
            },
            "departments": departments,
            "revenue_items": revenue_items,
            "rate_schedules": rate_schedules,
            "rate_bands": rate_bands,
            "rate_tiers": rate_tiers,
        }
