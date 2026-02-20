FROM apache/superset:4.1.1

USER root

# Superset needs a PostgreSQL DBAPI driver for the analytics database connection.
RUN pip install --no-cache-dir psycopg2-binary==2.9.11

COPY app/dashboard/superset/bootstrap_superset.py /app/bootstrap_superset.py
COPY app/dashboard/superset/init_superset.sh /app/init_superset.sh
RUN chmod +x /app/init_superset.sh && chown superset:superset /app/bootstrap_superset.py /app/init_superset.sh

USER superset
