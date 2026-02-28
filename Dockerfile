FROM postgres:15

# Copy initialization scripts
COPY init_db.sql /docker-entrypoint-initdb.d/
# COPY init_extensions.sql /docker-entrypoint-initdb.d/

# (Optional) Copy custom postgres.conf
# COPY postgres.conf /etc/postgresql/postgresql.conf

# Expose Postgres port
EXPOSE 5432
