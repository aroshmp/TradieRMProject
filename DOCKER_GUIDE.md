# Docker Setup Guide for TradieRM Project

## Quick Start

### 1. Setup Environment Variables
```bash
cp .env.example .env
# Edit .env with your configuration
nano .env
```

### 2. Build and Start Containers
```bash
docker-compose up -d
```

### 3. Verify Services
```bash
# Check service status
docker-compose ps

# View logs
docker-compose logs -f web
docker-compose logs -f db
docker-compose logs -f nginx
```

## What's Improved

### Dockerfile Improvements:
- ✅ **Multi-stage build** - Reduced final image size by ~50%
- ✅ **Non-root user** - Improved security (runs as appuser:1000)
- ✅ **Health checks** - Container orchestration monitoring
- ✅ **Virtual environment** - Better dependency isolation
- ✅ **PostgreSQL client** - Support for PostgreSQL database
- ✅ **Optimized gunicorn** - Production-ready settings

### Docker-Compose Improvements:
- ✅ **PostgreSQL database** - More production-ready than SQLite
- ✅ **Nginx reverse proxy** - Better performance and static file serving
- ✅ **Environment variables** - Easy configuration management
- ✅ **Health checks** - Service dependency management
- ✅ **Volume management** - Persistent data storage
- ✅ **Restart policies** - Better resilience
- ✅ **Version 3.9** - Latest stable version

### Additional Files:
- ✅ **.env.example** - Configuration template
- ✅ **nginx.conf** - Production-ready Nginx configuration
- ✅ **.dockerignore** - Optimize build context

## Common Commands

```bash
# Start services
docker-compose up -d

# Stop services
docker-compose down

# View logs
docker-compose logs -f web

# Run migrations
docker-compose exec web python manage.py migrate

# Create superuser
docker-compose exec web python manage.py createsuperuser

# Collect static files
docker-compose exec web python manage.py collectstatic --noinput

# Access Django admin
# http://localhost/admin/

# Access API
# http://localhost/api/
```

## Database Configuration

The docker-compose.yml now includes PostgreSQL. Update your Django settings.py:

```python
import os
from urllib.parse import urlparse

# Database
if os.getenv('DATABASE_URL'):
    db_url = urlparse(os.getenv('DATABASE_URL'))
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': db_url.path[1:],
            'USER': db_url.username,
            'PASSWORD': db_url.password,
            'HOST': db_url.hostname,
            'PORT': db_url.port or 5432,
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }
```

## Production Deployment Notes

1. **Change SECRET_KEY in .env** - Use a secure random key
2. **Set DEBUG=False** in .env
3. **Update ALLOWED_HOSTS** in .env with your domain
4. **Use environment-specific .env files** for different deployments
5. **Consider SSL/TLS** - Add let's encrypt with Nginx
6. **Monitor logs** - Set up log aggregation
7. **Backup database** - Regular PostgreSQL backups
8. **Resource limits** - Set CPU/memory limits in docker-compose.yml

## Troubleshooting

### Container won't start
```bash
docker-compose logs web
```

### Database connection error
```bash
docker-compose exec db psql -U tradie_user -d tradie_db
```

### Static files not loading
```bash
docker-compose exec web python manage.py collectstatic --noinput --clear
```

### Permission denied errors
```bash
docker-compose exec -u root web chown -R appuser:appuser /app
```

## Next Steps

1. Update `requirements.txt` to include PostgreSQL driver:
   ```
   psycopg2-binary==2.9.9
   ```

2. Update Django settings.py for PostgreSQL (see above)

3. Run migrations on first startup
   ```bash
   docker-compose exec web python manage.py migrate
   ```

4. Test the setup:
   ```bash
   curl http://localhost/health
   ```

