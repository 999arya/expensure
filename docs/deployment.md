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
   - `TELEGRAM_BOT_TOKEN`: from @BotFather (for the Telegram bot).
   - `TELEGRAM_CLIENT_ID`: from @BotFather → Login Widget (for OIDC login).
   - `TELEGRAM_CLIENT_SECRET`: from @BotFather → Login Widget.
   - `TELEGRAM_WEBHOOK_URL`: your app's HTTPS URL + `/telegram/webhook/` (e.g., `https://expensure.onrender.com/telegram/webhook/`).
   - `TELEGRAM_WEBHOOK_SECRET`: a random string (e.g., `openssl rand -hex 32`).
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

## Telegram Bot Setup (Phase 1: Login Only)

After deployment:

1. In @BotFather, create or select your bot and copy the **Bot Token** → `TELEGRAM_BOT_TOKEN`.
2. In @BotFather → **Bot Settings → Login Widget**, add your app's domain (e.g., `https://expensure.onrender.com`) to **Allowed URLs**. Copy the **Client ID** and **Client Secret** → `TELEGRAM_CLIENT_ID`, `TELEGRAM_CLIENT_SECRET`.
3. Set `TELEGRAM_WEBHOOK_URL` to `https://<your-app>.onrender.com/telegram/webhook/`.
4. Generate `TELEGRAM_WEBHOOK_SECRET` with `openssl rand -hex 32`.
5. Run `python manage.py telegram_setup` to register the webhook with Telegram.
6. Verify with `python manage.py telegram_status`.

The login button will appear on `/accounts/login/` and `/accounts/signup/` when all Telegram settings are configured.

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
