# Render Free + Neon Free

The [Render blueprint](../render.yaml) provisions only a free Python web service.
The database is a separate Neon Free project; no expiring Render database is created.

## Account setup

1. Sign in to Neon and create a Free project named `expensure`. Prefer Singapore
   if available, or the closest region to Render Singapore.
2. Copy the pooled PostgreSQL connection URL from Neon's Connect panel, retaining
   `sslmode=require`. Keep it private.
3. In Render, create a Blueprint from the `999arya/expensure` GitHub repository.
4. Confirm the web service plan is Free. Provide these secret values when prompted:
   - `DATABASE_URL`: the Neon connection URL.
   - `DJANGO_SECRET_KEY`: a random secret at least 50 characters long.
   - `GROQ_API_KEY`: your Groq key (can be added later for optional AI).
5. Deploy. The build installs dependencies and collects static assets. Startup
   runs migrations before launching Gunicorn. Render's assigned hostname is added
   to Django's allowed hosts and trusted CSRF origins automatically.

Never commit the local environment file, database URL, or keys. The cloud database
starts empty; local SQLite accounts and financial records are not uploaded.

## Validation

Check the HTTPS homepage, registration, login, dashboard, static assets, and a goal
action plan. A target review needs three completed months of recorded history.
Confirm Render reports the latest commit as live and Neon retains database records
across a web-service restart. Keep database backups before future schema changes.

## Free-plan limitations

- Render can sleep when idle, so the first request may take longer.
- Neon has storage and compute quotas. Stay on the Free plan in both dashboards.
- Render Free blocks SMTP ports. Password reset is explicitly unavailable until an
  HTTPS email provider is integrated; `PASSWORD_RESET_ENABLED=false` prevents false
  delivery messages. Signup and login work without email delivery.
- Daily reports and insights are not scheduled by this blueprint. On-demand
  reports and coaching remain available. Add scheduling separately if needed.
- A custom domain is optional. For one, also set `DJANGO_ALLOWED_HOSTS` and
  `DJANGO_CSRF_TRUSTED_ORIGINS` to its hostname and HTTPS origin respectively.

## References

- [Render Django deployment](https://render.com/docs/deploy-django)
- [Render Free limitations](https://render.com/docs/free)
- [Neon Free plan](https://neon.com/pricing)
