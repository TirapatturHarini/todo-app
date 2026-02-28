-- create database tododb;
-- Create user if not exists
DO
$$
BEGIN
   IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'todouser') THEN
      CREATE USER todouser WITH PASSWORD 'todopass';
   END IF;
END
$$;

-- Create database if not exists
DO
$$
BEGIN
   IF NOT EXISTS (SELECT FROM pg_database WHERE datname = 'tododb') THEN
      CREATE DATABASE tododb OWNER todouser;
   END IF;
END
$$;

-- Connect to tododb
\connect tododb;

-- Make sure schema is owned by todouser
ALTER SCHEMA public OWNER TO todouser;

-- Grant full privileges on schema
GRANT ALL PRIVILEGES ON SCHEMA public TO todouser;
GRANT CREATE, USAGE ON SCHEMA public TO todouser;

-- Ensure default privileges for future tables/sequences
ALTER DEFAULT PRIVILEGES IN SCHEMA public
   GRANT ALL ON TABLES TO todouser;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
   GRANT ALL ON SEQUENCES TO todouser;

-- Create todos table if not exists
CREATE TABLE IF NOT EXISTS todos (
    id SERIAL PRIMARY KEY,
    title VARCHAR(200) NOT NULL,
    description VARCHAR(1000),
    completed BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
