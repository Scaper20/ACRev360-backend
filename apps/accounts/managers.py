from django.contrib.auth.base_user import BaseUserManager


class AppUserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, username, password, **extra_fields):
        if not username:
            raise ValueError("username is required")
        # email is unique+required at the model level (PR8). Every onboarding
        # flow now collects and passes one explicitly, but this fallback stays
        # for the paths that don't go through a serializer at all (createsuperuser,
        # management commands, seed data) — this only needs to be unique, not
        # deliverable, and username already is unique, so this always is too.
        extra_fields.setdefault("email", f"{username}@placeholder.acrev360.local")
        user = self.model(username=username, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, username, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(username, password, **extra_fields)

    def create_superuser(self, username, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True")
        return self._create_user(username, password, **extra_fields)
