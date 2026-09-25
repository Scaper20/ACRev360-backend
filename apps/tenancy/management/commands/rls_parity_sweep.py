"""
Prove the API behaves the same with row-level security enforced as it does with
it bypassed — the check to run before switching a deployment to the restricted
runtime role (docs/DEPLOYMENT.md section 6).

    # 1. as the OWNER role (BYPASSRLS):
    DATABASE_URL=<owner url>   python manage.py rls_parity_sweep --output owner.json
    # 2. as the RESTRICTED runtime role (NOBYPASSRLS):
    DATABASE_URL=<app url>     python manage.py rls_parity_sweep --output app.json
    # 3. compare:
    python manage.py rls_parity_sweep --compare owner.json app.json

The sweep signs in (in-process, minting a token, so no login is recorded) as one
active user per access level and issues read-only GETs against the main list and
summary endpoints, recording status code and a hash of each body. Any 5xx, or any
response that differs between the two runs, is a place where code only worked
because RLS was off — typically a related row loaded lazily after a council's
RLS context had closed (it turned up two such bugs on 2026-09-24: consultant
``registration_payer_ref`` and settlement ``consultant_name`` came back null for
platform-tier users). Only GET requests are made; nothing is written.
"""
import hashlib
import json
import sys

from django.core.management.base import BaseCommand, CommandError

PATHS = [
    "/api/v1/auth/me", "/api/v1/dashboard/summary", "/api/v1/dashboard/global", "/api/v1/reports",
    "/api/v1/payers", "/api/v1/bills", "/api/v1/payments", "/api/v1/receipts", "/api/v1/revenue-items",
    "/api/v1/debt", "/api/v1/audit", "/api/v1/settlements", "/api/v1/agents", "/api/v1/consultants",
    "/api/v1/reconciliation", "/api/v1/wards", "/api/v1/departments", "/api/v1/stakeholders",
    "/api/v1/mobile/worklist", "/api/v1/my/profile", "/api/v1/my/bills", "/api/v1/my/payments",
]


def _sweep():
    from django.conf import settings
    from rest_framework.test import APIClient

    from apps.accounts.models import AppUser
    from apps.accounts.tokens import AppTokenObtainPairSerializer

    # The test client's host isn't a real one; this is an in-process sweep.
    if "testserver" not in settings.ALLOWED_HOSTS and "*" not in settings.ALLOWED_HOSTS:
        settings.ALLOWED_HOSTS = [*settings.ALLOWED_HOSTS, "testserver"]
    settings.SECURE_SSL_REDIRECT = False

    out, seen = {}, set()
    for user in AppUser.objects.select_related("role").order_by("id"):
        level = user.access_level
        if level in seen or not user.is_active:
            continue
        seen.add(level)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {AppTokenObtainPairSerializer.get_token(user).access_token}")
        rows = {}
        for path in PATHS:
            response = client.get(path)
            # Sort list results by id so a harmless difference in tie order isn't
            # reported as a data difference; content, not ordering, is the point.
            try:
                data = json.loads(response.content)
                if isinstance(data, dict) and isinstance(data.get("results"), list):
                    data["results"] = sorted(data["results"], key=lambda r: json.dumps(r, sort_keys=True, default=str))
                canonical = json.dumps(data, sort_keys=True, default=str).encode()
            except ValueError:
                canonical = response.content
            rows[path] = [response.status_code, hashlib.sha1(canonical).hexdigest()[:12]]
        out[f"{level} ({user.username})"] = rows
    return out


class Command(BaseCommand):
    help = "Read-only API sweep for comparing behaviour with row-level security bypassed vs enforced."

    def add_arguments(self, parser):
        parser.add_argument("--output", help="Run the sweep against DATABASE_URL and save the results to this JSON file.")
        parser.add_argument("--compare", nargs=2, metavar=("OWNER_JSON", "APP_JSON"), help="Compare two saved sweeps.")

    def handle(self, *args, **opts):
        if opts["compare"]:
            with open(opts["compare"][0], encoding="utf-8") as fh:
                owner = json.load(fh)
            with open(opts["compare"][1], encoding="utf-8") as fh:
                app = json.load(fh)
            problems = []
            for role in sorted(set(owner) | set(app)):
                for path in sorted(set(owner.get(role, {})) | set(app.get(role, {}))):
                    a, b = owner.get(role, {}).get(path), app.get(role, {}).get(path)
                    if b and b[0] >= 500:
                        problems.append(f"5xx under enforced RLS: {role} {path} -> {b[0]}")
                    elif a != b:
                        problems.append(f"differs: {role} {path}: owner {a} vs app {b}")
            total = sum(len(v) for v in owner.values())
            self.stdout.write(f"{len(owner)} roles, {total} requests compared")
            if problems:
                for problem in problems:
                    self.stderr.write(self.style.ERROR(problem))
                sys.exit(1)
            self.stdout.write(self.style.SUCCESS("Identical: enforcing RLS changes no response."))
            return
        if not opts["output"]:
            raise CommandError("Pass --output FILE to sweep, or --compare OWNER_JSON APP_JSON.")
        results = _sweep()
        with open(opts["output"], "w", encoding="utf-8") as fh:
            json.dump(results, fh, indent=1, sort_keys=True)
        errors = sum(1 for rows in results.values() for status, _ in rows.values() if status >= 500)
        self.stdout.write(f"Swept {len(results)} roles x {len(PATHS)} endpoints; {errors} server error(s). Saved to {opts['output']}")
